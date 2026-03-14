from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from sounio_stroke_lab.benchmark import BenchmarkHarness
from sounio_stroke_lab.config import get_kubernetes_settings
from sounio_stroke_lab.schemas import (
    AgentRun,
    AgentRunStatus,
    BenchmarkRequest,
    BenchmarkRun,
    JobKind,
    JobRecord,
    JobStatus,
    RecordOwnerType,
    WorkerHeartbeat,
    utc_now,
)
from sounio_stroke_lab.storage import StorageManager

try:  # pragma: no cover - optional dependency exercised in real cluster environments
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
    from kubernetes.client.exceptions import ApiException as K8sApiException
except ImportError:  # pragma: no cover - optional dependency path
    k8s_client = None
    k8s_config = None
    K8sApiException = Exception


class UnsupportedJobBackend(RuntimeError):
    pass


@dataclass(frozen=True)
class JobBackendDescriptor:
    name: str
    execution_mode: str
    supports_cluster: bool
    notes: str = ""


class BaseJobBackend:
    descriptor = JobBackendDescriptor(
        name="base",
        execution_mode="unknown",
        supports_cluster=False,
    )

    def __init__(self, storage: StorageManager):
        self.storage = storage

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def submit_benchmark(self, request: BenchmarkRequest) -> JobRecord:
        raise NotImplementedError

    def submit_agent_run(self, run: AgentRun) -> JobRecord:
        raise UnsupportedJobBackend(f"{self.descriptor.name} does not support agent-run job submission.")

    def get_job(self, job_id: str) -> JobRecord:
        return self.storage.get_job(job_id)

    def cancel_job(self, job_id: str) -> JobRecord:
        raise NotImplementedError

    def wait_for_job(self, job_id: str, timeout_seconds: float = 120.0, poll_interval: float = 0.05) -> JobRecord:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            job = self.get_job(job_id)
            if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
                return job
            time.sleep(poll_interval)
        raise TimeoutError(f"Job {job_id} did not reach a terminal state within {timeout_seconds} seconds.")

    def run_benchmark(self, request: BenchmarkRequest) -> BenchmarkRun:
        submitted = self.submit_benchmark(request)
        job = self.wait_for_job(submitted.job_id)
        if job.status == JobStatus.failed:
            raise RuntimeError(job.error or f"Benchmark job {job.job_id} failed.")
        if job.status == JobStatus.cancelled:
            raise RuntimeError(job.error or f"Benchmark job {job.job_id} was cancelled.")
        benchmark_run_id = job.result_payload.get("benchmark_run_id")
        if not benchmark_run_id:
            raise RuntimeError(f"Benchmark job {job.job_id} completed without a benchmark_run_id.")
        return self.storage.get_benchmark_run(str(benchmark_run_id))

    def render_job_manifest(self, job_id: str) -> dict:
        raise UnsupportedJobBackend(f"{self.descriptor.name} does not expose a renderable job manifest.")


def _obj_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _job_condition_message(status_obj: Any) -> str:
    conditions = _obj_get(status_obj, "conditions", []) or []
    for condition in conditions:
        if str(_obj_get(condition, "status", "")).lower() == "true":
            message = _obj_get(condition, "message") or _obj_get(condition, "reason")
            if message:
                return str(message)
    return ""


