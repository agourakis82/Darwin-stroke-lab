import time
from pathlib import Path

from fastapi.testclient import TestClient

from sounio_stroke_lab.main import create_app


def _wait_for_run_completion(client: TestClient, run_id: str) -> dict:
    deadline = time.time() + 5.0
    last_payload = None
    while time.time() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["status"] in {"completed", "failed"}:
            return last_payload
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not finish in time. Last payload: {last_payload}")


def test_run_submit_resume_and_events_flow(tmp_path: Path):
    workspace = tmp_path / "workspace"
    repo = workspace / "src"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "README.md").write_text("# Pilot\n", encoding="utf-8")
    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")

    client = TestClient(create_app(storage_root=tmp_path / "data"))

    create_response = client.post(
        "/runs",
        json={
            "workspace_id": "sounio",
            "user_id": "demetrios",
            "workspace_path": str(workspace),
            "repo_path": str(repo),
            "task_name": "inventory-workspace",
            "parameters": {"simulate_delay_seconds": "0.1"},
        },
    )
    assert create_response.status_code == 201
    run = create_response.json()
    assert run["workflow_kind"] == "generic_agent_task"
    assert run["status"] in {"pending", "running", "completed"}

    finished_run = _wait_for_run_completion(client, run["run_id"])
    assert finished_run["status"] == "completed"

    summary_response = client.get(f"/runs/{run['run_id']}/resume-summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["run_id"] == run["run_id"]
    assert summary["status"] == "completed"
    assert summary["last_completed_step"]["seq"] == 3
    assert any("labctl run resume" in line for line in summary["resume_instructions"])
    assert summary["recent_artifacts"]
    assert summary["recent_events"]

    events_response = client.get(f"/runs/{run['run_id']}/events")
    assert events_response.status_code == 200
    events = events_response.json()
    assert events[0]["event_type"] == "run.submitted"
    assert events[-1]["event_type"] == "run.completed"

    run_root = Path(finished_run["run_root_uri"])
    assert run_root.exists()
    assert (run_root / "artifacts" / "context" / "workspace-context.json").exists()
    assert (run_root / "artifacts" / "task" / "agent-output.json").exists()
    assert (run_root / "artifacts" / "summary" / "resume-summary.md").exists()
