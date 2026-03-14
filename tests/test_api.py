from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sounio_stroke_lab.main import create_app
from tests.support import create_benchmark_manifest


def _npy_payload() -> bytes:
    volume = np.zeros((10, 20, 20), dtype=np.float32)
    volume[:, 6:12, 10:16] = 0.25
    buffer = BytesIO()
    np.save(buffer, volume)
    return buffer.getvalue()


def _wait_for_job_terminal(client: TestClient, job_id: str, timeout_seconds: float = 30.0) -> dict:
    import time

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"Job {job_id} did not reach a terminal state within {timeout_seconds} seconds.")


def test_api_study_analysis_and_benchmark_flow(tmp_path: Path):
    client = TestClient(create_app(storage_root=tmp_path / "data", start_benchmark_worker=True))
    manifest_path = create_benchmark_manifest(tmp_path)

    create_response = client.post(
        "/studies",
        files=[("files", ("study.npy", _npy_payload(), "application/octet-stream"))],
    )
    assert create_response.status_code == 201
    study = create_response.json()
    assert study["input_mode"] == "research"

    benchmark_response = client.post(
        "/benchmark/runs",
        json={"dataset_manifest_path": str(manifest_path), "seed": 7},
    )
    assert benchmark_response.status_code == 201
    benchmark = benchmark_response.json()
    assert benchmark["job_id"]
    assert benchmark["language_stack"] == "multi"
    assert len(benchmark["artifacts"]) >= 8

    job_response = client.get(f"/jobs/{benchmark['job_id']}")
    assert job_response.status_code == 200
    job = job_response.json()
    assert job["status"] == "completed"
    assert job["owner_type"] == "benchmark_run"
    assert job["owner_id"] == benchmark["run_id"]

    artifact_response = client.get(f"/artifacts/benchmark_run/{benchmark['run_id']}")
    assert artifact_response.status_code == 200
    artifact_refs = artifact_response.json()
    assert len(artifact_refs) >= len(benchmark["artifacts"])
    assert any(item["name"] == "train_manifest" for item in artifact_refs)

    fetch_benchmark = client.get(f"/benchmark/runs/{benchmark['run_id']}")
    assert fetch_benchmark.status_code == 200
    assert fetch_benchmark.json()["run_id"] == benchmark["run_id"]

    sounio_artifact_path = next(
        item["path"] for item in benchmark["artifacts"] if item["name"] == "model_sounio_hypercomplex"
    )

    analysis_response = client.post(
        f"/studies/{study['study_id']}/analyze",
        json={
            "model_family": "sounio_hypercomplex",
            "include_baseline_comparison": True,
            "model_artifact_path": sounio_artifact_path,
        },
    )
    assert analysis_response.status_code == 200
    analysis = analysis_response.json()
    assert analysis["model_family"] == "sounio_hypercomplex"
    assert analysis["benchmark_context"]["pipeline_version"] == "common-preprocess-v1"
    assert analysis["benchmark_context"]["dataset_version"] == "fixture-v1"
    assert "python_3d_conventional" in analysis["baseline_comparison"]
    assert analysis["baseline_comparison"]["python_3d_conventional"]["uses_trained_artifact"] is True
    assert Path(analysis["heatmap_volume_ref"]).exists()

    get_response = client.get(f"/studies/{study['study_id']}/result")
    assert get_response.status_code == 200
    assert get_response.json()["study_id"] == study["study_id"]


def test_api_submit_benchmark_job_and_poll(tmp_path: Path):
    client = TestClient(create_app(storage_root=tmp_path / "data", start_benchmark_worker=True))
    manifest_path = create_benchmark_manifest(tmp_path)

    submit_response = client.post(
        "/benchmark/jobs",
        json={"dataset_manifest_path": str(manifest_path), "seed": 9},
    )
    assert submit_response.status_code == 202
    job = submit_response.json()
    assert job["status"] in {"queued", "running", "completed"}
    assert job["kind"] == "benchmark"

    terminal = _wait_for_job_terminal(client, job["job_id"])
    assert terminal["status"] == "completed"
    benchmark_run_id = terminal["result_payload"]["benchmark_run_id"]

    benchmark_response = client.get(f"/benchmark/runs/{benchmark_run_id}")
    assert benchmark_response.status_code == 200
    benchmark = benchmark_response.json()
    assert benchmark["job_id"] == job["job_id"]


def test_api_blocking_benchmark_requires_active_worker(tmp_path: Path):
    client = TestClient(create_app(storage_root=tmp_path / "data", start_benchmark_worker=False))
    manifest_path = create_benchmark_manifest(tmp_path)

    response = client.post(
        "/benchmark/runs",
        json={"dataset_manifest_path": str(manifest_path), "seed": 9},
    )

    assert response.status_code == 409
    assert "No active benchmark worker heartbeat" in response.json()["detail"]


@pytest.mark.usefixtures("monkeypatch")
def test_api_requires_bearer_token_when_configured(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SOUNIO_STROKE_API_TOKEN", "secret-token")
    client = TestClient(create_app(storage_root=tmp_path / "data", start_benchmark_worker=False))
    manifest_path = create_benchmark_manifest(tmp_path)

    health = client.get("/health")
    assert health.status_code == 200
    ready = client.get("/readyz")
    assert ready.status_code == 200

    unauthorized = client.post("/benchmark/jobs", json={"dataset_manifest_path": str(manifest_path), "seed": 3})
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/benchmark/jobs",
        json={"dataset_manifest_path": str(manifest_path), "seed": 3},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert authorized.status_code == 202
