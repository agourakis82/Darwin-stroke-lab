from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from sounio_stroke_lab.schemas import AnalysisResult, BenchmarkRun, StudyRecord, StudyStatus


class StorageManager:
    def __init__(self, root: Path):
        self.root = root
        self.db_path = self.root / "state.sqlite3"
        self.study_dir = self.root / "studies"
        self.artifact_dir = self.root / "artifacts"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.study_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
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
