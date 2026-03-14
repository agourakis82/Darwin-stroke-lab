from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from sounio_stroke_lab.config import get_runtime_config
from sounio_stroke_lab.object_store import LocalObjectStore, MinioObjectStore, ObjectStore
from sounio_stroke_lab.schemas import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AnalysisResult,
    ArtifactRef,
    BenchmarkRun,
    CampaignRecord,
    JobRecord,
    MCPServerDescriptor,
    ProgramRecord,
    RecordOwnerType,
    ResearchBrief,
    StudyRecord,
    StudyStatus,
    ToolAudit,
    TraceEvent,
    WorkerHeartbeat,
)
from sounio_stroke_lab.state_store import PostgresStateStore, SQLiteStateStore, StateStore


class StorageManager:
    def __init__(
        self,
        root: Path,
        *,
        state_store: StateStore | None = None,
        object_store: ObjectStore | None = None,
    ):
        self.root = root
        self.study_dir = self.root / "studies"
        self.artifact_dir = self.root / "artifacts"
        runtime = get_runtime_config(storage_root=root)
        self.state_store = state_store or self._build_state_store(runtime.db_url)
        self.object_store = object_store or self._build_object_store(runtime.object_store, runtime.minio)

    def _build_state_store(self, db_url: str | None) -> StateStore:
        if db_url:
            return PostgresStateStore(db_url)
        return SQLiteStateStore(self.root / "state.sqlite3")

    def _build_object_store(self, backend: str, minio_settings) -> ObjectStore:
        if backend == "minio":
            if minio_settings is None:
                raise RuntimeError(
                    "SOUNIO_STROKE_OBJECT_STORE=minio requires SOUNIO_STROKE_MINIO_ENDPOINT and credentials."
                )
            return MinioObjectStore(self.artifact_dir, minio_settings)
        return LocalObjectStore(self.artifact_dir)

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.study_dir.mkdir(parents=True, exist_ok=True)
        self.object_store.initialize()
        self.state_store.initialize()

    def create_study(self, files: list[str], input_mode, warnings: list[str]) -> StudyRecord:
        record = StudyRecord(
            study_id=uuid.uuid4().hex,
            input_mode=input_mode,
            status=StudyStatus.created,
            files=files,
            warnings=warnings,
        )
        self.save_study(record)
        return record

    def save_study(self, record: StudyRecord) -> None:
        self.state_store.save_study(record)

    def update_study_status(self, study_id: str, status: StudyStatus, warnings: list[str] | None = None) -> StudyRecord:
        record = self.get_study(study_id)
        updated = record.model_copy(update={"status": status, "warnings": warnings or record.warnings})
        self.save_study(updated)
        return updated

    def get_study(self, study_id: str) -> StudyRecord:
        return self.state_store.get_study(study_id)

    def allocate_study_dir(self, study_id: str) -> Path:
        path = self.study_dir / study_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_analysis(self, result: AnalysisResult) -> None:
        self.state_store.save_analysis(result)

    def get_analysis(self, study_id: str) -> AnalysisResult:
        return self.state_store.get_analysis(study_id)

    def save_benchmark_run(self, run: BenchmarkRun) -> None:
        self.state_store.save_benchmark_run(run)

    def get_benchmark_run(self, run_id: str) -> BenchmarkRun:
        return self.state_store.get_benchmark_run(run_id)

    def save_campaign(self, campaign: CampaignRecord) -> None:
        self.state_store.save_campaign(campaign)

    def get_campaign(self, campaign_id: str) -> CampaignRecord:
        return self.state_store.get_campaign(campaign_id)

    def list_campaigns(self) -> list[CampaignRecord]:
        return self.state_store.list_campaigns()

    def save_program(self, program: ProgramRecord) -> None:
        self.state_store.save_program(program)

    def get_program(self, program_id: str) -> ProgramRecord:
        return self.state_store.get_program(program_id)

    def list_programs(self) -> list[ProgramRecord]:
        return self.state_store.list_programs()

    def save_agent_run(self, run: AgentRun) -> None:
        self.state_store.save_agent_run(run)

    def create_agent_run(self, run: AgentRun) -> AgentRun:
        return self.state_store.create_agent_run(run)

    def get_agent_run(self, run_id: str) -> AgentRun:
        return self.state_store.get_agent_run(run_id)

    def update_agent_run(self, run_id: str, **updates) -> AgentRun:
        return self.state_store.update_agent_run(run_id, **updates)

    def requeue_inflight_agent_runs(self) -> None:
        self.state_store.requeue_inflight_agent_runs()

    def claim_next_agent_run(self) -> AgentRun | None:
        return self.state_store.claim_next_agent_run()

    def save_agent_step(self, step: AgentStep) -> None:
        self.state_store.save_agent_step(step)

    def list_agent_steps(self, run_id: str) -> list[AgentStep]:
        return self.state_store.list_agent_steps(run_id)

    def save_research_brief(self, brief: ResearchBrief) -> None:
        self.state_store.save_research_brief(brief)

    def get_research_brief(self, brief_id: str) -> ResearchBrief:
        return self.state_store.get_research_brief(brief_id)

    def save_mcp_server(self, descriptor: MCPServerDescriptor) -> None:
        self.state_store.save_mcp_server(descriptor)

    def list_mcp_servers(self) -> list[MCPServerDescriptor]:
        return self.state_store.list_mcp_servers()

    def get_mcp_server(self, name: str) -> MCPServerDescriptor:
        return self.state_store.get_mcp_server(name)

    def save_tool_audit(self, audit: ToolAudit) -> None:
        self.state_store.save_tool_audit(audit)

    def list_tool_audit(self, run_id: str) -> list[ToolAudit]:
        return self.state_store.list_tool_audit(run_id)

    def save_trace_event(self, event: TraceEvent) -> None:
        self.state_store.save_trace_event(event)

    def list_trace_events(self, run_id: str) -> list[TraceEvent]:
        return self.state_store.list_trace_events(run_id)

    def save_artifact_ref(self, artifact: ArtifactRef) -> None:
        self.state_store.save_artifact_ref(artifact)

    def register_artifact(
        self,
        owner_type: RecordOwnerType,
        owner_id: str,
        *,
        artifact_id: str | None = None,
        name: str,
        kind: str,
        path: str,
        description: str = "",
        content_type: str | None = None,
    ) -> ArtifactRef:
        artifact_path = Path(path)
        materialized = None
        if artifact_path.exists():
            relative_path = self._artifact_relative_path(owner_type, owner_id, name, artifact_path)
            materialized = self.object_store.ensure_object(relative_path, artifact_path)
        inferred_type = content_type or (
            materialized.content_type if materialized else mimetypes.guess_type(str(artifact_path))[0]
        ) or ""
        artifact = ArtifactRef(
            artifact_id=artifact_id or uuid.uuid4().hex,
            owner_type=owner_type,
            owner_id=owner_id,
            name=name,
            kind=kind,
            path=str(materialized.local_path) if materialized else str(artifact_path),
            uri=materialized.uri if materialized else None,
            description=description,
            content_type=inferred_type,
            bytes=materialized.bytes if materialized else None,
        )
        self.save_artifact_ref(artifact)
        return artifact

    def _artifact_relative_path(self, owner_type: RecordOwnerType, owner_id: str, name: str, artifact_path: Path) -> str:
        try:
            return str(artifact_path.resolve().relative_to(self.artifact_dir.resolve()))
        except ValueError:
            suffix = artifact_path.suffix or ".bin"
            return f"{owner_id}/{name}{suffix}"

    def list_artifact_refs(self, owner_type: RecordOwnerType, owner_id: str) -> list[ArtifactRef]:
        owner_value = owner_type.value if hasattr(owner_type, "value") else str(owner_type)
        return self.state_store.list_artifact_refs(owner_value, owner_id)

    def save_job(self, job: JobRecord) -> None:
        self.state_store.save_job(job)

    def get_job(self, job_id: str) -> JobRecord:
        return self.state_store.get_job(job_id)

    def update_job(self, job_id: str, **updates) -> JobRecord:
        return self.state_store.update_job(job_id, **updates)

    def requeue_inflight_jobs(self, kind: str | None = None) -> None:
        self.state_store.requeue_inflight_jobs(kind=kind)

    def claim_next_job(self, kind: str | None = None) -> JobRecord | None:
        return self.state_store.claim_next_job(kind=kind)

    def save_worker_heartbeat(self, heartbeat: WorkerHeartbeat) -> None:
        self.state_store.save_worker_heartbeat(heartbeat)

    def list_worker_heartbeats(self, worker_kind: str | None = None) -> list[WorkerHeartbeat]:
        return self.state_store.list_worker_heartbeats(worker_kind=worker_kind)

    def write_artifact_text(self, relative_path: str, content: str) -> str:
        return str(self.object_store.write_text(relative_path, content).local_path)

    def append_artifact_text(self, relative_path: str, content: str) -> str:
        return str(self.object_store.append_text(relative_path, content).local_path)

    def write_artifact_json(self, relative_path: str, payload: dict) -> str:
        return str(self.object_store.write_json(relative_path, payload).local_path)
