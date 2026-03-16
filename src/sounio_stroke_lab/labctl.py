from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from sounio_stroke_lab.schemas import RunSubmitRequest
from sounio_stroke_lab.service import StrokeResearchService


def _parse_parameters(values: list[str] | None) -> dict[str, str]:
    parameters: dict[str, str] = {}
    for item in values or []:
        key, _, value = item.partition("=")
        if not key:
            raise ValueError(f"Invalid --parameter value: {item}")
        parameters[key] = value if value else "true"
    return parameters


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="labctl", description="Workspace helper for resume-capable lab runs.")
    parser.add_argument("--api-base", help="Optional API base URL. If omitted, uses local storage mode.")
    parser.add_argument("--storage-root", help="Storage root for local mode.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Submit or resume durable runs.")
    run_subparsers = run.add_subparsers(dest="run_command", required=True)

    submit = run_subparsers.add_parser("submit", help="Submit a generic agent run.")
    submit.add_argument("--workspace-id")
    submit.add_argument("--user-id", default="workspace-user")
    submit.add_argument("--workspace-path", default="/workspace")
    submit.add_argument("--repo-path", default="/workspace/src")
    submit.add_argument("--task-name", default="inventory-workspace")
    submit.add_argument("--parameter", action="append", dest="parameters")

    resume = run_subparsers.add_parser("resume", help="Fetch the latest resume summary.")
    resume.add_argument("run_id")

    return parser


def _local_service(storage_root: str | None) -> StrokeResearchService:
    resolved = Path(storage_root).expanduser().resolve() if storage_root else None
    return StrokeResearchService(storage_root=resolved)


def _post_remote(api_base: str, path: str, payload: dict) -> dict:
    with httpx.Client(base_url=api_base, timeout=30.0) as client:
        response = client.post(path, json=payload)
        response.raise_for_status()
        return response.json()


def _get_remote(api_base: str, path: str) -> dict:
    with httpx.Client(base_url=api_base, timeout=30.0) as client:
        response = client.get(path)
        response.raise_for_status()
        return response.json()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run" and args.run_command == "submit":
        parameters = _parse_parameters(args.parameters)
        request = RunSubmitRequest(
            workspace_id=args.workspace_id,
            user_id=args.user_id,
            workspace_path=str(Path(args.workspace_path).expanduser().resolve()),
            repo_path=str(Path(args.repo_path).expanduser().resolve()),
            task_name=args.task_name,
            parameters=parameters,
        )
        if args.api_base:
            payload = _post_remote(args.api_base, "/runs", request.model_dump(mode="json"))
        else:
            payload = _local_service(args.storage_root).submit_run(request).model_dump(mode="json")
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "run" and args.run_command == "resume":
        if args.api_base:
            payload = _get_remote(args.api_base, f"/runs/{args.run_id}/resume-summary")
        else:
            payload = _local_service(args.storage_root).get_resume_summary(args.run_id).model_dump(mode="json")
        print(json.dumps(payload, indent=2))
        return 0

    parser.error("Unsupported command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
