from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from sounio_stroke_lab.benchmark import BenchmarkHarness
from sounio_stroke_lab.job_backend import BenchmarkWorker, KubernetesJobBackend, LocalJobBackend
from sounio_stroke_lab.object_store import MinioObjectStore
from sounio_stroke_lab.schemas import (
    AgentRun,
    AgentRunStatus,
    AgentSurface,
    BenchmarkRequest,
    JobKind,
    JobRecord,
    JobStatus,
    RecordOwnerType,
    SafetyPolicy,
)
from sounio_stroke_lab.state_store import PostgresStateStore
from sounio_stroke_lab.storage import StorageManager
from tests.support import create_benchmark_manifest


class _FakeMinioClient:
    def __init__(self):
        self.buckets: set[str] = set()
        self.uploads: list[tuple[str, str, str]] = []

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets.add(bucket)

    def fput_object(self, bucket: str, key: str, file_path: str, content_type: str | None = None):
        self.uploads.append((bucket, key, file_path))


class _FakeK8sStatus:
    def __init__(self, *, active: int = 0, succeeded: int = 0, failed: int = 0, conditions: list[dict] | None = None):
        self.active = active
        self.succeeded = succeeded
        self.failed = failed
        self.conditions = conditions or []


class _FakeK8sJob:
    def __init__(self, status: _FakeK8sStatus):
        self.status = status


class _FakeBatchApi:
    def __init__(self):
        self.created: list[tuple[str, dict]] = []
        self.deleted: list[tuple[str, str, str]] = []
        self.jobs: dict[tuple[str, str], _FakeK8sJob] = {}

    def create_namespaced_job(self, namespace: str, body: dict):
        name = body["metadata"]["name"]
        self.created.append((namespace, body))
        self.jobs[(namespace, name)] = _FakeK8sJob(_FakeK8sStatus(active=1))
        return {"metadata": {"name": name, "namespace": namespace, "uid": f"uid-{name}"}}

    def read_namespaced_job(self, name: str, namespace: str):
        return self.jobs[(namespace, name)]

    def delete_namespaced_job(self, name: str, namespace: str, propagation_policy: str = "Background"):
        self.deleted.append((namespace, name, propagation_policy))
        self.jobs.pop((namespace, name), None)
        return {}


class _FakeCustomObjectsApi:
    def __init__(self):
        self.workloads: dict[tuple[str, str], dict] = {}

    def list_namespaced_custom_object(self, group: str, version: str, namespace: str, plural: str, label_selector: str = ""):
        assert group == "kueue.x-k8s.io"
        assert version == "v1beta2"
        assert plural == "workloads"
        uid = label_selector.split("=", 1)[1] if "=" in label_selector else ""
        item = self.workloads.get((namespace, uid))
        return {"items": [item] if item else []}


def test_local_backend_queued_job_can_be_cancelled(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    backend = LocalJobBackend(storage)
    manifest_path = create_benchmark_manifest(tmp_path)

    job = backend.submit_benchmark(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=3))
    cancelled = backend.cancel_job(job.job_id)

    assert cancelled.status == JobStatus.cancelled
    assert cancelled.result_payload["cancel_requested"] is True


def test_benchmark_worker_executes_queued_job_and_persists_run(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    backend = LocalJobBackend(storage)
    manifest_path = create_benchmark_manifest(tmp_path)

    job = backend.submit_benchmark(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=11))
    worker = BenchmarkWorker(storage, harness)
    processed = worker.run_once()

    assert processed is True
    terminal = storage.get_job(job.job_id)
    assert terminal.status == JobStatus.completed
    run = storage.get_benchmark_run(terminal.result_payload["benchmark_run_id"])
    assert run.job_id == job.job_id


