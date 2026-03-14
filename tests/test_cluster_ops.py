from __future__ import annotations

import json

from sounio_stroke_lab.cluster_ops import (
    build_cutover_plan,
    render_dataset_copy_job_manifest,
    run_cluster_preflight,
)


def _fake_runner_factory(fixtures: dict[tuple[str, ...], tuple[int, str, str]]):
    def _runner(args: list[str]) -> tuple[int, str, str]:
        key = tuple(args)
        return fixtures.get(key, (1, "", "not found"))

    return _runner


def test_cluster_preflight_reports_ready_cutover() -> None:
    fixtures = {
        ("version", "--client"): (0, "kubectl", ""),
        ("get", "storageclass", "-o", "json"): (
            0,
            json.dumps({"items": [{"metadata": {"name": "local-path"}}, {"metadata": {"name": "rook-cephfs"}}]}),
            "",
        ),
        ("get", "namespace", "darwin-genomics", "-o", "json"): (0, json.dumps({"metadata": {"name": "darwin-genomics"}}), ""),
        (
            "get",
            "pvc",
            "sounio-stroke-datasets",
            "-n",
            "darwin-genomics",
            "-o",
            "json",
        ): (
            0,
            json.dumps({"spec": {"storageClassName": "local-path", "accessModes": ["ReadWriteOnce"]}}),
            "",
        ),
        ("get", "storageclass", "rook-cephfs", "-o", "json"): (0, "{}", ""),
        ("get", "localqueue", "darwin-lab", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "clusterqueue", "darwin-shared", "-o", "json"): (0, "{}", ""),
        ("get", "serviceaccount", "sounio-stroke-runner", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "secret", "sounio-stroke-db", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "secret", "sounio-stroke-minio", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "secret", "sounio-stroke-api", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "deployment", "sounio-stroke-api", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "service", "sounio-stroke-api", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
    }
    result = run_cluster_preflight(
        namespace="darwin-genomics",
        pvc_name="sounio-stroke-datasets",
        target_storage_class="rook-cephfs",
        overlay_mode="cephfs",
        runner=_fake_runner_factory(fixtures),
    )

    assert result.ready_for_cutover is True
    assert result.overlay == "deploy/k8s/overlays/cephfs"
    assert result.current_storage_class == "local-path"
    assert result.available_storage_classes == ("local-path", "rook-cephfs")
    assert any(item.name == "api_deployment" and item.ok for item in result.checks)


def test_cutover_plan_warns_when_not_ready() -> None:
    fixtures = {
        ("version", "--client"): (1, "", "kubectl missing"),
        ("get", "storageclass", "-o", "json"): (0, json.dumps({"items": []}), ""),
    }
    result = run_cluster_preflight(
        namespace="darwin-genomics",
        pvc_name="sounio-stroke-datasets",
        target_storage_class="rook-cephfs",
        overlay_mode="cephfs",
        runner=_fake_runner_factory(fixtures),
    )

    plan = build_cutover_plan(result)
    assert result.ready_for_cutover is False
    assert "Run the preflight first" in plan[0]


def test_cluster_preflight_warns_when_target_storage_class_is_missing() -> None:
    fixtures = {
        ("version", "--client"): (0, "kubectl", ""),
        ("get", "storageclass", "-o", "json"): (
            0,
            json.dumps({"items": [{"metadata": {"name": "local-path"}}]}),
            "",
        ),
        ("get", "namespace", "darwin-genomics", "-o", "json"): (0, json.dumps({"metadata": {"name": "darwin-genomics"}}), ""),
        (
            "get",
            "pvc",
            "sounio-stroke-datasets",
            "-n",
            "darwin-genomics",
            "-o",
            "json",
        ): (
            0,
            json.dumps({"spec": {"storageClassName": "local-path", "accessModes": ["ReadWriteOnce"]}}),
            "",
        ),
        ("get", "storageclass", "rook-cephfs", "-o", "json"): (1, "", "not found"),
        ("get", "localqueue", "darwin-lab", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "clusterqueue", "darwin-shared", "-o", "json"): (0, "{}", ""),
        ("get", "serviceaccount", "sounio-stroke-runner", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "secret", "sounio-stroke-db", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "secret", "sounio-stroke-minio", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "secret", "sounio-stroke-api", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "deployment", "sounio-stroke-api", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
        ("get", "service", "sounio-stroke-api", "-n", "darwin-genomics", "-o", "json"): (0, "{}", ""),
    }
    result = run_cluster_preflight(
        namespace="darwin-genomics",
        pvc_name="sounio-stroke-datasets",
        target_storage_class="rook-cephfs",
        overlay_mode="cephfs",
        runner=_fake_runner_factory(fixtures),
    )

    assert result.ready_for_cutover is False
    assert any("Available classes: local-path." in item for item in result.warnings)


def test_render_dataset_copy_job_manifest_mounts_both_pvcs() -> None:
    manifest = render_dataset_copy_job_manifest(
        namespace="darwin-genomics",
        source_pvc="source-pvc",
        target_pvc="target-pvc",
        job_name="copy-job",
    )

    assert manifest["metadata"]["name"] == "copy-job"
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert "cp -a /from/. /to/" in container["command"][-1]
    volumes = manifest["spec"]["template"]["spec"]["volumes"]
    assert volumes[0]["persistentVolumeClaim"]["claimName"] == "source-pvc"
    assert volumes[1]["persistentVolumeClaim"]["claimName"] == "target-pvc"
