from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from sounio_stroke_lab.schemas import (
    AnalysisResult,
    ArtifactRecord,
    BenchmarkRun,
    RunEvent,
    RunRecord,
    StepRecord,
    StudyRecord,
    StudyStatus,
)


class StorageManager:
    def __init__(self, root: Path):
        self.root = root
        self.db_path = self.root / "state.sqlite3"
        self.study_dir = self.root / "studies"
        self.artifact_dir = self.root / "artifacts"
        self.run_dir = self.root / "runs"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.study_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_steps (
                    step_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO studies (study_id, payload) VALUES (?, ?)",
                (record.study_id, record.model_dump_json()),
            )

    def update_study_status(self, study_id: str, status: StudyStatus, warnings: list[str] | None = None) -> StudyRecord:
        record = self.get_study(study_id)
        updated = record.model_copy(update={"status": status, "warnings": warnings or record.warnings})
        self.save_study(updated)
        return updated

    def get_study(self, study_id: str) -> StudyRecord:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM studies WHERE study_id = ?", (study_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown study_id: {study_id}")
        return StudyRecord.model_validate_json(row[0])

    def allocate_study_dir(self, study_id: str) -> Path:
        path = self.study_dir / study_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_analysis(self, result: AnalysisResult) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO analyses (study_id, payload) VALUES (?, ?)",
                (result.study_id, result.model_dump_json()),
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
                (run.run_id, run.model_dump_json()),
            )

    def get_benchmark_run(self, run_id: str) -> BenchmarkRun:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM benchmark_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown benchmark run: {run_id}")
        return BenchmarkRun.model_validate_json(row[0])

    def write_artifact_text(self, relative_path: str, content: str) -> str:
        path = self.artifact_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def write_artifact_json(self, relative_path: str, payload: dict) -> str:
        path = self.artifact_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def allocate_run_root(self, workspace_path: Path, run_id: str) -> Path:
        path = workspace_path / ".lab" / "runs" / run_id
        (path / "artifacts").mkdir(parents=True, exist_ok=True)
        (path / "checkpoints").mkdir(parents=True, exist_ok=True)
        (path / "logs").mkdir(parents=True, exist_ok=True)
        return path

    def save_run(self, record: RunRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, payload) VALUES (?, ?)",
                (record.run_id, record.model_dump_json()),
            )

    def get_run(self, run_id: str) -> RunRecord:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown run: {run_id}")
        return RunRecord.model_validate_json(row[0])

    def save_run_step(self, step: StepRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO run_steps (step_id, run_id, seq, payload) VALUES (?, ?, ?, ?)",
                (step.step_id, step.run_id, step.seq, step.model_dump_json()),
            )

    def list_run_steps(self, run_id: str) -> list[StepRecord]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT payload FROM run_steps WHERE run_id = ? ORDER BY seq ASC",
                (run_id,),
            ).fetchall()
        return [StepRecord.model_validate_json(row[0]) for row in rows]

    def save_run_artifact(self, artifact: ArtifactRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_artifacts (artifact_id, run_id, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (artifact.artifact_id, artifact.run_id, artifact.created_at.isoformat(), artifact.model_dump_json()),
            )

    def register_run_artifact(
        self,
        run_id: str,
        step_id: str | None,
        kind: str,
        path: Path,
        content_type: str,
        is_checkpoint: bool = False,
        preview_text: str | None = None,
    ) -> ArtifactRecord:
        payload = path.read_bytes()
        artifact = ArtifactRecord(
            artifact_id=uuid.uuid4().hex,
            run_id=run_id,
            step_id=step_id,
            kind=kind,
            uri=str(path),
            content_type=content_type,
            size_bytes=len(payload),
            sha256=sha256(payload).hexdigest(),
            is_checkpoint=is_checkpoint,
            preview_text=preview_text,
        )
        self.save_run_artifact(artifact)
        return artifact

    def list_run_artifacts(self, run_id: str, limit: int | None = None) -> list[ArtifactRecord]:
        query = "SELECT payload FROM run_artifacts WHERE run_id = ? ORDER BY created_at DESC"
        params: tuple[object, ...] = (run_id,)
        if limit is not None:
            query = f"{query} LIMIT ?"
            params = (run_id, limit)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [ArtifactRecord.model_validate_json(row[0]) for row in rows]

    def append_run_event(
        self,
        run_id: str,
        event_type: str,
        message: str,
        step_id: str | None = None,
        payload: dict | None = None,
    ) -> RunEvent:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            next_seq = int(row[0]) + 1
            event = RunEvent(
                event_id=uuid.uuid4().hex,
                run_id=run_id,
                seq=next_seq,
                step_id=step_id,
                event_type=event_type,
                message=message,
                payload=payload or {},
            )
            conn.execute(
                "INSERT INTO run_events (event_id, run_id, seq, payload) VALUES (?, ?, ?, ?)",
                (event.event_id, run_id, next_seq, event.model_dump_json()),
            )
        return event

    def list_run_events(self, run_id: str, limit: int | None = None) -> list[RunEvent]:
        query = "SELECT payload FROM run_events WHERE run_id = ? ORDER BY seq DESC"
        params: tuple[object, ...] = (run_id,)
        if limit is not None:
            query = f"{query} LIMIT ?"
            params = (run_id, limit)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        events = [RunEvent.model_validate_json(row[0]) for row in rows]
        return list(reversed(events))