class BenchmarkWorker:
    def __init__(
        self,
        storage: StorageManager,
        benchmark: BenchmarkHarness,
        *,
        poll_interval: float = 0.1,
        heartbeat_interval: float = 2.0,
        backend_name: str = "local",
    ):
        self.storage = storage
        self.benchmark = benchmark
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.backend_name = backend_name
        self.worker_id = f"benchmark-worker-{uuid.uuid4().hex[:12]}"
        self.hostname = socket.gethostname()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_heartbeat = 0.0

    def start_in_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._heartbeat(force=True)
        self._thread = threading.Thread(target=self.run_forever, name="darwin-benchmark-worker", daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def run_forever(self) -> None:
        self.storage.requeue_inflight_jobs(kind=JobKind.benchmark.value)
        while not self._stop_event.is_set():
            self._heartbeat(force=False)
            processed = self.run_once()
            if not processed:
                self._stop_event.wait(self.poll_interval)

    def run_once(self) -> bool:
        job = self.storage.claim_next_job(kind=JobKind.benchmark.value)
        if job is None:
            return False
        self._execute_job(job)
        return True

    def run_job(self, job_id: str) -> bool:
        self._heartbeat(force=True)
        job = self.storage.get_job(job_id)
        if job.kind != JobKind.benchmark:
            raise ValueError(f"Job {job_id} is not a benchmark job.")
        if job.status == JobStatus.queued:
            job = self.storage.update_job(job_id, status=JobStatus.running)
        elif job.status != JobStatus.running:
            raise ValueError(f"Job {job_id} is in status {job.status} and cannot be executed by the benchmark worker.")
        self._execute_job(job)
        return True

    def _execute_job(self, job: JobRecord) -> None:
        self._heartbeat(force=True)
        try:
            request = BenchmarkRequest.model_validate(job.request_payload)
            run = self.benchmark.run_suite(request, run_id=job.owner_id, job_id=job.job_id)
            payload = dict(job.result_payload)
            backend_name = str(payload.get("backend") or self.backend_name)
            payload.update(
                {
                    "benchmark_run_id": run.run_id,
                    "artifact_count": len(run.artifacts),
                    "artifact_prefix": f"{run.run_id}/",
                    "dataset_version": run.dataset_version,
                    "backend": backend_name,
                    "cancel_requested": bool(job.result_payload.get("cancel_requested", False)),
                }
            )
            self.storage.update_job(
                job.job_id,
                status=JobStatus.completed,
                result_payload=payload,
            )
        except Exception as exc:
            payload = dict(job.result_payload)
            payload.setdefault("backend", self.backend_name)
            payload.setdefault("artifact_prefix", f"{job.owner_id}/")
            payload.setdefault("cancel_requested", bool(job.result_payload.get("cancel_requested", False)))
            self.storage.update_job(
                job.job_id,
                status=JobStatus.failed,
                result_payload=payload,
                error=str(exc),
            )

    def _heartbeat(self, *, force: bool) -> None:
        now = time.time()
        if not force and (now - self._last_heartbeat) < self.heartbeat_interval:
            return
        heartbeat = WorkerHeartbeat(
            worker_id=self.worker_id,
            worker_kind=JobKind.benchmark.value,
            backend=self.backend_name,
            executor="benchmark-worker",
            hostname=self.hostname,
            updated_at=utc_now(),
            notes="Local benchmark worker heartbeat.",
        )
        self.storage.save_worker_heartbeat(heartbeat)
        self._last_heartbeat = now


class LocalJobBackend(BaseJobBackend):
    descriptor = JobBackendDescriptor(
        name="local",
        execution_mode="queued-worker",
        supports_cluster=False,
        notes="Submits local benchmark jobs for a separate worker loop to execute.",
    )

    def __init__(self, storage: StorageManager, *, worker_ttl_seconds: float = 15.0):
        super().__init__(storage)
        self.worker_ttl_seconds = worker_ttl_seconds

    def submit_benchmark(self, request: BenchmarkRequest) -> JobRecord:
        run_id = uuid.uuid4().hex
        job = JobRecord(
            job_id=uuid.uuid4().hex,
            owner_type=RecordOwnerType.benchmark_run,
            owner_id=run_id,
            kind=JobKind.benchmark,
            status=JobStatus.queued,
            queue_name="local-benchmark",
            executor="benchmark-worker",
            request_payload=request.model_dump(mode="json"),
            result_payload={
                "backend": self.descriptor.name,
                "artifact_prefix": f"{run_id}/",
                "cancel_requested": False,
            },
        )
        self.storage.save_job(job)
        return job

    def run_benchmark(self, request: BenchmarkRequest) -> BenchmarkRun:
        self._ensure_worker_available()
        return super().run_benchmark(request)

    def cancel_job(self, job_id: str) -> JobRecord:
        job = self.storage.get_job(job_id)
        if job.status == JobStatus.queued:
            payload = dict(job.result_payload)
            payload["cancel_requested"] = True
            return self.storage.update_job(
                job_id,
                status=JobStatus.cancelled,
                result_payload=payload,
                error="Cancellation requested before local execution started.",
            )
        if job.status == JobStatus.running:
            payload = dict(job.result_payload)
            payload["cancel_requested"] = True
            return self.storage.update_job(
                job_id,
                result_payload=payload,
                error="Cancellation requested after local execution started; this slice does not preempt active runs.",
            )
        return job

    def _ensure_worker_available(self) -> None:
        threshold = utc_now() - timedelta(seconds=self.worker_ttl_seconds)
        live_workers = [
            item
            for item in self.storage.list_worker_heartbeats(worker_kind=JobKind.benchmark.value)
            if item.updated_at >= threshold
        ]
        if live_workers:
            return
        raise RuntimeError(
            "No active benchmark worker heartbeat was found. Start `sounio-stroke-lab run-benchmark-worker` "
            "or set SOUNIO_STROKE_START_EMBEDDED_BENCHMARK_WORKER=1 for local development."
        )


class KubernetesJobBackend(BaseJobBackend):
    descriptor = JobBackendDescriptor(
        name="kubernetes",
        execution_mode="queued-cluster",
        supports_cluster=True,
        notes="Submits one Kubernetes Job per benchmark run and reconciles status back into the local job contract.",
    )

    def __init__(
        self,
        storage: StorageManager,
        *,
        batch_api: Any | None = None,
        custom_objects_api: Any | None = None,
        config_loader: Callable[[], None] | None = None,
    ):
        super().__init__(storage)
        self.settings = get_kubernetes_settings()
        self._batch_api = batch_api
        self._custom_objects_api = custom_objects_api
        self._config_loader = config_loader or self._default_config_loader

    def _default_config_loader(self) -> None:
        if k8s_config is None:  # pragma: no cover - dependency availability
            raise RuntimeError("Kubernetes job backend requested but the 'kubernetes' package is not installed.")
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()

    def _get_batch_api(self):
        if self._batch_api is not None:
            return self._batch_api
        if k8s_client is None:  # pragma: no cover - dependency availability
            raise RuntimeError("Kubernetes job backend requested but the 'kubernetes' package is not installed.")
        self._config_loader()
        self._batch_api = k8s_client.BatchV1Api()
        return self._batch_api

    def _get_custom_objects_api(self):
        if self._custom_objects_api is not None:
            return self._custom_objects_api
        if k8s_client is None:  # pragma: no cover - dependency availability
            raise RuntimeError("Kubernetes job backend requested but the 'kubernetes' package is not installed.")
        self._config_loader()
        self._custom_objects_api = k8s_client.CustomObjectsApi()
        return self._custom_objects_api

    def submit_benchmark(self, request: BenchmarkRequest) -> JobRecord:
        run_id = uuid.uuid4().hex
        job = JobRecord(
            job_id=uuid.uuid4().hex,
            owner_type=RecordOwnerType.benchmark_run,
            owner_id=run_id,
            kind=JobKind.benchmark,
            status=JobStatus.queued,
            queue_name=f"kubernetes:{self.settings.namespace}/{self.settings.local_queue}",
            executor="kubernetes-job",
            request_payload=request.model_dump(mode="json"),
            result_payload={
                "backend": self.descriptor.name,
                "artifact_prefix": f"{run_id}/",
                "cancel_requested": False,
            },
        )
        self.storage.save_job(job)
        manifest = self.render_job_manifest(job.job_id)
        try:
            response = self._get_batch_api().create_namespaced_job(namespace=self.settings.namespace, body=manifest)
        except Exception as exc:
            self.storage.update_job(
                job.job_id,
                status=JobStatus.failed,
                result_payload={
                    **job.result_payload,
                    "backend": self.descriptor.name,
                    "artifact_prefix": f"{run_id}/",
                    "cancel_requested": False,
                },
                error=f"Kubernetes submission failed: {exc}",
            )
            raise RuntimeError(f"Kubernetes submission failed for benchmark job {job.job_id}: {exc}") from exc
        return self.storage.update_job(
            job.job_id,
            result_payload={
                **job.result_payload,
                "backend": self.descriptor.name,
                "artifact_prefix": f"{run_id}/",
                "cancel_requested": False,
                "k8s_namespace": self.settings.namespace,
                "k8s_job_name": manifest["metadata"]["name"],
                "k8s_job_uid": str(_obj_get(_obj_get(response, "metadata", {}), "uid", "")),
                "kueue_local_queue": self.settings.local_queue,
            },
        )

    def submit_agent_run(self, run: AgentRun) -> JobRecord:
        job = JobRecord(
            job_id=uuid.uuid4().hex,
            owner_type=RecordOwnerType.agent_run,
            owner_id=run.run_id,
            kind=JobKind.agent_run,
            status=JobStatus.queued,
            queue_name=f"kubernetes:{self.settings.namespace}/{self.settings.local_queue}",
            executor="kubernetes-job",
            request_payload={"run_id": run.run_id, "surface": run.surface},
            result_payload={
                "backend": self.descriptor.name,
                "artifact_prefix": f"{run.run_id}/",
                "cancel_requested": False,
                "agent_run_id": run.run_id,
            },
        )
        self.storage.save_job(job)
        manifest = self.render_job_manifest(job.job_id)
        try:
            response = self._get_batch_api().create_namespaced_job(namespace=self.settings.namespace, body=manifest)
        except Exception as exc:
            self.storage.update_job(
                job.job_id,
                status=JobStatus.failed,
                result_payload=dict(job.result_payload),
                error=f"Kubernetes submission failed: {exc}",
            )
            raise RuntimeError(f"Kubernetes submission failed for agent job {job.job_id}: {exc}") from exc
        return self.storage.update_job(
            job.job_id,
            result_payload={
                **job.result_payload,
                "k8s_namespace": self.settings.namespace,
                "k8s_job_name": manifest["metadata"]["name"],
                "k8s_job_uid": str(_obj_get(_obj_get(response, "metadata", {}), "uid", "")),
                "kueue_local_queue": self.settings.local_queue,
            },
        )

    def get_job(self, job_id: str) -> JobRecord:
        job = self.storage.get_job(job_id)
        return self._sync_job(job)

    def run_benchmark(self, request: BenchmarkRequest) -> BenchmarkRun:
        submitted = self.submit_benchmark(request)
        deadline = time.time() + 600.0
        while time.time() < deadline:
            job = self.get_job(submitted.job_id)
            if job.status == JobStatus.failed:
                raise RuntimeError(job.error or f"Benchmark job {job.job_id} failed.")
            if job.status == JobStatus.cancelled:
                raise RuntimeError(job.error or f"Benchmark job {job.job_id} was cancelled.")
            benchmark_run_id = job.result_payload.get("benchmark_run_id")
            if job.status == JobStatus.completed and benchmark_run_id:
                return self.storage.get_benchmark_run(str(benchmark_run_id))
            time.sleep(0.5)
        raise TimeoutError(f"Kubernetes benchmark job {submitted.job_id} did not persist a benchmark_run_id in time.")

    def cancel_job(self, job_id: str) -> JobRecord:
        job = self.storage.get_job(job_id)
        payload = dict(job.result_payload)
        payload["cancel_requested"] = True
        namespace = str(payload.get("k8s_namespace") or self.settings.namespace)
        k8s_job_name = str(payload.get("k8s_job_name") or "")
        if k8s_job_name:
            try:
                self._get_batch_api().delete_namespaced_job(
                    name=k8s_job_name,
                    namespace=namespace,
                    propagation_policy="Background",
                )
            except Exception as exc:
                if not self._is_not_found(exc):
                    raise RuntimeError(f"Failed to cancel Kubernetes job {k8s_job_name}: {exc}") from exc
        return self.storage.update_job(
            job_id,
            status=JobStatus.cancelled,
            result_payload=payload,
            error="Cancellation requested for the Kubernetes benchmark job.",
        )

    def render_job_manifest(self, job_id: str) -> dict:
        job = self.storage.get_job(job_id)
        minio_endpoint = os.getenv("SOUNIO_STROKE_MINIO_ENDPOINT", "")
        service_account_name = self.settings.service_account_name
        args: list[str]
        labels = {
            "app.kubernetes.io/name": "sounio-stroke-lab",
            "darwin.run/job-id": job.job_id,
        }
        darwin_payload: dict[str, Any] = {
            "job_id": job.job_id,
            "owner_id": job.owner_id,
            "backend": self.descriptor.name,
            "artifact_prefix": job.result_payload.get("artifact_prefix", f"{job.owner_id}/"),
        }
        if job.kind == JobKind.benchmark:
            request = BenchmarkRequest.model_validate(job.request_payload)
            manifest_name = f"sounio-benchmark-{job.job_id[:12]}"
            args = [
                "run-benchmark-worker",
                "--job-id",
                job.job_id,
            ]
            labels["app.kubernetes.io/component"] = "benchmark-worker"
            darwin_payload.update(
                {
                    "dataset_manifest_path": request.dataset_manifest_path,
                    "external_test_manifest_path": request.external_test_manifest_path,
                    "train_split": request.train_split,
                    "test_split": request.test_split,
                    "seed": request.seed,
                }
            )
        elif job.kind == JobKind.agent_run:
            manifest_name = f"sounio-agent-{job.job_id[:12]}"
            args = [
                "run-agent-worker",
                "--run-id",
                job.owner_id,
                "--job-id",
                job.job_id,
            ]
            labels["app.kubernetes.io/component"] = "agent-runner"
            darwin_payload.update({"agent_run_id": job.owner_id})
        else:
            raise ValueError(f"Unsupported Kubernetes job kind for manifest rendering: {job.kind}")

        pod_spec: dict[str, Any] = {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": labels["app.kubernetes.io/component"],
                    "image": self.settings.worker_image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sounio-stroke-lab"],
                    "args": args,
                    "env": [
                        {"name": "SOUNIO_STROKE_JOB_BACKEND", "value": "kubernetes"},
                        {"name": "SOUNIO_STROKE_AGENT_BACKEND", "value": "kubernetes"},
                        {"name": "SOUNIO_STROKE_K8S_NAMESPACE", "value": self.settings.namespace},
                        {"name": "SOUNIO_STROKE_K8S_LOCAL_QUEUE", "value": self.settings.local_queue},
                        {"name": "SOUNIO_STROKE_K8S_WORKER_IMAGE", "value": self.settings.worker_image},
                        {"name": "SOUNIO_STROKE_K8S_DATASET_PVC", "value": self.settings.dataset_pvc},
                        {"name": "SOUNIO_STROKE_K8S_SERVICE_ACCOUNT", "value": self.settings.service_account_name},
                        {
                            "name": "SOUNIO_STROKE_DB_URL",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": self.settings.db_secret_name,
                                    "key": self.settings.db_secret_key,
                                }
                            },
                        },
                        {"name": "SOUNIO_STROKE_OBJECT_STORE", "value": os.getenv("SOUNIO_STROKE_OBJECT_STORE", "filesystem")},
                        {"name": "SOUNIO_STROKE_MINIO_ENDPOINT", "value": minio_endpoint},
                        {
                            "name": "SOUNIO_STROKE_MINIO_ACCESS_KEY",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": self.settings.minio_secret_name,
                                    "key": self.settings.minio_access_key_key,
                                }
                            },
                        },
                        {
                            "name": "SOUNIO_STROKE_MINIO_SECRET_KEY",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": self.settings.minio_secret_name,
                                    "key": self.settings.minio_secret_key_key,
                                }
                            },
                        },
                        {"name": "SOUNIO_STROKE_MINIO_BUCKET", "value": os.getenv("SOUNIO_STROKE_MINIO_BUCKET", "sounio-stroke-lab")},
                    ],
                    "resources": {
                        "requests": {
                            "cpu": self.settings.cpu_request,
                            "memory": self.settings.memory_request,
                        },
                        "limits": {
                            "cpu": self.settings.cpu_limit,
                            "memory": self.settings.memory_limit,
                        },
                    },
                    "volumeMounts": [
                        {"name": "storage-cache", "mountPath": "/var/lib/sounio-stroke-lab"},
                        {"name": "datasets", "mountPath": "/datasets", "readOnly": True},
                    ],
                }
            ],
            "volumes": [
                {"name": "storage-cache", "emptyDir": {}},
                {"name": "datasets", "persistentVolumeClaim": {"claimName": self.settings.dataset_pvc}},
            ],
        }
        if service_account_name:
            pod_spec["serviceAccountName"] = service_account_name
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": manifest_name,
                "namespace": self.settings.namespace,
                "labels": {
                    **labels,
                    "darwin.run/job-id": job.job_id,
                    "darwin.run/run-id": job.owner_id,
                    "kueue.x-k8s.io/queue-name": self.settings.local_queue,
                },
            },
            "spec": {
                "backoffLimit": 0,
                "template": {
                    "metadata": {
                        "labels": {**labels}
                    },
                    "spec": pod_spec,
                },
            },
            "darwin": darwin_payload,
        }

    def _sync_job(self, job: JobRecord) -> JobRecord:
        payload = dict(job.result_payload)
        k8s_job_name = str(payload.get("k8s_job_name") or "")
        if not k8s_job_name:
            return job
        namespace = str(payload.get("k8s_namespace") or self.settings.namespace)
        try:
            cluster_job = self._get_batch_api().read_namespaced_job(name=k8s_job_name, namespace=namespace)
        except Exception as exc:
            if self._is_not_found(exc):
                return job
            raise RuntimeError(f"Failed to poll Kubernetes job {k8s_job_name}: {exc}") from exc
        payload["k8s_job_uid"] = str(_obj_get(_obj_get(cluster_job, "metadata", {}), "uid", payload.get("k8s_job_uid", "")))
        payload = self._sync_workload(namespace, payload)
        status_obj = _obj_get(cluster_job, "status", None)
        if status_obj is None:
            return job
        if _obj_get(status_obj, "failed", 0):
            return self.storage.update_job(
                job.job_id,
                status=JobStatus.failed,
                result_payload=payload,
                error=_job_condition_message(status_obj) or f"Kubernetes job {k8s_job_name} failed.",
            )
        if payload.get("cancel_requested") and job.status == JobStatus.cancelled:
            return job
        if _obj_get(status_obj, "active", 0):
            if job.status != JobStatus.running:
                return self.storage.update_job(job.job_id, status=JobStatus.running, result_payload=payload)
            return job
        if _obj_get(status_obj, "succeeded", 0):
            if job.kind == JobKind.agent_run:
                run = self.storage.get_agent_run(job.owner_id)
                status_map = {
                    AgentRunStatus.completed: JobStatus.completed,
                    AgentRunStatus.failed: JobStatus.failed,
                    AgentRunStatus.cancelled: JobStatus.cancelled,
                }
                mirrored_status = status_map.get(run.status)
                if mirrored_status is not None:
                    payload["agent_run_status"] = run.status
                    payload["final_output_present"] = bool(run.final_output)
                    return self.storage.update_job(job.job_id, status=mirrored_status, result_payload=payload, error=run.error)
                payload["cluster_phase"] = "succeeded_pending_agent_persistence"
                return self.storage.update_job(job.job_id, status=JobStatus.running, result_payload=payload)
            if payload.get("benchmark_run_id"):
                return self.storage.update_job(job.job_id, status=JobStatus.completed, result_payload=payload)
            payload["cluster_phase"] = "succeeded_pending_persistence"
            return self.storage.update_job(job.job_id, status=JobStatus.running, result_payload=payload)
        return job

    def _sync_workload(self, namespace: str, payload: dict[str, Any]) -> dict[str, Any]:
        job_uid = str(payload.get("k8s_job_uid") or "").strip()
        if not job_uid:
            return payload
        try:
            response = self._get_custom_objects_api().list_namespaced_custom_object(
                group="kueue.x-k8s.io",
                version="v1beta2",
                namespace=namespace,
                plural="workloads",
                label_selector=f"kueue.x-k8s.io/job-uid={job_uid}",
            )
        except Exception:
            return payload
        items = _obj_get(response, "items", []) or []
        if not items:
            return payload
        workload = items[0]
        status_obj = _obj_get(workload, "status", {}) or {}
        admission = _obj_get(status_obj, "admission", {}) or {}
        conditions = _obj_get(status_obj, "conditions", []) or []
        condition_map: dict[str, dict[str, Any]] = {}
        for item in conditions:
            condition_type = str(_obj_get(item, "type", "")).strip()
            if condition_type:
                condition_map[condition_type] = {
                    "status": str(_obj_get(item, "status", "")),
                    "reason": str(_obj_get(item, "reason", "")),
                    "message": str(_obj_get(item, "message", "")),
                    "last_transition_time": str(_obj_get(item, "lastTransitionTime", "")),
                }
        payload.update(
            {
                "kueue_workload_name": str(_obj_get(_obj_get(workload, "metadata", {}), "name", "")),
                "kueue_cluster_queue": str(_obj_get(admission, "clusterQueue", "")),
                "kueue_admitted": condition_map.get("Admitted", {}).get("status") == "True",
                "kueue_finished": condition_map.get("Finished", {}).get("status") == "True",
                "kueue_conditions": condition_map,
            }
        )
        if payload["kueue_finished"]:
            payload["cluster_phase"] = "finished"
            return payload
        if payload["kueue_admitted"] and payload.get("cluster_phase") in {None, "", "queued"}:
            payload["cluster_phase"] = "admitted"
        return payload

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        status_code = getattr(exc, "status", None)
        return status_code == 404 or "NotFound" in exc.__class__.__name__


def build_job_backend(storage: StorageManager, benchmark: BenchmarkHarness | None = None) -> BaseJobBackend:
    backend_name = os.getenv("SOUNIO_STROKE_JOB_BACKEND", "local").strip().lower()
    if backend_name == "local":
        return LocalJobBackend(storage)
    if backend_name in {"k8s", "kubernetes", "cluster"}:
        return KubernetesJobBackend(storage)
    raise UnsupportedJobBackend(f"Unsupported job backend: {backend_name}")