def test_benchmark_worker_preserves_backend_name_from_job_payload(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    manifest_path = create_benchmark_manifest(tmp_path)
    job = JobRecord(
        job_id=uuid.uuid4().hex,
        owner_type=RecordOwnerType.benchmark_run,
        owner_id=uuid.uuid4().hex,
        kind=JobKind.benchmark,
        status=JobStatus.running,
        queue_name="kubernetes:darwin-genomics/darwin-lab",
        executor="kubernetes-job",
        request_payload=BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=19).model_dump(mode="json"),
        result_payload={"backend": "kubernetes", "artifact_prefix": "example/"},
    )
    storage.save_job(job)

    worker = BenchmarkWorker(storage, harness, backend_name="kubernetes")
    worker.run_job(job.job_id)

    terminal = storage.get_job(job.job_id)
    assert terminal.status == JobStatus.completed
    assert terminal.result_payload["backend"] == "kubernetes"


def test_benchmark_worker_requeues_running_job_after_restart(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    backend = LocalJobBackend(storage)
    manifest_path = create_benchmark_manifest(tmp_path)

    job = backend.submit_benchmark(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=17))
    claimed = storage.claim_next_job(kind=JobKind.benchmark.value)
    assert claimed is not None
    assert claimed.status == JobStatus.running

    worker = BenchmarkWorker(storage, harness, poll_interval=0.05)
    worker.start_in_background()
    try:
        deadline = time.time() + 30.0
        while time.time() < deadline:
            terminal = storage.get_job(job.job_id)
            if terminal.status == JobStatus.completed:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Recovered benchmark job did not complete after worker restart.")
    finally:
        worker.stop_background()

    final_job = storage.get_job(job.job_id)
    assert final_job.status == JobStatus.completed
    assert final_job.result_payload["benchmark_run_id"]


def test_kubernetes_backend_renders_manifest_for_persisted_job(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    backend = LocalJobBackend(storage)
    manifest_path = create_benchmark_manifest(tmp_path)
    job = backend.submit_benchmark(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=13))

    rendered = KubernetesJobBackend(storage).render_job_manifest(job.job_id)

    assert rendered["kind"] == "Job"
    assert rendered["metadata"]["labels"]["darwin.run/job-id"] == job.job_id
    container = rendered["spec"]["template"]["spec"]["containers"][0]
    args = container["args"]
    assert args[:2] == ["run-benchmark-worker", "--job-id"]
    assert container["command"] == ["sounio-stroke-lab"]
    assert container["resources"]["requests"]["cpu"] == "1000m"
    assert container["resources"]["requests"]["memory"] == "1Gi"
    assert rendered["spec"]["template"]["spec"]["serviceAccountName"] == "sounio-stroke-runner"
    assert rendered["darwin"]["dataset_manifest_path"] == str(manifest_path)


def test_kubernetes_backend_submits_and_reconciles_job_status(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    manifest_path = create_benchmark_manifest(tmp_path)
    fake_batch = _FakeBatchApi()
    fake_custom = _FakeCustomObjectsApi()
    backend = KubernetesJobBackend(storage, batch_api=fake_batch, custom_objects_api=fake_custom, config_loader=lambda: None)

    job = backend.submit_benchmark(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=29))

    assert job.status == JobStatus.queued
    assert job.result_payload["k8s_job_name"].startswith("sounio-benchmark-")
    assert job.result_payload["k8s_job_uid"].startswith("uid-")
    synced_running = backend.get_job(job.job_id)
    assert synced_running.status == JobStatus.running

    storage.update_job(
        job.job_id,
        result_payload={**synced_running.result_payload, "benchmark_run_id": "benchmark-123"},
    )
    fake_batch.jobs[(synced_running.result_payload["k8s_namespace"], synced_running.result_payload["k8s_job_name"])] = _FakeK8sJob(
        _FakeK8sStatus(succeeded=1)
    )

    synced_completed = backend.get_job(job.job_id)
    assert synced_completed.status == JobStatus.completed
    assert synced_completed.result_payload["benchmark_run_id"] == "benchmark-123"


