import time
from pathlib import Path

from fastapi.testclient import TestClient

from sounio_stroke_lab.main import create_app
from tests.support import create_benchmark_manifest, create_small_benchmark_manifest


def _wait_for_campaign_terminal(client: TestClient, campaign_id: str, timeout_seconds: float = 90.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/campaigns/{campaign_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.1)
    raise AssertionError(f"Campaign {campaign_id} did not reach a terminal state within {timeout_seconds} seconds.")


def test_program_groups_campaigns_and_materializes_summary(tmp_path: Path):
    client = TestClient(create_app(storage_root=tmp_path / "data", start_benchmark_worker=True))
    manifest_path = create_benchmark_manifest(tmp_path)
    small_manifest_path = create_small_benchmark_manifest(tmp_path)

    create_program_response = client.post(
        "/programs",
        json={
            "name": "lesion-sensitivity-program",
            "objective": "Track campaigns that improve lesion sensitivity without losing ASPECTS calibration.",
            "hypothesis": "A tighter Sounio sweep should improve cortical sensitivity across small realistic cohorts.",
        },
    )
    assert create_program_response.status_code == 201
    program = create_program_response.json()
    assert program["name"] == "lesion-sensitivity-program"

    program_campaign_response = client.post(
        f"/programs/{program['program_id']}/campaigns",
        json={
            "name": "program-seeded-campaign",
            "objective": "Run the first in-program benchmark sweep.",
            "benchmark_specs": [
                {
                    "label": "program-main-seed-21",
                    "request": {"dataset_manifest_path": str(manifest_path), "seed": 21},
                }
            ],
        },
    )
    assert program_campaign_response.status_code == 202
    program_campaign = program_campaign_response.json()
    assert program_campaign["program_id"] == program["program_id"]

    detached_campaign_response = client.post(
        "/campaigns",
        json={
            "name": "detached-campaign",
            "objective": "Create a second campaign and attach it afterward.",
            "benchmark_specs": [
                {
                    "label": "detached-mini-seed-22",
                    "request": {"dataset_manifest_path": str(small_manifest_path), "seed": 22},
                }
            ],
        },
    )
    assert detached_campaign_response.status_code == 202
    detached_campaign = detached_campaign_response.json()
    assert detached_campaign["program_id"] is None

    attach_response = client.post(
        f"/programs/{program['program_id']}/campaigns/attach",
        json={"campaign_id": detached_campaign["campaign_id"]},
    )
    assert attach_response.status_code == 200

    _wait_for_campaign_terminal(client, program_campaign["campaign_id"])
    _wait_for_campaign_terminal(client, detached_campaign["campaign_id"])

    refreshed_program_response = client.get(f"/programs/{program['program_id']}")
    assert refreshed_program_response.status_code == 200
    refreshed_program = refreshed_program_response.json()
    assert refreshed_program["summary"]["total_campaigns"] == 2
    assert refreshed_program["summary"]["campaigns"]
    assert refreshed_program["summary"]["leading_campaign_id"]
    assert refreshed_program["summary"]["next_actions"]
    assert refreshed_program["status"] in {"active", "completed", "blocked"}

    listed_programs = client.get("/programs")
    assert listed_programs.status_code == 200
    assert any(item["program_id"] == program["program_id"] for item in listed_programs.json())

    program_portfolio_response = client.get("/portfolio/programs")
    assert program_portfolio_response.status_code == 200
    program_portfolio = program_portfolio_response.json()
    assert program_portfolio["total_programs"] >= 1
    assert program_portfolio["programs"]
    assert program_portfolio["programs"][0]["program_id"] == program["program_id"]
    assert program_portfolio["next_actions"]

    artifacts_response = client.get(f"/artifacts/program/{program['program_id']}")
    assert artifacts_response.status_code == 200
    artifacts = artifacts_response.json()
    assert {item["name"] for item in artifacts} >= {"program_summary", "program_report"}

    portfolio_artifacts_response = client.get("/artifacts/portfolio/program-portfolio")
    assert portfolio_artifacts_response.status_code == 200
    portfolio_artifacts = portfolio_artifacts_response.json()
    assert {item["name"] for item in portfolio_artifacts} >= {"portfolio_summary", "portfolio_report"}
