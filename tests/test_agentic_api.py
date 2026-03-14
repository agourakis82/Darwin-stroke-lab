import time
from pathlib import Path

from fastapi.testclient import TestClient

from sounio_stroke_lab.agentic import PublicPayloadFilter
from sounio_stroke_lab.main import create_app
from sounio_stroke_lab.schemas import AgentRunRequest
from tests.support import create_small_benchmark_manifest, create_study_file


class _FakeBatchApi:
    def __init__(self):
        self.created: list[tuple[str, dict]] = []

    def create_namespaced_job(self, namespace: str, body: dict):
        self.created.append((namespace, body))
        return {"metadata": {"name": body["metadata"]["name"], "namespace": namespace, "uid": f"uid-{body['metadata']['name']}"}}

    def read_namespaced_job(self, name: str, namespace: str):
        class _Job:
            metadata = {"uid": f"uid-{name}"}
            status = type("Status", (), {"active": 1, "succeeded": 0, "failed": 0, "conditions": []})()

        return _Job()


def _wait_for_terminal_run(client: TestClient, run_id: str, timeout_seconds: float = 45.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/agent/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"Agent run {run_id} did not reach a terminal state within {timeout_seconds} seconds.")


def test_agentic_dual_run_and_mcp_endpoints(tmp_path: Path):
    client = TestClient(create_app(storage_root=tmp_path / "data", start_benchmark_worker=True))
    manifest_path = create_small_benchmark_manifest(tmp_path)
    study_file = create_study_file(tmp_path)

    servers_response = client.get("/mcp/servers")
    assert servers_response.status_code == 200
    server_names = {item["name"] for item in servers_response.json()}
    assert "darwin-benchmark" in server_names
    assert "darwin-clinical" in server_names
    assert "darwin-research" in server_names
    research_server = next(item for item in servers_response.json() if item["name"] == "darwin-research")
    assert "search_public_literature" in research_server["allowed_tools"]

    health_response = client.get("/mcp/servers/darwin-sounio/health")
    assert health_response.status_code == 200
    assert health_response.json()["name"] == "darwin-sounio"

    create_response = client.post(
        "/agent/runs",
        json={
            "objective": "Build a benchmark-backed research brief and clinical copilot summary.",
            "surface": "dual",
            "dataset_manifest_path": str(manifest_path),
            "study_file_paths": [str(study_file)],
            "research_question": "Review stroke MCP agents using /tmp/patient123/study.dcm as context.",
            "model_family": "sounio_hypercomplex",
            "seed": 5,
        },
    )
    assert create_response.status_code == 202
    run = create_response.json()

    completed = _wait_for_terminal_run(client, run["run_id"])
    assert completed["status"] == "completed"
    assert completed["benchmark_run_id"]
    assert completed["research_brief_id"]
    assert completed["study_id"]
    assert "Research OS" in completed["final_output"]
    assert "Clinical Copilot" in completed["final_output"]
    assert completed["artifact_paths"]

    brief_response = client.post(
        "/research/briefs",
        json={
            "question": "Summarize the MCP and stroke benchmark evidence.",
            "benchmark_run_id": completed["benchmark_run_id"],
            "dataset_manifest_path": str(manifest_path),
        },
    )
    assert brief_response.status_code == 201
    brief = brief_response.json()
    assert brief["supporting_run_id"] == completed["benchmark_run_id"]
    assert brief["evidence_table_path"]
    assert brief["source_links"] == []

    with client.stream("GET", f"/agent/runs/{run['run_id']}/events") as response:
        assert response.status_code == 200
        body = "".join(chunk for chunk in response.iter_text())
    assert "event: step" in body
    assert "ClinicalManagerAgent" in body

    trace_response = client.get(f"/agent/runs/{run['run_id']}/trace")
    assert trace_response.status_code == 200
    trace_events = trace_response.json()
    assert trace_events
    assert any(event["event_type"] == "agent_step" for event in trace_events)

    artifact_response = client.get(f"/artifacts/agent_run/{run['run_id']}")
    assert artifact_response.status_code == 200
    agent_artifacts = artifact_response.json()
    assert any(item["name"] == "research_evidence_table" for item in agent_artifacts)
    assert any(item["name"] == "clinical_explanation" for item in agent_artifacts)

    audits = client.app.state.service.storage.list_tool_audit(run["run_id"])
    assert audits
    public_audits = [item for item in audits if item.trust_level == "public"]
    assert public_audits
    assert all(".dcm" not in item.sanitized_input_preview for item in public_audits)
    assert any("[REDACTED" in item.sanitized_input_preview for item in public_audits)
    steps = client.app.state.service.storage.list_agent_steps(run["run_id"])
    guardrail_steps = [step for step in steps if step.kind == "guardrail"]
    assert guardrail_steps


