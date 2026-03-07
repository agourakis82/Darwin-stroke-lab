from io import BytesIO
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from sounio_stroke_lab.main import create_app
from tests.support import create_benchmark_manifest


def _npy_payload() -> bytes:
    volume = np.zeros((10, 20, 20), dtype=np.float32)
    volume[:, 6:12, 10:16] = 0.25
    buffer = BytesIO()
    np.save(buffer, volume)
    return buffer.getvalue()


def test_api_study_analysis_and_benchmark_flow(tmp_path: Path):
    client = TestClient(create_app(storage_root=tmp_path / "data"))
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
    assert benchmark["language_stack"] == "multi"
    assert len(benchmark["artifacts"]) >= 8

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
