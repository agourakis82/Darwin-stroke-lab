from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sounio_stroke_lab.schemas import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AnalysisResult,
    ArtifactRef,
    BenchmarkRun,
    CampaignRecord,
    JobRecord,
    JobStatus,
    MCPServerDescriptor,
    ProgramRecord,
    ResearchBrief,
    StudyRecord,
    TraceEvent,
    ToolAudit,
    WorkerHeartbeat,
)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - optional dependency path
    psycopg = None
    dict_row = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _model_to_json(model: Any) -> str:
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json()
    return json.dumps(model)


def _payload_to_model(model_cls, payload: Any):
    if isinstance(payload, str):
        return model_cls.model_validate_json(payload)
    return model_cls.model_validate(payload)


class StateStore(ABC):
    @abstractmethod
    def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_study(self, record: StudyRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_study(self, study_id: str) -> StudyRecord:
        raise NotImplementedError

    @abstractmethod
    def save_analysis(self, result: AnalysisResult) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_analysis(self, study_id: str) -> AnalysisResult:
        raise NotImplementedError

    @abstractmethod
    def save_benchmark_run(self, run: BenchmarkRun) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_benchmark_run(self, run_id: str) -> BenchmarkRun:
        raise NotImplementedError

    @abstractmethod
    def save_campaign(self, campaign: CampaignRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_campaign(self, campaign_id: str) -> CampaignRecord:
        raise NotImplementedError

    @abstractmethod
    def list_campaigns(self) -> list[CampaignRecord]:
        raise NotImplementedError

    @abstractmethod
    def save_program(self, program: ProgramRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_program(self, program_id: str) -> ProgramRecord:
        raise NotImplementedError

    @abstractmethod
    def list_programs(self) -> list[ProgramRecord]:
        raise NotImplementedError

    @abstractmethod
    def save_agent_run(self, run: AgentRun) -> None:
        raise NotImplementedError

    @abstractmethod
    def create_agent_run(self, run: AgentRun) -> AgentRun:
        raise NotImplementedError

    @abstractmethod
    def get_agent_run(self, run_id: str) -> AgentRun:
        raise NotImplementedError

    @abstractmethod
    def update_agent_run(self, run_id: str, **updates) -> AgentRun:
        raise NotImplementedError

    @abstractmethod
    def requeue_inflight_agent_runs(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def claim_next_agent_run(self) -> AgentRun | None:
        raise NotImplementedError

    @abstractmethod
    def save_agent_step(self, step: AgentStep) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_agent_steps(self, run_id: str) -> list[AgentStep]:
        raise NotImplementedError

    @abstractmethod
    def save_research_brief(self, brief: ResearchBrief) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_research_brief(self, brief_id: str) -> ResearchBrief:
        raise NotImplementedError

    @abstractmethod
    def save_mcp_server(self, descriptor: MCPServerDescriptor) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_mcp_servers(self) -> list[MCPServerDescriptor]:
        raise NotImplementedError

    @abstractmethod
    def get_mcp_server(self, name: str) -> MCPServerDescriptor:
        raise NotImplementedError

    @abstractmethod
    def save_tool_audit(self, audit: ToolAudit) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_tool_audit(self, run_id: str) -> list[ToolAudit]:
        raise NotImplementedError

    @abstractmethod
    def save_trace_event(self, event: TraceEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_trace_events(self, run_id: str) -> list[TraceEvent]:
        raise NotImplementedError

    @abstractmethod
    def save_artifact_ref(self, artifact: ArtifactRef) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_artifact_refs(self, owner_type: str, owner_id: str) -> list[ArtifactRef]:
        raise NotImplementedError

    @abstractmethod
    def save_job(self, job: JobRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_job(self, job_id: str) -> JobRecord:
        raise NotImplementedError

    @abstractmethod
    def update_job(self, job_id: str, **updates) -> JobRecord:
        raise NotImplementedError

    @abstractmethod
    def requeue_inflight_jobs(self, kind: str | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def claim_next_job(self, kind: str | None = None) -> JobRecord | None:
        raise NotImplementedError

    @abstractmethod
    def save_worker_heartbeat(self, heartbeat: WorkerHeartbeat) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_worker_heartbeats(self, worker_kind: str | None = None) -> list[WorkerHeartbeat]:
        raise NotImplementedError


class SQLiteStateStore(StateStore):
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS studies (
                    study_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    study_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS programs (
                    program_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS programs (
                    program_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_briefs (
                    brief_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_audit (
                    audit_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_refs (
                    artifact_id TEXT PRIMARY KEY,
                    owner_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "kind" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT ''")
            if "status" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN status TEXT NOT NULL DEFAULT ''")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_kind_updated ON jobs (status, kind, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_campaigns_status_updated ON campaigns (status, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_programs_status_updated ON programs (status, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_runs_status_updated ON agent_runs (status, updated_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_heartbeats (
                    worker_id TEXT PRIMARY KEY,
                    worker_kind TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_kind_updated ON worker_heartbeats (worker_kind, updated_at)"
            )

    def save_study(self, record: StudyRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO studies (study_id, payload) VALUES (?, ?)",
                (record.study_id, _model_to_json(record)),
            )

    def get_study(self, study_id: str) -> StudyRecord:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM studies WHERE study_id = ?", (study_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown study_id: {study_id}")
        return StudyRecord.model_validate_json(row[0])

    def save_analysis(self, result: AnalysisResult) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO analyses (study_id, payload) VALUES (?, ?)",
                (result.study_id, _model_to_json(result)),
            )

    def get_analysis(self, study_id: str) -> AnalysisResult:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM analyses WHERE study_id = ?", (study_id,)).fetchone()
        if not row:
            raise KeyError(f"No analysis for study_id: {study_id}")
        return AnalysisResult.model_validate_json(row[0])

    def save_benchmark_run(self, run: BenchmarkRun) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO benchmark_runs (run_id, payload) VALUES (?, ?)",
                (run.run_id, _model_to_json(run)),
            )

    def get_benchmark_run(self, run_id: str) -> BenchmarkRun:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM benchmark_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown benchmark run: {run_id}")
        return BenchmarkRun.model_validate_json(row[0])

    def save_campaign(self, campaign: CampaignRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO campaigns (campaign_id, status, updated_at, payload) VALUES (?, ?, ?, ?)",
                (
                    campaign.campaign_id,
                    campaign.status.value if hasattr(campaign.status, "value") else str(campaign.status),
                    campaign.updated_at.isoformat(),
                    _model_to_json(campaign),
                ),
            )

    def get_campaign(self, campaign_id: str) -> CampaignRecord:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM campaigns WHERE campaign_id = ?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown campaign: {campaign_id}")
        return CampaignRecord.model_validate_json(row[0])

    def list_campaigns(self) -> list[CampaignRecord]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT payload FROM campaigns ORDER BY updated_at DESC").fetchall()
        return [CampaignRecord.model_validate_json(row[0]) for row in rows]

    def save_program(self, program: ProgramRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO programs (program_id, status, updated_at, payload) VALUES (?, ?, ?, ?)",
                (
                    program.program_id,
                    program.status.value if hasattr(program.status, "value") else str(program.status),
                    program.updated_at.isoformat(),
                    _model_to_json(program),
                ),
            )

    def get_program(self, program_id: str) -> ProgramRecord:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM programs WHERE program_id = ?", (program_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown program: {program_id}")
        return ProgramRecord.model_validate_json(row[0])

    def list_programs(self) -> list[ProgramRecord]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT payload FROM programs ORDER BY updated_at DESC").fetchall()
        return [ProgramRecord.model_validate_json(row[0]) for row in rows]

    def save_agent_run(self, run: AgentRun) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agent_runs (run_id, status, updated_at, payload) VALUES (?, ?, ?, ?)",
                (
                    run.run_id,
                    run.status.value if hasattr(run.status, "value") else str(run.status),
                    run.updated_at.isoformat(),
                    _model_to_json(run),
                ),
            )

    def create_agent_run(self, run: AgentRun) -> AgentRun:
        self.save_agent_run(run)
        return run

    def get_agent_run(self, run_id: str) -> AgentRun:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown agent run: {run_id}")
        return AgentRun.model_validate_json(row[0])

    def update_agent_run(self, run_id: str, **updates) -> AgentRun:
        run = self.get_agent_run(run_id)
        updated = run.model_copy(update={**updates, "updated_at": _utc_now()})
        self.save_agent_run(updated)
        return updated

    def requeue_inflight_agent_runs(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT run_id, payload FROM agent_runs WHERE status = ?",
                (AgentRunStatus.running.value,),
            ).fetchall()
        for run_id, payload in rows:
            run = AgentRun.model_validate_json(payload)
            warnings = list(run.warnings)
            warnings.append("Recovered queued agent run after process restart.")
            updated = run.model_copy(
                update={
                    "status": AgentRunStatus.queued,
                    "warnings": warnings,
                    "updated_at": _utc_now(),
                }
            )
            self.save_agent_run(updated)

    def claim_next_agent_run(self) -> AgentRun | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT run_id, payload FROM agent_runs WHERE status = ? ORDER BY updated_at ASC LIMIT 1",
                (AgentRunStatus.queued.value,),
            ).fetchone()
            if not row:
                return None
        run_id, payload = row
        run = AgentRun.model_validate_json(payload)
        updated = run.model_copy(update={"status": AgentRunStatus.running, "updated_at": _utc_now()})
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute(
                """
                UPDATE agent_runs
                SET status = ?, updated_at = ?, payload = ?
                WHERE run_id = ? AND status = ?
                """,
                (
                    AgentRunStatus.running.value,
                    updated.updated_at.isoformat(),
                    _model_to_json(updated),
                    run_id,
                    AgentRunStatus.queued.value,
                ),
            )
            if result.rowcount != 1:
                return None
        return updated

    def save_agent_step(self, step: AgentStep) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agent_steps (step_id, run_id, created_at, payload) VALUES (?, ?, ?, ?)",
                (step.step_id, step.run_id, step.created_at.isoformat(), _model_to_json(step)),
            )

    def list_agent_steps(self, run_id: str) -> list[AgentStep]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload FROM agent_steps WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [AgentStep.model_validate_json(row[0]) for row in rows]

    def save_research_brief(self, brief: ResearchBrief) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO research_briefs (brief_id, payload) VALUES (?, ?)",
                (brief.brief_id, _model_to_json(brief)),
            )

    def get_research_brief(self, brief_id: str) -> ResearchBrief:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM research_briefs WHERE brief_id = ?", (brief_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown research brief: {brief_id}")
        return ResearchBrief.model_validate_json(row[0])

    def save_mcp_server(self, descriptor: MCPServerDescriptor) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mcp_servers (name, payload) VALUES (?, ?)",
                (descriptor.name, _model_to_json(descriptor)),
            )

    def list_mcp_servers(self) -> list[MCPServerDescriptor]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT payload FROM mcp_servers ORDER BY name ASC").fetchall()
        return [MCPServerDescriptor.model_validate_json(row[0]) for row in rows]

    def get_mcp_server(self, name: str) -> MCPServerDescriptor:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM mcp_servers WHERE name = ?", (name,)).fetchone()
        if not row:
            raise KeyError(f"Unknown MCP server: {name}")
        return MCPServerDescriptor.model_validate_json(row[0])

    def save_tool_audit(self, audit: ToolAudit) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tool_audit (audit_id, run_id, created_at, payload) VALUES (?, ?, ?, ?)",
                (audit.audit_id, audit.run_id, audit.created_at.isoformat(), _model_to_json(audit)),
            )

    def list_tool_audit(self, run_id: str) -> list[ToolAudit]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload FROM tool_audit WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [ToolAudit.model_validate_json(row[0]) for row in rows]

    def save_trace_event(self, event: TraceEvent) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO trace_events (event_id, run_id, trace_id, created_at, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.run_id,
                    event.trace_id,
                    event.created_at.isoformat(),
                    _model_to_json(event),
                ),
            )

    def list_trace_events(self, run_id: str) -> list[TraceEvent]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload FROM trace_events WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [TraceEvent.model_validate_json(row[0]) for row in rows]

    def save_artifact_ref(self, artifact: ArtifactRef) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO artifact_refs (artifact_id, owner_type, owner_id, created_at, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    artifact.artifact_id,
                    artifact.owner_type.value if hasattr(artifact.owner_type, "value") else str(artifact.owner_type),
                    artifact.owner_id,
                    artifact.created_at.isoformat(),
                    _model_to_json(artifact),
                ),
            )

    def list_artifact_refs(self, owner_type: str, owner_id: str) -> list[ArtifactRef]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload FROM artifact_refs WHERE owner_type = ? AND owner_id = ? ORDER BY created_at ASC",
                (owner_type, owner_id),
            ).fetchall()
        return [ArtifactRef.model_validate_json(row[0]) for row in rows]

    def save_job(self, job: JobRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO jobs (job_id, owner_type, owner_id, kind, status, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.owner_type.value if hasattr(job.owner_type, "value") else str(job.owner_type),
                    job.owner_id,
                    job.kind.value if hasattr(job.kind, "value") else str(job.kind),
                    job.status.value if hasattr(job.status, "value") else str(job.status),
                    job.updated_at.isoformat(),
                    _model_to_json(job),
                ),
            )

    def get_job(self, job_id: str) -> JobRecord:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown job: {job_id}")
        return JobRecord.model_validate_json(row[0])

    def update_job(self, job_id: str, **updates) -> JobRecord:
        job = self.get_job(job_id)
        updated = job.model_copy(update={**updates, "updated_at": _utc_now()})
        self.save_job(updated)
        return updated

    def requeue_inflight_jobs(self, kind: str | None = None) -> None:
        query = "SELECT payload FROM jobs WHERE status = ?"
        params: list[Any] = [JobStatus.running.value]
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        for (payload,) in rows:
            job = JobRecord.model_validate_json(payload)
            recovered_payload = dict(job.result_payload)
            recovered_payload["recovered_after_restart"] = True
            updated = job.model_copy(
                update={
                    "status": JobStatus.queued,
                    "result_payload": recovered_payload,
                    "updated_at": _utc_now(),
                }
            )
            self.save_job(updated)

    def claim_next_job(self, kind: str | None = None) -> JobRecord | None:
        query = "SELECT job_id, payload FROM jobs WHERE status = ?"
        params: list[Any] = [JobStatus.queued.value]
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY updated_at ASC LIMIT 1"
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(query, tuple(params)).fetchone()
            if not row:
                return None
        job_id, payload = row
        job = JobRecord.model_validate_json(payload)
        updated = job.model_copy(update={"status": JobStatus.running, "updated_at": _utc_now()})
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?, payload = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    JobStatus.running.value,
                    updated.updated_at.isoformat(),
                    _model_to_json(updated),
                    job_id,
                    JobStatus.queued.value,
                ),
            )
            if result.rowcount != 1:
                return None
        return updated

    def save_worker_heartbeat(self, heartbeat: WorkerHeartbeat) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO worker_heartbeats (worker_id, worker_kind, updated_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    heartbeat.worker_id,
                    heartbeat.worker_kind,
                    heartbeat.updated_at.isoformat(),
                    _model_to_json(heartbeat),
                ),
            )

    def list_worker_heartbeats(self, worker_kind: str | None = None) -> list[WorkerHeartbeat]:
        query = "SELECT payload FROM worker_heartbeats"
        params: list[Any] = []
        if worker_kind:
            query += " WHERE worker_kind = ?"
            params.append(worker_kind)
        query += " ORDER BY updated_at DESC"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [WorkerHeartbeat.model_validate_json(row[0]) for row in rows]


