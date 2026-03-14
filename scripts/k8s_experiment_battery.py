from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 scripts/k8s_experiment_battery.py",
        description="Run a small K8s-backed experiment battery against the remote Sounio Stroke Lab API.",
    )
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--benchmark-runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--objective", default="Run a small research-only validation battery on Kubernetes.")
    return parser


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _http_json(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = _headers(token)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_terminal(api_url: str, token: str, path: str, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = _http_json("GET", f"{api_url}{path}", token)
        if str(payload.get("status", "")).lower() in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(2.0)
    raise TimeoutError(f"{path} did not reach a terminal state within {timeout} seconds.")


def _wait_for_health(api_url: str, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            request = urllib.request.Request(f"{api_url}/health")
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok":
                return
        except Exception:
            pass
        time.sleep(1.0)
    raise TimeoutError(f"{api_url} did not become healthy within {timeout} seconds.")


def _run_benchmark(api_url: str, token: str, manifest_path: str, timeout: float, seed: int) -> dict[str, Any]:
    submitted = _http_json(
        "POST",
        f"{api_url}/benchmark/jobs",
        token,
        {"dataset_manifest_path": manifest_path, "seed": seed},
    )
    print(f"[benchmark seed={seed}] submitted job_id={submitted['job_id']}", flush=True)
    completed = _wait_for_terminal(api_url, token, f"/jobs/{submitted['job_id']}", timeout)
    print(
        f"[benchmark seed={seed}] status={completed['status']} benchmark_run_id={completed['result_payload'].get('benchmark_run_id')}",
        flush=True,
    )
    return {
        "job_id": completed["job_id"],
        "status": completed["status"],
        "benchmark_run_id": completed["result_payload"].get("benchmark_run_id"),
        "artifact_count": completed["result_payload"].get("artifact_count"),
        "dataset_version": completed["result_payload"].get("dataset_version"),
        "kueue_admitted": completed["result_payload"].get("kueue_admitted"),
        "kueue_finished": completed["result_payload"].get("kueue_finished"),
        "updated_at": completed["updated_at"],
        "created_at": completed["created_at"],
    }


def _run_agent(api_url: str, token: str, manifest_path: str, timeout: float, objective: str) -> dict[str, Any]:
    submitted = _http_json(
        "POST",
        f"{api_url}/agent/runs",
        token,
        {
            "objective": objective,
            "surface": "research",
            "dataset_manifest_path": manifest_path,
            "seed": 13,
        },
    )
    print(f"[agent] submitted run_id={submitted['run_id']} job_id={submitted.get('job_id')}", flush=True)
    completed = _wait_for_terminal(api_url, token, f"/agent/runs/{submitted['run_id']}", timeout)
    print(
        f"[agent] status={completed['status']} benchmark_run_id={completed.get('benchmark_run_id')} research_brief_id={completed.get('research_brief_id')}",
        flush=True,
    )
    trace = _http_json("GET", f"{api_url}/agent/runs/{submitted['run_id']}/trace", token)
    return {
        "run_id": completed["run_id"],
        "job_id": completed.get("job_id"),
        "status": completed["status"],
        "benchmark_run_id": completed.get("benchmark_run_id"),
        "research_brief_id": completed.get("research_brief_id"),
        "trace_count": len(trace),
        "final_output": completed.get("final_output", ""),
    }


def _seconds_between(created_at: str, updated_at: str) -> float:
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    return (updated - created).total_seconds()


def main() -> int:
    args = _build_parser().parse_args()
    api_url = args.api_url.rstrip("/")
    _wait_for_health(api_url, timeout=60.0)

    run_root = Path.cwd() / ".k8s-battery" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)

    benchmarks: list[dict[str, Any]] = []
    for index in range(args.benchmark_runs):
        benchmarks.append(
            _run_benchmark(
                api_url=api_url,
                token=args.token,
                manifest_path=args.manifest_path,
                timeout=args.timeout,
                seed=13 + index,
            )
        )

    agent = _run_agent(
        api_url=api_url,
        token=args.token,
        manifest_path=args.manifest_path,
        timeout=args.timeout,
        objective=args.objective,
    )

    durations = [_seconds_between(item["created_at"], item["updated_at"]) for item in benchmarks]
    summary = {
        "api_url": api_url,
        "manifest_path": args.manifest_path,
        "benchmark_runs": benchmarks,
        "agent_run": agent,
        "summary": {
            "benchmark_completed": sum(1 for item in benchmarks if item["status"] == "completed"),
            "benchmark_failed": sum(1 for item in benchmarks if item["status"] != "completed"),
            "median_benchmark_seconds": round(statistics.median(durations), 3) if durations else None,
            "artifact_counts": [item["artifact_count"] for item in benchmarks],
            "all_kueue_finished": all(bool(item["kueue_finished"]) for item in benchmarks),
            "agent_completed": agent["status"] == "completed",
        },
    }

    json_path = run_root / "battery_results.json"
    md_path = run_root / "battery_results.md"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# K8s Experiment Battery",
        "",
        f"- API: `{api_url}`",
        f"- Manifest: `{args.manifest_path}`",
        f"- Benchmarks: `{len(benchmarks)}`",
        "",
        "## Summary",
        "",
        f"- benchmark_completed: `{summary['summary']['benchmark_completed']}`",
        f"- benchmark_failed: `{summary['summary']['benchmark_failed']}`",
        f"- median_benchmark_seconds: `{summary['summary']['median_benchmark_seconds']}`",
        f"- all_kueue_finished: `{summary['summary']['all_kueue_finished']}`",
        f"- agent_completed: `{summary['summary']['agent_completed']}`",
        "",
        "## Agent",
        "",
        f"- run_id: `{agent['run_id']}`",
        f"- benchmark_run_id: `{agent['benchmark_run_id']}`",
        f"- research_brief_id: `{agent['research_brief_id']}`",
        f"- trace_count: `{agent['trace_count']}`",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"results_json": str(json_path), "results_md": str(md_path), **summary["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
