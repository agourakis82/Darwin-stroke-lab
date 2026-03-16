import json
import time
from pathlib import Path

from sounio_stroke_lab.labctl import main as labctl_main
from sounio_stroke_lab.schemas import RunStatus
from sounio_stroke_lab.service import StrokeResearchService


def test_labctl_submit_and_resume_local_mode(tmp_path: Path, capsys):
    workspace = tmp_path / "workspace"
    repo = workspace / "src"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "main.py").write_text("print('pilot')\n", encoding="utf-8")

    storage_root = tmp_path / "data"
    exit_code = labctl_main(
        [
            "--storage-root",
            str(storage_root),
            "run",
            "submit",
            "--workspace-path",
            str(workspace),
            "--repo-path",
            str(repo),
            "--task-name",
            "inventory-workspace",
        ]
    )
    assert exit_code == 0
    submit_payload = json.loads(capsys.readouterr().out)
    run_id = submit_payload["run_id"]

    service = StrokeResearchService(storage_root=storage_root)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        run = service.get_run(run_id)
        if run.status in {RunStatus.completed, RunStatus.failed}:
            break
        time.sleep(0.05)
    assert service.get_run(run_id).status == RunStatus.completed

    exit_code = labctl_main(
        [
            "--storage-root",
            str(storage_root),
            "run",
            "resume",
            run_id,
        ]
    )
    assert exit_code == 0
    resume_payload = json.loads(capsys.readouterr().out)
    assert resume_payload["run_id"] == run_id
    assert resume_payload["status"] == "completed"
    assert any("Inspect" in line for line in resume_payload["resume_instructions"])
