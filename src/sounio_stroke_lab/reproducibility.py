from __future__ import annotations

import importlib
import platform
import subprocess
import sys
from pathlib import Path

from sounio_stroke_lab.sounio_runtime import SounioRuntime


def _package_version(name: str) -> str | None:
    try:
        module = importlib.import_module(name)
    except Exception:
        return None
    return getattr(module, "__version__", None)


def _git_head(root: Path) -> dict[str, object]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        return {
            "head": head,
            "branch": branch,
            "dirty": bool(status),
        }
    except Exception:
        return {}


def build_reproducibility_snapshot(project_root: Path) -> dict[str, object]:
    runtime = SounioRuntime.auto()
    return {
        "python_version": sys.version,
        "platform": platform.platform(),
        "project_root": str(project_root),
        "git": _git_head(project_root),
        "packages": {
            name: _package_version(name)
            for name in ("numpy", "scipy", "matplotlib", "fastapi", "pydantic", "nibabel", "pydicom")
        },
        "sounio_runtime": (
            {
                "available": True,
                "souc_path": str(runtime.souc_path),
                "stdlib_path": str(runtime.stdlib_path),
                "source": runtime.source,
            }
            if runtime is not None
            else {
                "available": False,
            }
        ),
    }