def test_public_payload_filter_redacts_local_identifiers():
    payload = {
        "path": "/tmp/patient123/scan.dcm",
        "note": "patient-001 and accession123 should not leave the machine",
        "items": ["C:\\\\data\\\\brain.nii.gz", "safe text"],
    }
    sanitized = PublicPayloadFilter.sanitize_payload(payload)
    rendered = PublicPayloadFilter.preview(sanitized)
    assert "[REDACTED_PATH]" in rendered or "[REDACTED_IMAGE_REF]" in rendered
    assert "scan.dcm" not in rendered
    assert "patient" not in rendered.lower()


def test_run_scoped_service_helpers(tmp_path: Path):
    client = TestClient(create_app(storage_root=tmp_path / "data", start_benchmark_worker=True))
    service = client.app.state.service
    manifest_path = create_small_benchmark_manifest(tmp_path)
    study_file = create_study_file(tmp_path)

    run = service.create_agent_run(
        AgentRunRequest(
            objective="Run scoped helper validation.",
            surface="dual",
            dataset_manifest_path=str(manifest_path),
            study_file_paths=[str(study_file)],
            research_question="Check run-scoped research helper behavior.",
            seed=7,
        )
    )

    context = service.get_agent_run_context_summary(run.run_id)
    assert context["dataset_manifest_available"] is True
    assert context["study_available"] is True

    study = service.prepare_study_for_agent_run(run.run_id)
    assert study.study_id

    validation = service.validate_agent_run_manifest(run.run_id)
    assert validation["valid"] is True

    cohort = service.summarize_agent_run_cohort(run.run_id)
    assert cohort["split"] == "test"

    benchmark = service.run_benchmark_for_agent_run(run.run_id)
    assert benchmark.run_id

    brief = service.compile_research_brief_for_agent_run(run.run_id)
    assert brief.brief_id
    assert brief.evidence_table_path

    quality = service.inspect_agent_run_study_quality(run.run_id)
    assert quality["study_id"] == study.study_id

    clinical = service.generate_clinical_summary_for_agent_run(run.run_id)
    assert clinical["study_id"] == study.study_id
    assert clinical["explanation_artifact"]


def test_agent_run_submits_kubernetes_job_when_backend_is_cluster(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SOUNIO_STROKE_JOB_BACKEND", "kubernetes")
    client = TestClient(create_app(storage_root=tmp_path / "data", start_benchmark_worker=False))
    service = client.app.state.service
    manifest_path = create_small_benchmark_manifest(tmp_path)
    fake_batch = _FakeBatchApi()
    service.job_backend._batch_api = fake_batch
    service.job_backend._config_loader = lambda: None

    response = client.post(
        "/agent/runs",
        json={
            "objective": "Run research branch on Kubernetes.",
            "surface": "research",
            "dataset_manifest_path": "/datasets/fixture-mini/small_benchmark_manifest.json",
            "seed": 11,
        },
    )
    assert response.status_code == 202
    run = response.json()
    assert run["job_id"]

    stored = client.get(f"/agent/runs/{run['run_id']}")
    assert stored.status_code == 200
    assert stored.json()["job_id"] == run["job_id"]

    manifest = client.get(f"/jobs/{run['job_id']}/manifest")
    assert manifest.status_code == 200
    payload = manifest.json()
    assert payload["metadata"]["name"].startswith("sounio-agent-")
    args = payload["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args[:3] == ["run-agent-worker", "--run-id", run["run_id"]]
    assert fake_batch.created