def test_kubernetes_backend_submits_and_renders_agent_run_job(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    fake_batch = _FakeBatchApi()
    backend = KubernetesJobBackend(storage, batch_api=fake_batch, config_loader=lambda: None)
    run = AgentRun(
        run_id=uuid.uuid4().hex,
        objective="Cluster research run",
        surface=AgentSurface.research,
        status=AgentRunStatus.queued,
        root_agent="LabDirectorAgent",
        trace_id=f"trace-{uuid.uuid4().hex}",
        session_id=f"session-{uuid.uuid4().hex}",
        safety_policy=SafetyPolicy(),
        request_payload={"objective": "Cluster research run", "surface": "research"},
        dataset_manifest_path="/datasets/fixture-mini/small_benchmark_manifest.json",
    )
    storage.create_agent_run(run)

    job = backend.submit_agent_run(run)
    rendered = backend.render_job_manifest(job.job_id)

    assert job.kind == JobKind.agent_run
    assert job.result_payload["agent_run_id"] == run.run_id
    assert rendered["metadata"]["name"].startswith("sounio-agent-")
    assert rendered["darwin"]["agent_run_id"] == run.run_id
    container = rendered["spec"]["template"]["spec"]["containers"][0]
    assert container["args"][:3] == ["run-agent-worker", "--run-id", run.run_id]
    assert any(item["name"] == "SOUNIO_STROKE_AGENT_BACKEND" and item["value"] == "kubernetes" for item in container["env"])
    assert any(item["name"] == "SOUNIO_STROKE_K8S_WORKER_IMAGE" for item in container["env"])
    assert rendered["spec"]["template"]["spec"]["serviceAccountName"] == "sounio-stroke-runner"


def test_kubernetes_backend_syncs_kueue_workload_details(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    manifest_path = create_benchmark_manifest(tmp_path)
    fake_batch = _FakeBatchApi()
    fake_custom = _FakeCustomObjectsApi()
    backend = KubernetesJobBackend(storage, batch_api=fake_batch, custom_objects_api=fake_custom, config_loader=lambda: None)

    job = backend.submit_benchmark(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=37))
    uid = job.result_payload["k8s_job_uid"]
    fake_custom.workloads[(job.result_payload["k8s_namespace"], uid)] = {
        "metadata": {"name": "job-sounio-example"},
        "status": {
            "admission": {"clusterQueue": "darwin-shared"},
            "conditions": [
                {"type": "QuotaReserved", "status": "True", "reason": "QuotaReserved", "message": "Quota reserved"},
                {"type": "Admitted", "status": "True", "reason": "Admitted", "message": "Workload admitted"},
                {"type": "Finished", "status": "True", "reason": "Succeeded", "message": "Workload finished"},
            ],
        },
    }

    synced = backend.get_job(job.job_id)

    assert synced.result_payload["kueue_workload_name"] == "job-sounio-example"
    assert synced.result_payload["kueue_cluster_queue"] == "darwin-shared"
    assert synced.result_payload["kueue_admitted"] is True
    assert synced.result_payload["kueue_finished"] is True
    assert synced.result_payload["cluster_phase"] == "finished"


def test_kubernetes_backend_refreshes_finished_workload_for_completed_job(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    manifest_path = create_benchmark_manifest(tmp_path)
    fake_batch = _FakeBatchApi()
    fake_custom = _FakeCustomObjectsApi()
    backend = KubernetesJobBackend(storage, batch_api=fake_batch, custom_objects_api=fake_custom, config_loader=lambda: None)

    job = backend.submit_benchmark(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=41))
    uid = job.result_payload["k8s_job_uid"]
    namespace = job.result_payload["k8s_namespace"]
    job_name = job.result_payload["k8s_job_name"]

    fake_batch.jobs[(namespace, job_name)] = _FakeK8sJob(_FakeK8sStatus(succeeded=1))
    storage.update_job(
        job.job_id,
        status=JobStatus.completed,
        result_payload={
            **job.result_payload,
            "benchmark_run_id": "benchmark-456",
            "kueue_workload_name": "job-sounio-example",
            "kueue_finished": False,
        },
    )
    fake_custom.workloads[(namespace, uid)] = {
        "metadata": {"name": "job-sounio-example"},
        "status": {
            "admission": {"clusterQueue": "darwin-shared"},
            "conditions": [
                {"type": "Admitted", "status": "True", "reason": "Admitted", "message": "Workload admitted"},
                {"type": "Finished", "status": "True", "reason": "Succeeded", "message": "Workload finished"},
            ],
        },
    }

    refreshed = backend.get_job(job.job_id)

    assert refreshed.status == JobStatus.completed
    assert refreshed.result_payload["benchmark_run_id"] == "benchmark-456"
    assert refreshed.result_payload["kueue_finished"] is True
    assert refreshed.result_payload["cluster_phase"] == "finished"


def test_kubernetes_backend_cancel_marks_job_cancelled(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    manifest_path = create_benchmark_manifest(tmp_path)
    fake_batch = _FakeBatchApi()
    backend = KubernetesJobBackend(storage, batch_api=fake_batch, config_loader=lambda: None)

    job = backend.submit_benchmark(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=31))
    cancelled = backend.cancel_job(job.job_id)

    assert cancelled.status == JobStatus.cancelled
    assert cancelled.result_payload["cancel_requested"] is True
    assert fake_batch.deleted


def test_minio_object_store_writes_uri(tmp_path: Path):
    from sounio_stroke_lab.config import MinioSettings

    fake_client = _FakeMinioClient()
    store = MinioObjectStore(
        tmp_path / "artifacts",
        MinioSettings(
            endpoint="minio:9000",
            access_key="minio",
            secret_key="minio123",
            bucket="stroke-artifacts",
            secure=False,
        ),
        client_factory=lambda: fake_client,
    )
    store.initialize()
    stored = store.write_text("runs/example/report.md", "# report")

    assert stored.uri == "minio://stroke-artifacts/runs/example/report.md"
    assert fake_client.uploads[0][1] == "runs/example/report.md"
    assert stored.local_path.exists()


@pytest.mark.skipif(not os.getenv("SOUNIO_STROKE_TEST_POSTGRES_URL"), reason="Requires a live Postgres DSN.")
def test_postgres_state_store_job_claim_and_requeue():
    dsn = os.environ["SOUNIO_STROKE_TEST_POSTGRES_URL"]
    store = PostgresStateStore(dsn)
    store.initialize()
    suffix = uuid.uuid4().hex
    job = JobRecord(
        job_id=f"pg-job-{suffix}",
        owner_type=RecordOwnerType.benchmark_run,
        owner_id=f"pg-run-{suffix}",
        kind=JobKind.benchmark,
        status=JobStatus.queued,
    )
    store.save_job(job)

    claimed = store.claim_next_job(kind=JobKind.benchmark.value)
    assert claimed is not None
    assert claimed.job_id == job.job_id
    assert claimed.status == JobStatus.running

    store.requeue_inflight_jobs(kind=JobKind.benchmark.value)
    requeued = store.get_job(job.job_id)
    assert requeued.status == JobStatus.queued
    assert requeued.result_payload["recovered_after_restart"] is True


@pytest.mark.skipif(not os.getenv("SOUNIO_STROKE_TEST_POSTGRES_URL"), reason="Requires a live Postgres DSN.")
def test_postgres_state_store_initialize_is_serialized():
    dsn = os.environ["SOUNIO_STROKE_TEST_POSTGRES_URL"]
    errors: list[Exception] = []

    def initialize_store() -> None:
        try:
            PostgresStateStore(dsn).initialize()
        except Exception as exc:  # pragma: no cover - exercised only with live Postgres
            errors.append(exc)

    threads = [threading.Thread(target=initialize_store) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