class PostgresStateStore(StateStore):
    _SCHEMA_BOOTSTRAP_LOCK_KEY = 6_412_907_311_884_221_057

    def __init__(self, dsn: str):
        if psycopg is None:  # pragma: no cover - dependency availability
            raise RuntimeError("Postgres state store requested but 'psycopg[binary]' is not installed.")
        self.dsn = dsn

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def initialize(self) -> None:
        with self._connect() as conn:
            # Concurrent API/worker startup can race on CREATE TABLE IF NOT EXISTS in
            # PostgreSQL. A transaction-scoped advisory lock keeps bootstrap single-file.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (self._SCHEMA_BOOTSTRAP_LOCK_KEY,))
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS studies (
                    study_id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    study_id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_briefs (
                    brief_id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    name TEXT PRIMARY KEY,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_audit (
                    audit_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_refs (
                    artifact_id TEXT PRIMARY KEY,
                    owner_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pg_jobs_status_kind_updated ON jobs (status, kind, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pg_campaigns_status_updated ON campaigns (status, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pg_programs_status_updated ON programs (status, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pg_agent_runs_status_updated ON agent_runs (status, updated_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_heartbeats (
                    worker_id TEXT PRIMARY KEY,
                    worker_kind TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pg_worker_heartbeats_kind_updated ON worker_heartbeats (worker_kind, updated_at)"
            )
            conn.commit()

    def _upsert_payload(self, table: str, pk_col: str, pk_value: str, payload: Any, *, extra: dict[str, Any] | None = None) -> None:
        extra = extra or {}
        columns = [pk_col, *extra.keys(), "payload"]
        values = [pk_value, *extra.values(), json.dumps(payload)]
        updates = [f"{column} = EXCLUDED.{column}" for column in columns[1:]]
        placeholders = ", ".join(["%s"] * (len(values) - 1) + ["%s::jsonb"])
        sql = f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT ({pk_col}) DO UPDATE
            SET {", ".join(updates)}
        """
        with self._connect() as conn:
            conn.execute(sql, values)
            conn.commit()

    def _fetch_payload(self, table: str, where_col: str, where_value: str) -> Any:
        with self._connect() as conn:
            row = conn.execute(f"SELECT payload FROM {table} WHERE {where_col} = %s", (where_value,)).fetchone()
        if not row:
            raise KeyError(f"Unknown {table.rstrip('s')}: {where_value}")
        return row["payload"]

    def save_study(self, record: StudyRecord) -> None:
        self._upsert_payload("studies", "study_id", record.study_id, record.model_dump(mode="json"))

    def get_study(self, study_id: str) -> StudyRecord:
        try:
            return _payload_to_model(StudyRecord, self._fetch_payload("studies", "study_id", study_id))
        except KeyError as exc:
            raise KeyError(f"Unknown study_id: {study_id}") from exc

    def save_analysis(self, result: AnalysisResult) -> None:
        self._upsert_payload("analyses", "study_id", result.study_id, result.model_dump(mode="json"))

    def get_analysis(self, study_id: str) -> AnalysisResult:
        try:
            return _payload_to_model(AnalysisResult, self._fetch_payload("analyses", "study_id", study_id))
        except KeyError as exc:
            raise KeyError(f"No analysis for study_id: {study_id}") from exc

    def save_benchmark_run(self, run: BenchmarkRun) -> None:
        self._upsert_payload("benchmark_runs", "run_id", run.run_id, run.model_dump(mode="json"))

    def get_benchmark_run(self, run_id: str) -> BenchmarkRun:
        try:
            return _payload_to_model(BenchmarkRun, self._fetch_payload("benchmark_runs", "run_id", run_id))
        except KeyError as exc:
            raise KeyError(f"Unknown benchmark run: {run_id}") from exc

    def save_campaign(self, campaign: CampaignRecord) -> None:
        self._upsert_payload(
            "campaigns",
            "campaign_id",
            campaign.campaign_id,
            campaign.model_dump(mode="json"),
            extra={
                "status": campaign.status.value if hasattr(campaign.status, "value") else str(campaign.status),
                "updated_at": campaign.updated_at,
            },
        )

    def get_campaign(self, campaign_id: str) -> CampaignRecord:
        try:
            return _payload_to_model(CampaignRecord, self._fetch_payload("campaigns", "campaign_id", campaign_id))
        except KeyError as exc:
            raise KeyError(f"Unknown campaign: {campaign_id}") from exc

    def list_campaigns(self) -> list[CampaignRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM campaigns ORDER BY updated_at DESC").fetchall()
        return [_payload_to_model(CampaignRecord, row["payload"]) for row in rows]

    def save_program(self, program: ProgramRecord) -> None:
        self._upsert_payload(
            "programs",
            "program_id",
            program.program_id,
            program.model_dump(mode="json"),
            extra={
                "status": program.status.value if hasattr(program.status, "value") else str(program.status),
                "updated_at": program.updated_at,
            },
        )

    def get_program(self, program_id: str) -> ProgramRecord:
        try:
            return _payload_to_model(ProgramRecord, self._fetch_payload("programs", "program_id", program_id))
        except KeyError as exc:
            raise KeyError(f"Unknown program: {program_id}") from exc

    def list_programs(self) -> list[ProgramRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM programs ORDER BY updated_at DESC").fetchall()
        return [_payload_to_model(ProgramRecord, row["payload"]) for row in rows]

    def save_agent_run(self, run: AgentRun) -> None:
        self._upsert_payload(
            "agent_runs",
            "run_id",
            run.run_id,
            run.model_dump(mode="json"),
            extra={
                "status": run.status.value if hasattr(run.status, "value") else str(run.status),
                "updated_at": run.updated_at,
            },
        )

    def create_agent_run(self, run: AgentRun) -> AgentRun:
        self.save_agent_run(run)
        return run

    def get_agent_run(self, run_id: str) -> AgentRun:
        try:
            return _payload_to_model(AgentRun, self._fetch_payload("agent_runs", "run_id", run_id))
        except KeyError as exc:
            raise KeyError(f"Unknown agent run: {run_id}") from exc

    def update_agent_run(self, run_id: str, **updates) -> AgentRun:
        run = self.get_agent_run(run_id)
        updated = run.model_copy(update={**updates, "updated_at": _utc_now()})
        self.save_agent_run(updated)
        return updated

    def requeue_inflight_agent_runs(self) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM agent_runs WHERE status = %s",
                (AgentRunStatus.running.value,),
            ).fetchall()
            for row in rows:
                run = _payload_to_model(AgentRun, row["payload"])
                warnings = list(run.warnings)
                warnings.append("Recovered queued agent run after process restart.")
                updated = run.model_copy(
                    update={
                        "status": AgentRunStatus.queued,
                        "warnings": warnings,
                        "updated_at": _utc_now(),
                    }
                )
                conn.execute(
                    """
                    UPDATE agent_runs
                    SET status = %s, updated_at = %s, payload = %s::jsonb
                    WHERE run_id = %s
                    """,
                    (
                        AgentRunStatus.queued.value,
                        updated.updated_at,
                        json.dumps(updated.model_dump(mode="json")),
                        updated.run_id,
                    ),
                )
            conn.commit()

    def claim_next_agent_run(self) -> AgentRun | None:
        with self._connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT run_id, payload
                    FROM agent_runs
                    WHERE status = %s
                    ORDER BY updated_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (AgentRunStatus.queued.value,),
                ).fetchone()
                if not row:
                    return None
                run = _payload_to_model(AgentRun, row["payload"])
                updated = run.model_copy(update={"status": AgentRunStatus.running, "updated_at": _utc_now()})
                conn.execute(
                    """
                    UPDATE agent_runs
                    SET status = %s, updated_at = %s, payload = %s::jsonb
                    WHERE run_id = %s
                    """,
                    (
                        AgentRunStatus.running.value,
                        updated.updated_at,
                        json.dumps(updated.model_dump(mode="json")),
                        updated.run_id,
                    ),
                )
                return updated

    def save_agent_step(self, step: AgentStep) -> None:
        self._upsert_payload(
            "agent_steps",
            "step_id",
            step.step_id,
            step.model_dump(mode="json"),
            extra={"run_id": step.run_id, "created_at": step.created_at},
        )

    def list_agent_steps(self, run_id: str) -> list[AgentStep]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM agent_steps WHERE run_id = %s ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [_payload_to_model(AgentStep, row["payload"]) for row in rows]

    def save_research_brief(self, brief: ResearchBrief) -> None:
        self._upsert_payload("research_briefs", "brief_id", brief.brief_id, brief.model_dump(mode="json"))

    def get_research_brief(self, brief_id: str) -> ResearchBrief:
        try:
            return _payload_to_model(ResearchBrief, self._fetch_payload("research_briefs", "brief_id", brief_id))
        except KeyError as exc:
            raise KeyError(f"Unknown research brief: {brief_id}") from exc

    def save_mcp_server(self, descriptor: MCPServerDescriptor) -> None:
        self._upsert_payload("mcp_servers", "name", descriptor.name, descriptor.model_dump(mode="json"))

    def list_mcp_servers(self) -> list[MCPServerDescriptor]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM mcp_servers ORDER BY name ASC").fetchall()
        return [_payload_to_model(MCPServerDescriptor, row["payload"]) for row in rows]

    def get_mcp_server(self, name: str) -> MCPServerDescriptor:
        try:
            return _payload_to_model(MCPServerDescriptor, self._fetch_payload("mcp_servers", "name", name))
        except KeyError as exc:
            raise KeyError(f"Unknown MCP server: {name}") from exc

    def save_tool_audit(self, audit: ToolAudit) -> None:
        self._upsert_payload(
            "tool_audit",
            "audit_id",
            audit.audit_id,
            audit.model_dump(mode="json"),
            extra={"run_id": audit.run_id, "created_at": audit.created_at},
        )

    def list_tool_audit(self, run_id: str) -> list[ToolAudit]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM tool_audit WHERE run_id = %s ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [_payload_to_model(ToolAudit, row["payload"]) for row in rows]

    def save_trace_event(self, event: TraceEvent) -> None:
        self._upsert_payload(
            "trace_events",
            "event_id",
            event.event_id,
            event.model_dump(mode="json"),
            extra={"run_id": event.run_id, "trace_id": event.trace_id, "created_at": event.created_at},
        )

    def list_trace_events(self, run_id: str) -> list[TraceEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM trace_events WHERE run_id = %s ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [_payload_to_model(TraceEvent, row["payload"]) for row in rows]

    def save_artifact_ref(self, artifact: ArtifactRef) -> None:
        self._upsert_payload(
            "artifact_refs",
            "artifact_id",
            artifact.artifact_id,
            artifact.model_dump(mode="json"),
            extra={
                "owner_type": artifact.owner_type.value if hasattr(artifact.owner_type, "value") else str(artifact.owner_type),
                "owner_id": artifact.owner_id,
                "created_at": artifact.created_at,
            },
        )

    def list_artifact_refs(self, owner_type: str, owner_id: str) -> list[ArtifactRef]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload
                FROM artifact_refs
                WHERE owner_type = %s AND owner_id = %s
                ORDER BY created_at ASC
                """,
                (owner_type, owner_id),
            ).fetchall()
        return [_payload_to_model(ArtifactRef, row["payload"]) for row in rows]

    def save_job(self, job: JobRecord) -> None:
        self._upsert_payload(
            "jobs",
            "job_id",
            job.job_id,
            job.model_dump(mode="json"),
            extra={
                "owner_type": job.owner_type.value if hasattr(job.owner_type, "value") else str(job.owner_type),
                "owner_id": job.owner_id,
                "kind": job.kind.value if hasattr(job.kind, "value") else str(job.kind),
                "status": job.status.value if hasattr(job.status, "value") else str(job.status),
                "updated_at": job.updated_at,
            },
        )

    def get_job(self, job_id: str) -> JobRecord:
        try:
            return _payload_to_model(JobRecord, self._fetch_payload("jobs", "job_id", job_id))
        except KeyError as exc:
            raise KeyError(f"Unknown job: {job_id}") from exc

    def update_job(self, job_id: str, **updates) -> JobRecord:
        job = self.get_job(job_id)
        updated = job.model_copy(update={**updates, "updated_at": _utc_now()})
        self.save_job(updated)
        return updated

    def requeue_inflight_jobs(self, kind: str | None = None) -> None:
        query = "SELECT payload FROM jobs WHERE status = %s"
        params: list[Any] = [JobStatus.running.value]
        if kind:
            query += " AND kind = %s"
            params.append(kind)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            for row in rows:
                job = _payload_to_model(JobRecord, row["payload"])
                recovered_payload = dict(job.result_payload)
                recovered_payload["recovered_after_restart"] = True
                updated = job.model_copy(
                    update={
                        "status": JobStatus.queued,
                        "result_payload": recovered_payload,
                        "updated_at": _utc_now(),
                    }
                )
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = %s, updated_at = %s, payload = %s::jsonb
                    WHERE job_id = %s
                    """,
                    (
                        JobStatus.queued.value,
                        updated.updated_at,
                        json.dumps(updated.model_dump(mode="json")),
                        updated.job_id,
                    ),
                )
            conn.commit()

    def claim_next_job(self, kind: str | None = None) -> JobRecord | None:
        with self._connect() as conn:
            with conn.transaction():
                if kind:
                    row = conn.execute(
                        """
                        SELECT job_id, payload
                        FROM jobs
                        WHERE status = %s AND kind = %s
                        ORDER BY updated_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                        """,
                        (JobStatus.queued.value, kind),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT job_id, payload
                        FROM jobs
                        WHERE status = %s
                        ORDER BY updated_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                        """,
                        (JobStatus.queued.value,),
                    ).fetchone()
                if not row:
                    return None
                job = _payload_to_model(JobRecord, row["payload"])
                updated = job.model_copy(update={"status": JobStatus.running, "updated_at": _utc_now()})
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = %s, updated_at = %s, payload = %s::jsonb
                    WHERE job_id = %s
                    """,
                    (
                        JobStatus.running.value,
                        updated.updated_at,
                        json.dumps(updated.model_dump(mode="json")),
                        updated.job_id,
                    ),
                )
                return updated

    def save_worker_heartbeat(self, heartbeat: WorkerHeartbeat) -> None:
        self._upsert_payload(
            "worker_heartbeats",
            "worker_id",
            heartbeat.worker_id,
            heartbeat.model_dump(mode="json"),
            extra={"worker_kind": heartbeat.worker_kind, "updated_at": heartbeat.updated_at},
        )

    def list_worker_heartbeats(self, worker_kind: str | None = None) -> list[WorkerHeartbeat]:
        with self._connect() as conn:
            if worker_kind:
                rows = conn.execute(
                    """
                    SELECT payload FROM worker_heartbeats
                    WHERE worker_kind = %s
                    ORDER BY updated_at DESC
                    """,
                    (worker_kind,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload FROM worker_heartbeats ORDER BY updated_at DESC"
                ).fetchall()
        return [_payload_to_model(WorkerHeartbeat, row["payload"]) for row in rows]
