from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 scripts/dev_stack_smoke.py",
        description="Bring up the local dev stack and run a benchmark-job smoke test.",
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--keep-running", action="store_true")
    parser.add_argument("--compose-file", default="docker-compose.yml")
    return parser


def _run(cmd: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, check=check, capture_output=True)


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
    raise RuntimeError(
        "No working Docker Compose command was found. Install the Docker Compose plugin or docker-compose."
    )


def _ensure_docker_ready(cwd: Path) -> None:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI is not installed.")
    probe = subprocess.run([docker, "info"], cwd=cwd, text=True, capture_output=True)
    if probe.returncode != 0:
        detail = probe.stderr.strip() or probe.stdout.strip() or "Docker daemon is not available."
        raise RuntimeError(detail)


def _http_json(method: str, url: str, payload: dict | None = None) -> dict:
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


def _wait_for_job(api_url: str, job_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = _http_json("GET", f"{api_url}/jobs/{job_id}")
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(1.0)
    raise TimeoutError(f"Job {job_id} did not reach a terminal state within {timeout} seconds.")


def _prepare_manifest(repo_root: Path) -> str:
    sys.path.insert(0, str(repo_root / "src"))
    sys.path.insert(0, str(repo_root))
    from tests.support import create_small_benchmark_manifest  # type: ignore

    smoke_root = repo_root / ".dev-smoke"
    smoke_root.mkdir(parents=True, exist_ok=True)
    manifest_path = create_small_benchmark_manifest(smoke_root)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for case in payload.get("cases", []):
        for field in ("volume_path", "lesion_mask_path"):
            raw_path = case.get(field)
            if not raw_path:
                continue
            host_path = Path(raw_path)
            if host_path.is_absolute() and repo_root in host_path.parents:
                case[field] = f"/workspace/{host_path.relative_to(repo_root).as_posix()}"
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return f"/workspace/{manifest_path.relative_to(repo_root).as_posix()}"


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    compose_cmd = _find_compose_cmd(repo_root)
    _ensure_docker_ready(repo_root)
    manifest_path = _prepare_manifest(repo_root)

    up_cmd = [*compose_cmd, "-f", args.compose_file, "up", "-d", "--build", "postgres", "minio", "minio-init", "api", "benchmark-worker"]
    down_cmd = [*compose_cmd, "-f", args.compose_file, "down", "-v"]

    try:
        _run(up_cmd, repo_root)
        _wait_for_health(args.api_url, timeout=args.timeout)
        job = _http_json(
            "POST",
            f"{args.api_url}/benchmark/jobs",
            {"dataset_manifest_path": manifest_path, "seed": 17},
        )
        terminal = _wait_for_job(args.api_url, job["job_id"], timeout=args.timeout)
        if terminal["status"] != "completed":
            raise RuntimeError(f"Benchmark smoke job did not complete successfully: {terminal}")
        benchmark = _http_json("GET", f"{args.api_url}/benchmark/runs/{terminal['result_payload']['benchmark_run_id']}")
        artifacts = _http_json("GET", f"{args.api_url}/artifacts/benchmark_run/{benchmark['run_id']}")
        summary = {
            "job_id": job["job_id"],
            "benchmark_run_id": benchmark["run_id"],
            "dataset_version": benchmark["dataset_version"],
            "artifact_count": len(artifacts),
            "worker_backend": terminal["result_payload"].get("backend"),
        }
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        if not args.keep_running:
            try:
                _run(down_cmd, repo_root, check=False)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
