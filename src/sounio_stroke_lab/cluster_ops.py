from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


KubectlRunner = Callable[[list[str]], tuple[int, str, str]]


@dataclass(frozen=True)
class ClusterCheck:
    name: str
    ok: bool
    details: str
    value: str | None = None


@dataclass(frozen=True)
class ClusterPreflightResult:
    namespace: str
    pvc_name: str
    target_storage_class: str
    overlay: str
    current_storage_class: str | None
    available_storage_classes: tuple[str, ...]
    checks: tuple[ClusterCheck, ...]
    warnings: tuple[str, ...]

    @property
    def ready_for_cutover(self) -> bool:
        required_failures = {
            "kubectl",
            "namespace",
            "target_storage_class",
            "local_queue",
            "cluster_queue",
            "service_account",
            "secret:sounio-stroke-db",
            "secret:sounio-stroke-minio",
            "secret:sounio-stroke-api",
        }
        failures = {item.name for item in self.checks if not item.ok}
        return not bool(required_failures & failures)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["ready_for_cutover"] = self.ready_for_cutover
        return payload


def default_kubectl_runner(args: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def resolve_overlay(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized == "cephfs":
        return "deploy/k8s/overlays/cephfs"
    if normalized in {"ceph-rbd", "rbd"}:
        return "deploy/k8s/overlays/ceph-rbd"
    raise ValueError(f"Unsupported overlay mode: {mode}")


def run_cluster_preflight(
    *,
    namespace: str,
    pvc_name: str,
    target_storage_class: str,
    overlay_mode: str,
    local_queue: str = "darwin-lab",
    cluster_queue: str = "darwin-shared",
    service_account: str = "sounio-stroke-runner",
    secret_names: tuple[str, ...] = ("sounio-stroke-db", "sounio-stroke-minio", "sounio-stroke-api"),
    runner: KubectlRunner | None = None,
) -> ClusterPreflightResult:
    kubectl = runner or default_kubectl_runner
    checks: list[ClusterCheck] = []
    warnings: list[str] = []
    overlay = resolve_overlay(overlay_mode)
    available_storage_classes = _list_storage_classes(kubectl)

    checks.append(_run_simple_check("kubectl", ["version", "--client"], kubectl))
    namespace_check = _run_simple_check("namespace", ["get", "namespace", namespace, "-o", "json"], kubectl)
    checks.append(namespace_check)

    pvc_obj = _run_json(["get", "pvc", pvc_name, "-n", namespace, "-o", "json"], kubectl)
    if pvc_obj is None:
        checks.append(ClusterCheck(name="dataset_pvc", ok=False, details=f"PVC {namespace}/{pvc_name} not found."))
        current_storage_class = None
        warnings.append("Dataset PVC does not exist yet; cutover can proceed as a fresh create.")
    else:
        access_modes = ",".join(pvc_obj.get("spec", {}).get("accessModes", []))
        current_storage_class = pvc_obj.get("spec", {}).get("storageClassName")
        checks.append(
            ClusterCheck(
                name="dataset_pvc",
                ok=True,
                details=f"PVC {namespace}/{pvc_name} found with accessModes={access_modes or '<none>'}.",
                value=current_storage_class or "",
            )
        )
        if current_storage_class == target_storage_class:
            warnings.append(
                f"PVC {pvc_name} already points to storageClass {target_storage_class}; storage cutover may be unnecessary."
            )

    checks.append(
        _run_simple_check(
            "target_storage_class",
            ["get", "storageclass", target_storage_class, "-o", "json"],
            kubectl,
            success_details=f"StorageClass {target_storage_class} is present.",
        )
    )
    if target_storage_class not in available_storage_classes:
        known = ", ".join(available_storage_classes) if available_storage_classes else "<none>"
        warnings.append(
            f"Target StorageClass {target_storage_class} is not installed in this cluster. Available classes: {known}."
        )
    checks.append(
        _run_simple_check(
            "local_queue",
            ["get", "localqueue", local_queue, "-n", namespace, "-o", "json"],
            kubectl,
            success_details=f"LocalQueue {namespace}/{local_queue} is present.",
        )
    )
    checks.append(
        _run_simple_check(
            "cluster_queue",
            ["get", "clusterqueue", cluster_queue, "-o", "json"],
            kubectl,
            success_details=f"ClusterQueue {cluster_queue} is present.",
        )
    )
    checks.append(
        _run_simple_check(
            "service_account",
            ["get", "serviceaccount", service_account, "-n", namespace, "-o", "json"],
            kubectl,
            success_details=f"ServiceAccount {namespace}/{service_account} is present.",
        )
    )
    for secret_name in secret_names:
        checks.append(
            _run_simple_check(
                f"secret:{secret_name}",
                ["get", "secret", secret_name, "-n", namespace, "-o", "json"],
                kubectl,
                success_details=f"Secret {namespace}/{secret_name} is present.",
            )
        )
    checks.append(
        _run_simple_check(
            "api_deployment",
            ["get", "deployment", "sounio-stroke-api", "-n", namespace, "-o", "json"],
            kubectl,
            success_details=f"Deployment {namespace}/sounio-stroke-api is present.",
        )
    )
    checks.append(
        _run_simple_check(
            "api_service",
            ["get", "service", "sounio-stroke-api", "-n", namespace, "-o", "json"],
            kubectl,
            success_details=f"Service {namespace}/sounio-stroke-api is present.",
        )
    )

    return ClusterPreflightResult(
        namespace=namespace,
        pvc_name=pvc_name,
        target_storage_class=target_storage_class,
        overlay=overlay,
        current_storage_class=current_storage_class,
        available_storage_classes=available_storage_classes,
        checks=tuple(checks),
        warnings=tuple(warnings),
    )


def build_cutover_plan(
    preflight: ClusterPreflightResult,
    *,
    preserve_existing_data: bool = True,
    source_pvc_name: str | None = None,
    target_pvc_name: str | None = None,
) -> list[str]:
    if not preflight.ready_for_cutover:
        return [
            "Run the preflight first and fix the failing required checks before attempting storage migration.",
            "Do not recreate the dataset PVC until namespace, queues, service account, and secrets are healthy.",
        ]

    source_name = source_pvc_name or preflight.pvc_name
    target_name = target_pvc_name or f"{preflight.pvc_name}-ceph"
    overlay = preflight.overlay
    storage_class = preflight.target_storage_class

    steps = [
        "Pause new submissions by scaling the API deployment to zero or removing the ingress entry point.",
        f"Wait for benchmark and agent jobs in namespace {preflight.namespace} to finish draining.",
        "Capture a quick inventory of dataset manifests and mounted dataset paths before touching storage.",
    ]
    if preserve_existing_data:
        steps.extend(
            [
                f"Create a temporary target PVC such as {target_name} on storageClass {storage_class}.",
                (
                    "Render a copy job that mounts both PVCs and copies /datasets across before the cutover. "
                    "Use the new `render-dataset-copy-job` helper to generate the manifest."
                ),
                "Run the copy job and verify the target PVC contents before changing the canonical claim.",
            ]
        )
    else:
        steps.append("If the dataset contents are disposable, skip the copy step and plan to repopulate /datasets after the cutover.")
    steps.extend(
        [
            f"Apply the storage overlay from {overlay} to recreate the canonical PVC on {storage_class}.",
            f"If you used a temporary PVC, promote it or replay the dataset population into {preflight.pvc_name}.",
            "Scale the API deployment back up and submit one benchmark job plus one agent run as smoke tests.",
            "Keep the old PVC or a snapshot around until the first Kueue-backed run completes and artifacts land in MinIO.",
        ]
    )
    if preflight.current_storage_class == storage_class:
        steps.insert(
            0,
            f"The existing PVC already uses {storage_class}; review whether you need a migration at all before doing disruptive work.",
        )
    return steps


def render_dataset_copy_job_manifest(
    *,
    namespace: str,
    source_pvc: str,
    target_pvc: str,
    job_name: str = "sounio-stroke-dataset-copy",
    source_mount: str = "/from",
    target_mount: str = "/to",
    image: str = "alpine:3.21",
) -> dict:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "sounio-stroke-lab",
                "app.kubernetes.io/component": "dataset-copy",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/name": "sounio-stroke-lab",
                        "app.kubernetes.io/component": "dataset-copy",
                    }
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "copy",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": [
                                "sh",
                                "-lc",
                                f"set -eu; mkdir -p {target_mount}; cp -a {source_mount}/. {target_mount}/",
                            ],
                            "volumeMounts": [
                                {"name": "source", "mountPath": source_mount, "readOnly": True},
                                {"name": "target", "mountPath": target_mount},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "source", "persistentVolumeClaim": {"claimName": source_pvc}},
                        {"name": "target", "persistentVolumeClaim": {"claimName": target_pvc}},
                    ],
                },
            },
        },
    }


def _run_simple_check(
    name: str,
    args: list[str],
    runner: KubectlRunner,
    *,
    success_details: str | None = None,
) -> ClusterCheck:
    code, stdout, stderr = runner(args)
    if code == 0:
        return ClusterCheck(name=name, ok=True, details=success_details or "OK.")
    detail = (stderr or stdout).strip() or "kubectl returned a non-zero exit code."
    return ClusterCheck(name=name, ok=False, details=detail)


def _run_json(args: list[str], runner: KubectlRunner) -> dict | None:
    code, stdout, stderr = runner(args)
    if code != 0:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"kubectl returned invalid JSON for args={args!r}: {stdout[:200]!r}") from None


def _list_storage_classes(runner: KubectlRunner) -> tuple[str, ...]:
    response = _run_json(["get", "storageclass", "-o", "json"], runner)
    if response is None:
        return ()
    items = response.get("items", []) or []
    names = []
    for item in items:
        name = item.get("metadata", {}).get("name")
        if name:
            names.append(str(name))
    return tuple(sorted(names))
