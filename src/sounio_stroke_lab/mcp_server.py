from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sounio_stroke_lab.mcp_registry import build_mcp_server
from sounio_stroke_lab.service import StrokeResearchService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m sounio_stroke_lab.mcp_server")
    parser.add_argument("--name", required=True)
    parser.add_argument("--storage-root")
    return parser


async def _run(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    storage_root = Path(args.storage_root).expanduser().resolve() if args.storage_root else None
    service = StrokeResearchService(storage_root=storage_root)
    server = build_mcp_server(args.name, service)
    await server.run_stdio_async()


def main(argv: list[str] | None = None) -> None:
    asyncio.run(_run(argv))


if __name__ == "__main__":
    main()
