from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

from sounio_stroke_lab.schemas import (
    ArtifactRecord,
    ExecutorKind,
    RunRecord,
    RunStatus,
    RunSubmitRequest,
    StepRecord,
    StepStatus,
)
from sounio_stroke_lab.storage import StorageManager


class RunEngine:
    def __init__(self, storage: StorageManager):
        self.storage = storage
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def submit(self, request: RunSubmitRequest) -> RunRecord:
        workspace_path = Path(request.workspace_path).expanduser().resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)

        repo_path = Path(request.repo_path).expanduser()
        if not repo_path.is_absolute():
            repo_path = (workspace_path / repo_path).resolve()
        else:
            repo_path = repo_path.resolve()
        if not repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")
        if not repo_path.is_dir():
            raise ValueError(f"Repository path must be a directory: {repo_path}")

        workspace_id = request.workspace_id or workspace_path.name or "workspace"
        run_id = uuid.uuid4().hex
        run_root = self.storage.allocate_run_root(workspace_path, run_id)
        record = RunRecord(
            run_id=run_id,
            workspace_id=workspace_id,
            user_id=request.user_id,
            workflow_kind=request.workflow_kind,
            status=RunStatus.pending,
            current_step_seq=0,
            temporal_workflow_id=f"generic-agent-task-{run_id}",
            event_stream=f"runs.{run_id}",
            run_root_uri=str(run_root),
            workspace_uri=str(workspace_path),
            repo_path=str(repo_path),
            task_name=request.task_name,
            parameters=request.parameters,
            resume_token=uuid.uuid4().hex,
        )
        self.storage.save_run(record)
        for step in self._initial_steps(run_id):
            self.storage.save_run_step(step)
        self.storage.append_run_event(
            run_id=run_id,
            event_type="run.submitted",
            message="Run accepted and queued for execution.",
            payload={
                "task_name": request.task_name,
                "workspace_id": workspace_id,
                "repo_path": str(repo_path),
            },
        )

        thread = threading.Thread(
            target=self._execute_run,
            name=f"run-{run_id[:8]}",
            args=(run_id,),
            daemon=True,
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()
        return record

    def _initial_steps(self, run_id: str) -> list[StepRecord]:
        return [
            StepRecord(
                step_id=uuid.uuid4().hex,
                run_id=run_id,
                seq=1,
                name="capture_workspace_context",
                executor_kind=ExecutorKind.workspace,
                status=StepStatus.pending,
                resume_hint="Reconnect to the workspace and inspect context/workspace-context.json.",
            ),
            StepRecord(
                step_id=uuid.uuid4().hex,
                run_id=run_id,
                seq=2,
                name="run_agent_task",
                executor_kind=ExecutorKind.agent,
                status=StepStatus.pending,
                resume_hint="Resume by reading artifacts/task/agent-output.json and the latest checkpoint.",
            ),
            StepRecord(
                step_id=uuid.uuid4().hex,
                run_id=run_id,
                seq=3,
                name="write_final_summary",
                executor_kind=ExecutorKind.system,
                status=StepStatus.pending,
                resume_hint="Resume from the generated summary and recent events.",
            ),
        ]

    def _execute_run(self, run_id: str) -> None:
        try:
            now = self.storage.now()
            run = self.storage.get_run(run_id)
            run = run.model_copy(
                update={
                    "status": RunStatus.running,
                    "started_at": run.started_at or now,
                    "last_heartbeat_at": now,
                }
            )
            self.storage.save_run(run)
            self.storage.append_run_event(
                run_id=run_id,
                event_type="run.started",
                message="Run execution started.",
            )

            for step in self.storage.list_run_steps(run_id):
                run, step = self._start_step(run, step)
                if step.seq == 1:
                    step = self._capture_workspace_context(run, step)
                elif step.seq == 2:
                    step = self._run_agent_task(run, step)
                elif step.seq == 3:
                    step = self._write_final_summary(run, step)
                step = step.model_copy(
                    update={
                        "status": StepStatus.completed,
                        "ended_at": self.storage.now(),
                        "last_heartbeat_at": self.storage.now(),
                    }
                )
                self.storage.save_run_step(step)
                self.storage.append_run_event(
                    run_id=run_id,
                    step_id=step.step_id,
                    event_type="step.completed",
                    message=f"Step {step.seq} completed.",
                    payload={"step_name": step.name},
                )

            run = self.storage.get_run(run_id).model_copy(
                update={
                    "status": RunStatus.completed,
                    "current_step_seq": 3,
                    "last_heartbeat_at": self.storage.now(),
                    "failure_reason": None,
                }
            )
            self.storage.save_run(run)
            self.storage.append_run_event(
                run_id=run_id,
                event_type="run.completed",
                message="Run completed successfully.",
            )
        except Exception as exc:
            self._fail_run(run_id, exc)
        finally:
            with self._lock:
                self._threads.pop(run_id, None)

    def _start_step(self, run: RunRecord, step: StepRecord) -> tuple[RunRecord, StepRecord]:
        now = self.storage.now()
        updated_step = step.model_copy(
            update={
                "status": StepStatus.running,
                "attempt": step.attempt + 1,
                "started_at": step.started_at or now,
                "last_heartbeat_at": now,
            }
        )
        self.storage.save_run_step(updated_step)
        updated_run = run.model_copy(
            update={
                "status": RunStatus.running,
                "current_step_seq": step.seq,
                "last_heartbeat_at": now,
            }
        )
        self.storage.save_run(updated_run)
        self.storage.append_run_event(
            run_id=run.run_id,
            step_id=step.step_id,
            event_type="step.started",
            message=f"Step {step.seq} started.",
            payload={"step_name": step.name},
        )
        return updated_run, updated_step

    def _capture_workspace_context(self, run: RunRecord, step: StepRecord) -> StepRecord:
        repo_path = Path(run.repo_path)
        workspace_path = Path(run.workspace_uri)
        top_level_entries = sorted(item.name for item in repo_path.iterdir())[:50]
        payload = {
            "workspace_path": str(workspace_path),
            "repo_path": str(repo_path),
            "task_name": run.task_name,
            "parameters": run.parameters,
            "top_level_entries": top_level_entries,
        }
        artifact = self._write_json_artifact(
            run=run,
            step=step,
            relative_path="artifacts/context/workspace-context.json",
            payload=payload,
            kind="workspace-context",
            preview_text=f"Top-level entries: {', '.join(top_level_entries[:5])}" if top_level_entries else "Empty repo root.",
        )
        checkpoint = self._write_json_artifact(
            run=run,
            step=step,
            relative_path="checkpoints/step-1.json",
            payload={"status": "captured", "artifact_id": artifact.artifact_id},
            kind="checkpoint",
            is_checkpoint=True,
            preview_text="Workspace context captured.",
        )
        return step.model_copy(
            update={
                "checkpoint_uri": checkpoint.uri,
                "resume_hint": "Inspect workspace-context.json before continuing.",
            }
        )

    def _run_agent_task(self, run: RunRecord, step: StepRecord) -> StepRecord:
        repo_path = Path(run.repo_path)
        files = [path for path in repo_path.rglob("*") if path.is_file()]
        files = sorted(files)
        fingerprint_source = "\n".join(
            f"{path.relative_to(repo_path)}:{path.stat().st_size}:{int(path.stat().st_mtime)}" for path in files[:200]
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        simulate_delay_seconds = min(float(run.parameters.get("simulate_delay_seconds", 0.0)), 5.0)
        if simulate_delay_seconds > 0:
            deadline = time.time() + simulate_delay_seconds
            while time.time() < deadline:
                time.sleep(min(0.25, deadline - time.time()))
                self._heartbeat(run.run_id, step.step_id)

        agent_payload = {
            "task_name": run.task_name,
            "repo_path": str(repo_path),
            "file_count": len(files),
            "sample_files": [str(path.relative_to(repo_path)) for path in files[:50]],
            "parameters": run.parameters,
            "fingerprint": fingerprint,
        }
        result_artifact = self._write_json_artifact(
            run=run,
            step=step,
            relative_path="artifacts/task/agent-output.json",
            payload=agent_payload,
            kind="task-output",
            preview_text=f"Task {run.task_name} touched {len(files)} files.",
        )
        stdout_artifact = self._write_text_artifact(
            run=run,
            step=step,
            relative_path="logs/step-2.stdout.log",
            content=(
                f"task={run.task_name}\n"
                f"repo={repo_path}\n"
                f"file_count={len(files)}\n"
                f"fingerprint={fingerprint}\n"
            ),
            kind="stdout-log",
            content_type="text/plain",
            preview_text=f"file_count={len(files)}",
        )
        stderr_artifact = self._write_text_artifact(
            run=run,
            step=step,
            relative_path="logs/step-2.stderr.log",
            content="",
            kind="stderr-log",
            content_type="text/plain",
            preview_text="No stderr output.",
        )
        checkpoint = self._write_json_artifact(
            run=run,
            step=step,
            relative_path="checkpoints/step-2.json",
            payload={"status": "task-finished", "artifact_id": result_artifact.artifact_id},
            kind="checkpoint",
            is_checkpoint=True,
            preview_text="Agent task completed.",
        )
        return step.model_copy(
            update={
                "checkpoint_uri": checkpoint.uri,
                "stdout_artifact_id": stdout_artifact.artifact_id,
                "stderr_artifact_id": stderr_artifact.artifact_id,
                "resume_hint": "Resume from agent-output.json and the step-2 checkpoint.",
            }
        )

    def _write_final_summary(self, run: RunRecord, step: StepRecord) -> StepRecord:
        artifacts = self.storage.list_run_artifacts(run.run_id, limit=10)
        events = self.storage.list_run_events(run.run_id, limit=10)
        summary_lines = [
            f"# Run {run.run_id}",
            "",
            f"- workspace: `{run.workspace_uri}`",
            f"- repo: `{run.repo_path}`",
            f"- task: `{run.task_name}`",
            f"- event stream: `{run.event_stream}`",
            "",
            "## Recent Artifacts",
        ]
        summary_lines.extend(f"- {artifact.kind}: `{artifact.uri}`" for artifact in artifacts)
        summary_lines.extend(["", "## Recent Events"])
        summary_lines.extend(f"- {event.seq}. {event.event_type}: {event.message}" for event in events)
        summary_lines.extend(
            [
                "",
                "## Resume",
                f"- Run `labctl run resume {run.run_id}` to fetch the latest summary.",
                "- Re-open the workspace and inspect `.lab/runs/<run_id>/artifacts` for durable outputs.",
            ]
        )
        artifact = self._write_text_artifact(
            run=run,
            step=step,
            relative_path="artifacts/summary/resume-summary.md",
            content="\n".join(summary_lines) + "\n",
            kind="resume-summary",
            content_type="text/markdown",
            preview_text=f"Summary for {run.task_name}",
        )
        return step.model_copy(
            update={
                "checkpoint_uri": artifact.uri,
                "resume_hint": "Use the generated resume summary as the primary reconnect surface.",
            }
        )

    def _fail_run(self, run_id: str, exc: Exception) -> None:
        try:
            run = self.storage.get_run(run_id)
        except KeyError:
            return
        now = self.storage.now()
        active_step = None
        for step in self.storage.list_run_steps(run_id):
            if step.status == StepStatus.running:
                active_step = step
        if active_step is not None:
            failed_step = active_step.model_copy(
                update={
                    "status": StepStatus.failed,
                    "ended_at": now,
                    "last_heartbeat_at": now,
                    "error_code": exc.__class__.__name__,
                    "resume_hint": f"Inspect error and rerun from step {active_step.seq}.",
                }
            )
            self.storage.save_run_step(failed_step)
        failed_run = run.model_copy(
            update={
                "status": RunStatus.failed,
                "last_heartbeat_at": now,
                "failure_reason": str(exc),
            }
        )
        self.storage.save_run(failed_run)
        self.storage.append_run_event(
            run_id=run_id,
            step_id=active_step.step_id if active_step else None,
            event_type="run.failed",
            message=str(exc),
            payload={"error_code": exc.__class__.__name__},
        )

    def _heartbeat(self, run_id: str, step_id: str) -> None:
        now = self.storage.now()
        run = self.storage.get_run(run_id).model_copy(update={"last_heartbeat_at": now})
        self.storage.save_run(run)
        for step in self.storage.list_run_steps(run_id):
            if step.step_id == step_id:
                self.storage.save_run_step(step.model_copy(update={"last_heartbeat_at": now}))
                break

    def _write_text_artifact(
        self,
        run: RunRecord,
        step: StepRecord,
        relative_path: str,
        content: str,
        kind: str,
        content_type: str,
        is_checkpoint: bool = False,
        preview_text: str | None = None,
    ) -> ArtifactRecord:
        path = Path(run.run_root_uri) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self.storage.register_run_artifact(
            run_id=run.run_id,
            step_id=step.step_id,
            kind=kind,
            path=path,
            content_type=content_type,
            is_checkpoint=is_checkpoint,
            preview_text=preview_text,
        )

    def _write_json_artifact(
        self,
        run: RunRecord,
        step: StepRecord,
        relative_path: str,
        payload: dict,
        kind: str,
        is_checkpoint: bool = False,
        preview_text: str | None = None,
    ) -> ArtifactRecord:
        path = Path(run.run_root_uri) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self.storage.register_run_artifact(
            run_id=run.run_id,
            step_id=step.step_id,
            kind=kind,
            path=path,
            content_type="application/json",
            is_checkpoint=is_checkpoint,
            preview_text=preview_text,
        )
