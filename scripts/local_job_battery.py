from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    workers: int
    timeout_seconds: float
    description: str


PROFILES: dict[str, ProfileSpec] = {
    "stress-light": ProfileSpec(
        name="stress-light",
        workers=1,
        timeout_seconds=300.0,
        description="Five queued benchmark jobs on the compact mini fixture with a single worker.",
    ),
    "stress-medium": ProfileSpec(
        name="stress-medium",
        workers=2,
        timeout_seconds=600.0,
        description="Four larger benchmark jobs on the full local fixture with two workers.",
    ),
    "stress-heavy": ProfileSpec(
        name="stress-heavy",
        workers=3,
        timeout_seconds=900.0,
        description="Eight larger benchmark jobs on the full local fixture with three workers.",
    ),
    "agentic-e2e": ProfileSpec(
        name="agentic-e2e",
        workers=1,
        timeout_seconds=420.0,
        description="One dual-surface agent run that exercises research + clinical + benchmark orchestration.",
    ),
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 scripts/local_job_battery.py",
        description="Run a reproducible local job battery against the dev-local Postgres + MinIO stack.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        choices=sorted(PROFILES),
        help="Profile(s) to execute. Defaults to stress-light then stress-medium.",
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--compose-file", default="docker-compose.yml")
    parser.add_argument("--timeout", type=float, default=None, help="Override per-profile timeout.")
    parser.add_argument("--keep-running", action="store_true", help="Keep the compose stack running after the battery.")
    parser.add_argument("--build", action="store_true", help="Rebuild api/worker images before running.")
    return parser


def _run(cmd: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)


def _find_compose_cmd(cwd: Path) -> list[str]:
    docker = shutil.which("docker")
    if docker:
        probe = subprocess.run([docker, "compose", "version"], cwd=cwd, text=True, capture_output=True)
        if probe.returncode == 0:
            return [docker, "compose"]
    docker_compose = shutil.which("docker-compose")
    if docker_compose:
        probe = subprocess.run([docker_compose, "version"], cwd=cwd, text=True, capture_output=True)
        if probe.returncode == 0:
            return [docker_compose]
    raise RuntimeError("No working Docker Compose command was found.")


def _compose_project_name(repo_root: Path) -> str:
    return re.sub(r"[^a-z0-9]", "", repo_root.name.lower()) or "souniostrokelab"


def _ensure_docker_ready(cwd: Path) -> None:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI is not installed.")
    probe = subprocess.run([docker, "info"], cwd=cwd, text=True, capture_output=True)
    if probe.returncode != 0:
        detail = probe.stderr.strip() or probe.stdout.strip() or "Docker daemon is not available."
        raise RuntimeError(detail)


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_health(api_url: str, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            payload = _http_json("GET", f"{api_url}/health")
            if payload.get("status") == "ok":
                return
        except Exception:
            pass
        time.sleep(1.0)
    raise TimeoutError(f"API at {api_url} did not become healthy within {timeout} seconds.")


def _wait_for_terminal(api_url: str, path: str, timeout: float, terminal_statuses: set[str]) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = _http_json("GET", f"{api_url}{path}")
        status = str(payload.get("status", "")).lower()
        if status in terminal_statuses:
            return payload
        time.sleep(1.0)
    raise TimeoutError(f"{path} did not reach a terminal state within {timeout} seconds.")


def _host_to_workspace(host_path: Path, repo_root: Path) -> str:
    host_path = host_path.expanduser().resolve()
    return f"/workspace/{host_path.relative_to(repo_root).as_posix()}"


def _prepare_manifest(repo_root: Path, target_root: Path, mode: str) -> str:
    os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
    sys.path.insert(0, str(repo_root / "src"))
    sys.path.insert(0, str(repo_root))
    from tests.support import create_benchmark_manifest, create_small_benchmark_manifest  # type: ignore

    target_root.mkdir(parents=True, exist_ok=True)
    manifest_path = create_small_benchmark_manifest(target_root) if mode == "small" else create_benchmark_manifest(target_root)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for case in payload.get("cases", []):
        for field in ("volume_path", "lesion_mask_path"):
            raw_path = case.get(field)
            if not raw_path:
                continue
            case[field] = _host_to_workspace(Path(raw_path), repo_root)
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return _host_to_workspace(manifest_path, repo_root)


def _prepare_study_file(repo_root: Path, target_root: Path) -> str:
    os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")
    sys.path.insert(0, str(repo_root / "src"))
    sys.path.insert(0, str(repo_root))
    from tests.support import create_study_file  # type: ignore

    target_root.mkdir(parents=True, exist_ok=True)
    study_path = create_study_file(target_root)
    return _host_to_workspace(Path(study_path), repo_root)


def _parse_iso8601(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _parse_percent(value: str) -> float:
    cleaned = value.strip().replace("%", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_bytes(value: str) -> float:
    cleaned = value.strip()
    match = re.match(r"(?P<number>[0-9]*\.?[0-9]+)\s*(?P<unit>[KMGTP]?i?B)", cleaned)
    if not match:
        return 0.0
    number = float(match.group("number"))
    unit = match.group("unit")
    factors = {
        "B": 1,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "TiB": 1024**4,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
    }
    return number * factors.get(unit, 1)


class DockerStatsSampler:
    def __init__(self, repo_root: Path, *, interval_seconds: float = 2.0):
        self.repo_root = repo_root
        self.interval_seconds = interval_seconds
        self.project_prefix = f"{_compose_project_name(repo_root)}-"
        self._docker = shutil.which("docker")
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._samples: dict[str, dict[str, float]] = {}

    def start(self) -> None:
        if not self._docker or (self._thread and self._thread.is_alive()):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="local-job-battery-stats", daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, dict[str, float]]:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        return {
            name: {
                "max_cpu_percent": round(sample["max_cpu_percent"], 3),
                "max_mem_bytes": round(sample["max_mem_bytes"], 3),
                "max_mem_mib": round(sample["max_mem_bytes"] / (1024**2), 3),
                "max_mem_percent": round(sample["max_mem_percent"], 3),
            }
            for name, sample in sorted(self._samples.items())
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._sample_once()
            self._stop_event.wait(self.interval_seconds)

    def _sample_once(self) -> None:
        if not self._docker:
            return
        result = subprocess.run(
            [self._docker, "stats", "--no-stream", "--format", "{{json .}}"],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = str(payload.get("Name", ""))
            if not name.startswith(self.project_prefix):
                continue
            mem_usage = str(payload.get("MemUsage", "")).split("/", 1)[0].strip()
            sample = self._samples.setdefault(
                name,
                {"max_cpu_percent": 0.0, "max_mem_bytes": 0.0, "max_mem_percent": 0.0},
            )
            sample["max_cpu_percent"] = max(sample["max_cpu_percent"], _parse_percent(str(payload.get("CPUPerc", "0%"))))
            sample["max_mem_bytes"] = max(sample["max_mem_bytes"], _parse_bytes(mem_usage))
            sample["max_mem_percent"] = max(sample["max_mem_percent"], _parse_percent(str(payload.get("MemPerc", "0%"))))


def _job_summary(job: dict[str, Any]) -> dict[str, Any]:
    created_at = _parse_iso8601(job["created_at"])
    updated_at = _parse_iso8601(job["updated_at"])
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "queue_name": job.get("queue_name"),
        "executor": job.get("executor"),
        "duration_seconds": round((updated_at - created_at).total_seconds(), 3),
        "benchmark_run_id": job.get("result_payload", {}).get("benchmark_run_id"),
        "artifact_prefix": job.get("result_payload", {}).get("artifact_prefix"),
        "error": job.get("error"),
    }


def _run_stress_profile(
    api_url: str,
    manifest_path: str,
    profile: ProfileSpec,
    seeds: list[int],
    container_stats: dict[str, dict[str, float]],
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    for seed in seeds:
        submitted = _http_json(
            "POST",
            f"{api_url}/benchmark/jobs",
            {"dataset_manifest_path": manifest_path, "seed": seed},
        )
        jobs.append({"seed": seed, "submitted": submitted})

    completed: list[dict[str, Any]] = []
    for item in jobs:
        terminal = _wait_for_terminal(
            api_url,
            f"/jobs/{item['submitted']['job_id']}",
            timeout=profile.timeout_seconds,
            terminal_statuses={"completed", "failed", "cancelled"},
        )
        entry = {
            "seed": item["seed"],
            "job": _job_summary(terminal),
        }
        benchmark_run_id = terminal.get("result_payload", {}).get("benchmark_run_id")
        if benchmark_run_id:
            benchmark = _http_json("GET", f"{api_url}/benchmark/runs/{benchmark_run_id}")
            artifacts = _http_json("GET", f"{api_url}/artifacts/benchmark_run/{benchmark_run_id}")
            entry["benchmark"] = {
                "run_id": benchmark["run_id"],
                "dataset_version": benchmark["dataset_version"],
                "aspects_mae": benchmark["metrics"].get("aspects_mae"),
                "artifacts": len(artifacts),
            }
        completed.append(entry)

    durations = [item["job"]["duration_seconds"] for item in completed]
    return {
        "profile": profile.name,
        "description": profile.description,
        "manifest_path": manifest_path,
        "worker_count": profile.workers,
        "job_count": len(completed),
        "completed_jobs": completed,
        "summary": {
            "statuses": {status: sum(1 for item in completed if item["job"]["status"] == status) for status in {"completed", "failed", "cancelled"}},
            "duration_seconds_min": min(durations) if durations else 0.0,
            "duration_seconds_median": round(statistics.median(durations), 3) if durations else 0.0,
            "duration_seconds_max": max(durations) if durations else 0.0,
        },
        "container_stats": container_stats,
    }


def _run_agentic_profile(
    api_url: str,
    manifest_path: str,
    study_path: str,
    profile: ProfileSpec,
    container_stats: dict[str, dict[str, float]],
) -> dict[str, Any]:
    run = _http_json(
        "POST",
        f"{api_url}/agent/runs",
        {
            "objective": "Run a local dual-surface stress test for Research OS and Clinical Copilot.",
            "surface": "dual",
            "dataset_manifest_path": manifest_path,
            "study_file_paths": [study_path],
            "research_question": "Summarize the local benchmark and MCP research evidence without leaking local file paths.",
            "seed": 29,
        },
    )
    completed = _wait_for_terminal(
        api_url,
        f"/agent/runs/{run['run_id']}",
        timeout=profile.timeout_seconds,
        terminal_statuses={"completed", "failed", "cancelled"},
    )
    artifacts = _http_json("GET", f"{api_url}/artifacts/agent_run/{run['run_id']}")
    trace = _http_json("GET", f"{api_url}/agent/runs/{run['run_id']}/trace")
    events = urllib.request.urlopen(f"{api_url}/agent/runs/{run['run_id']}/events", timeout=30).read().decode("utf-8")
    return {
        "profile": profile.name,
        "description": profile.description,
        "run_id": run["run_id"],
        "status": completed["status"],
        "benchmark_run_id": completed.get("benchmark_run_id"),
        "research_brief_id": completed.get("research_brief_id"),
        "study_id": completed.get("study_id"),
        "artifact_count": len(artifacts),
        "trace_events": len(trace),
        "event_stream_bytes": len(events),
        "final_output_excerpt": str(completed.get("final_output", ""))[:400],
        "error": completed.get("error"),
        "container_stats": container_stats,
    }


def _write_report(output_dir: Path, results: list[dict[str, Any]]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "battery_results.json"
    md_path = output_dir / "battery_results.md"
    json_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    lines = ["# Local Job Battery", ""]
    for result in results:
        lines.append(f"## {result['profile']}")
        lines.append("")
        lines.append(result.get("description", ""))
        lines.append("")
        if "summary" in result:
            summary = result["summary"]
            lines.append(f"- worker_count: `{result['worker_count']}`")
            lines.append(f"- job_count: `{result['job_count']}`")
            lines.append(f"- statuses: `{json.dumps(summary['statuses'], sort_keys=True)}`")
            lines.append(f"- duration_seconds_min: `{summary['duration_seconds_min']}`")
            lines.append(f"- duration_seconds_median: `{summary['duration_seconds_median']}`")
            lines.append(f"- duration_seconds_max: `{summary['duration_seconds_max']}`")
        else:
            lines.append(f"- status: `{result['status']}`")
            lines.append(f"- artifact_count: `{result['artifact_count']}`")
            lines.append(f"- trace_events: `{result['trace_events']}`")
            lines.append(f"- benchmark_run_id: `{result['benchmark_run_id']}`")
        if result.get("container_stats"):
            lines.append("- container_stats:")
            for name, stats in result["container_stats"].items():
                lines.append(
                    f"  - `{name}` cpu<=`{stats['max_cpu_percent']}%` mem<=`{stats['max_mem_mib']} MiB` mem%<=`{stats['max_mem_percent']}%`"
                )
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def _ensure_stack(compose_cmd: list[str], repo_root: Path, compose_file: str, workers: int, *, build: bool) -> None:
    cmd = [*compose_cmd, "-f", compose_file, "up", "-d"]
    if build:
        cmd.append("--build")
    cmd.extend(["--scale", f"benchmark-worker={workers}", "postgres", "minio", "minio-init", "api", "benchmark-worker"])
    result = _run(cmd, repo_root, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "docker compose up failed")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    compose_cmd = _find_compose_cmd(repo_root)
    _ensure_docker_ready(repo_root)

    selected_profiles = args.profile or ["stress-light", "stress-medium"]
    run_root = repo_root / ".local-battery" / datetime.now().strftime("%Y%m%d-%H%M%S")
    fixture_root = run_root / "fixtures"
    output_dir = run_root / "results"

    results: list[dict[str, Any]] = []
    try:
        for profile_name in selected_profiles:
            profile = PROFILES[profile_name]
            _ensure_stack(compose_cmd, repo_root, args.compose_file, profile.workers, build=args.build)
            _wait_for_health(args.api_url, timeout=args.timeout or 180.0)
            sampler = DockerStatsSampler(repo_root)
            sampler.start()
            try:
                if profile.name == "stress-light":
                    manifest_path = _prepare_manifest(repo_root, fixture_root / "stress-light", mode="small")
                    result = _run_stress_profile(
                        args.api_url,
                        manifest_path,
                        profile,
                        seeds=[11, 13, 17, 19, 23],
                        container_stats={},
                    )
                elif profile.name == "stress-medium":
                    manifest_path = _prepare_manifest(repo_root, fixture_root / "stress-medium", mode="full")
                    result = _run_stress_profile(
                        args.api_url,
                        manifest_path,
                        profile,
                        seeds=[31, 37, 41, 43],
                        container_stats={},
                    )
                elif profile.name == "stress-heavy":
                    manifest_path = _prepare_manifest(repo_root, fixture_root / "stress-heavy", mode="full")
                    result = _run_stress_profile(
                        args.api_url,
                        manifest_path,
                        profile,
                        seeds=[47, 53, 59, 61, 67, 71, 73, 79],
                        container_stats={},
                    )
                elif profile.name == "agentic-e2e":
                    manifest_path = _prepare_manifest(repo_root, fixture_root / "agentic-e2e", mode="small")
                    study_path = _prepare_study_file(repo_root, fixture_root / "agentic-e2e")
                    result = _run_agentic_profile(
                        args.api_url,
                        manifest_path,
                        study_path,
                        profile,
                        container_stats={},
                    )
                else:  # pragma: no cover - parser constrains the values
                    raise ValueError(f"Unknown profile: {profile.name}")
            finally:
                container_stats = sampler.stop()
            result["container_stats"] = container_stats
            results.append(result)

        json_path, md_path = _write_report(output_dir, results)
        print(
            json.dumps(
                {
                    "profiles": selected_profiles,
                    "output_json": str(json_path),
                    "output_markdown": str(md_path),
                    "results": results,
                },
                indent=2,
            )
        )
        return 0
    finally:
        if not args.keep_running:
            _run([*compose_cmd, "-f", args.compose_file, "down", "-v"], repo_root, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
