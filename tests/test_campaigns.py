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


def test_campaign_submits_multiple_benchmark_jobs_and_reconciles(tmp_path: Path):
    client = TestClient(create_app(storage_root=tmp_path / "data", start_benchmark_worker=True))
    manifest_path = create_benchmark_manifest(tmp_path)
    small_manifest_path = create_small_benchmark_manifest(tmp_path)

    create_response = client.post(
        "/campaigns",
        json={
            "name": "small-realistic-smoke",
            "objective": "Compare benchmark behavior across two small manifests.",
            "benchmark_specs": [
                {
                    "label": "fixture-main-seed-11",
                    "request": {"dataset_manifest_path": str(manifest_path), "seed": 11},
                },
                {
                    "label": "fixture-mini-seed-12",
                    "request": {"dataset_manifest_path": str(small_manifest_path), "seed": 12},
                },
            ],
            "notes": "Platform-level campaign smoke test.",
        },
    )
    assert create_response.status_code == 202
    created = create_response.json()
    assert created["name"] == "small-realistic-smoke"
    assert len(created["benchmark_specs"]) == 2
    assert all(item["job_id"] for item in created["benchmark_specs"])

    listed = client.get("/campaigns")
    assert listed.status_code == 200
    assert any(item["campaign_id"] == created["campaign_id"] for item in listed.json())

    terminal = _wait_for_campaign_terminal(client, created["campaign_id"])
    assert terminal["status"] == "completed"
    assert terminal["summary"]["total_runs"] == 2
    assert terminal["summary"]["completed_runs"] == 2
    assert len(terminal["summary"]["completed_benchmark_run_ids"]) == 2
    assert len(terminal["summary"]["run_summaries"]) == 2
    assert terminal["summary"]["best_run"]["benchmark_run_id"]
    assert terminal["summary"]["best_run"]["leading_model"]
    assert "sounio_isles_dice" in terminal["summary"]["best_run"]
    assert terminal["summary"]["next_experiment_recommendations"]
    assert terminal["summary"]["follow_up_proposals"]
    assert terminal["summary"]["experiment_plans"]

    follow_up_response = client.get(f"/campaigns/{created['campaign_id']}/followup")
    assert follow_up_response.status_code == 200
    proposals = follow_up_response.json()
    assert len(proposals) >= 1
    assert proposals[0]["proposal_id"]
    assert proposals[0]["benchmark_specs"]

    launched_follow_up_response = client.post(
        f"/campaigns/{created['campaign_id']}/followup",
        json={"proposal_id": proposals[0]["proposal_id"]},
    )
    assert launched_follow_up_response.status_code == 202
    follow_up_campaign = launched_follow_up_response.json()
    assert follow_up_campaign["campaign_id"] != created["campaign_id"]
    assert follow_up_campaign["name"]
    assert len(follow_up_campaign["benchmark_specs"]) == len(proposals[0]["benchmark_specs"])

    plans_response = client.get(f"/campaigns/{created['campaign_id']}/plans")
    assert plans_response.status_code == 200
    plans = plans_response.json()
    assert len(plans) >= 1
    assert plans[0]["plan_id"]
    assert plans[0]["success_criteria"]
    assert plans[0]["required_baselines"]
    assert plans[0]["recommended_agent_request"]["dataset_manifest_path"]

    plan_reports_response = client.get(f"/campaigns/{created['campaign_id']}/plan-reports")
    assert plan_reports_response.status_code == 200
    plan_reports = plan_reports_response.json()
    assert len(plan_reports) == len(plans)
    assert plan_reports[0]["acceptance_status"] in {"ready", "watch", "blocked"}
    assert isinstance(plan_reports[0]["launch_ready"], bool)
    assert plan_reports[0]["acceptance_criteria"]
    assert plan_reports[0]["acceptance_notes"]

    launched_plan_campaign_response = client.post(
        f"/campaigns/{created['campaign_id']}/plans/{plans[0]['plan_id']}/campaign",
    )
    assert launched_plan_campaign_response.status_code == 202
    launched_plan_campaign = launched_plan_campaign_response.json()
    assert launched_plan_campaign["campaign_id"] not in {created["campaign_id"], follow_up_campaign["campaign_id"]}
    assert launched_plan_campaign["benchmark_specs"]

    launched_plan_agent_response = client.post(
        f"/campaigns/{created['campaign_id']}/plans/{plans[0]['plan_id']}/agent-run",
        json={"notes": "Campaign plan agent smoke."},
    )
    assert launched_plan_agent_response.status_code == 202
    launched_plan_agent = launched_plan_agent_response.json()
    assert launched_plan_agent["run_id"]
    assert launched_plan_agent["dataset_manifest_path"]
    assert launched_plan_agent["status"] in {"queued", "running", "completed"}

    artifact_response = client.get(f"/artifacts/campaign/{created['campaign_id']}")
    assert artifact_response.status_code == 200
    artifacts = artifact_response.json()
    assert {item["name"] for item in artifacts} >= {
        "campaign_summary",
        "campaign_report",
        "experiment_plans",
        "experiment_plan_reports",
    }

    portfolio_response = client.get("/portfolio/campaigns")
    assert portfolio_response.status_code == 200
    portfolio = portfolio_response.json()
    assert portfolio["total_campaigns"] >= 3
    assert portfolio["campaigns"]
    assert portfolio["campaigns"][0]["campaign_id"]
    assert portfolio["next_actions"]

    portfolio_artifacts_response = client.get("/artifacts/portfolio/campaign-portfolio")
    assert portfolio_artifacts_response.status_code == 200
    portfolio_artifacts = portfolio_artifacts_response.json()
    assert {item["name"] for item in portfolio_artifacts} >= {"portfolio_summary", "portfolio_report"}

    brief_response = client.post(
        f"/campaigns/{created['campaign_id']}/brief",
        json={"question": "Summarize the campaign evidence and recommend the next experiment."},
    )
    assert brief_response.status_code == 201
    brief = brief_response.json()
    assert brief["supporting_campaign_id"] == created["campaign_id"]
    assert brief["supporting_run_id"] == terminal["summary"]["best_run"]["benchmark_run_id"]
    assert brief["protocol_recommendations"]

    for entry in terminal["benchmark_specs"]:
        assert entry["status"] == "completed"
        assert entry["benchmark_run_id"]

        benchmark_response = client.get(f"/benchmark/runs/{entry['benchmark_run_id']}")
        assert benchmark_response.status_code == 200
