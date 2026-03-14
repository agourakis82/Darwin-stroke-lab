from __future__ import annotations

from pathlib import Path

from sounio_stroke_lab import cli
from sounio_stroke_lab.cluster_ops import ClusterCheck, ClusterPreflightResult


class _FakeRun:
    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {"run_id": "fake-run", "mode": mode}


class _FakeAgentRun:
    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {"run_id": "fake-agent-run", "mode": mode}


class _FakeService:
    instances: list["_FakeService"] = []

    def __init__(self, storage_root=None, *, start_benchmark_worker=None):
        self.storage_root = storage_root
        self.start_benchmark_worker = start_benchmark_worker
        self.shutdown_called = False
        self.benchmark_requests = []
        self.agent_requests = []
        self.__class__.instances.append(self)

    def run_benchmark(self, request):
        self.benchmark_requests.append(request)
        return _FakeRun()

    def create_agent_run(self, request):
        self.agent_requests.append(request)
        return _FakeAgentRun()

    def shutdown(self):
        self.shutdown_called = True


def test_run_benchmark_cli_disables_embedded_worker_for_kubernetes(monkeypatch, tmp_path: Path, capsys):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SOUNIO_STROKE_JOB_BACKEND", "kubernetes")
    monkeypatch.setattr(cli, "StrokeResearchService", _FakeService)
    _FakeService.instances.clear()

    result = cli.main(["run-benchmark", "--manifest", str(manifest_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert '"run_id": "fake-run"' in captured.out
    assert len(_FakeService.instances) == 1
    assert _FakeService.instances[0].start_benchmark_worker is False
    assert _FakeService.instances[0].shutdown_called is True


def test_run_agent_cli_keeps_embedded_worker_for_local_backend(monkeypatch, tmp_path: Path, capsys):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SOUNIO_STROKE_JOB_BACKEND", "local")
    monkeypatch.setattr(cli, "StrokeResearchService", _FakeService)
    _FakeService.instances.clear()

    result = cli.main(
        [
            "run-agent",
            "--objective",
            "queue a benchmark-backed lab run",
            "--surface",
            "research",
            "--manifest",
            str(manifest_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert '"run_id": "fake-agent-run"' in captured.out
    assert len(_FakeService.instances) == 1
    assert _FakeService.instances[0].start_benchmark_worker is True
    assert _FakeService.instances[0].shutdown_called is True


def test_k8s_preflight_cli_json(monkeypatch, capsys):
    fake_result = ClusterPreflightResult(
        namespace="darwin-genomics",
        pvc_name="sounio-stroke-datasets",
        target_storage_class="rook-cephfs",
        overlay="deploy/k8s/overlays/cephfs",
        current_storage_class="local-path",
        available_storage_classes=("local-path", "rook-cephfs"),
        checks=(ClusterCheck(name="kubectl", ok=True, details="OK."),),
        warnings=(),
    )
    monkeypatch.setattr(cli, "run_cluster_preflight", lambda **_: fake_result)

    result = cli.main(
        [
            "k8s-preflight",
            "--target-storage-class",
            "rook-cephfs",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert '"ready_for_cutover": true' in captured.out


def test_render_dataset_copy_job_cli(capsys):
    result = cli.main(
        [
            "render-dataset-copy-job",
            "--source-pvc",
            "source-pvc",
            "--target-pvc",
            "target-pvc",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert '"claimName": "source-pvc"' in captured.out
    assert '"claimName": "target-pvc"' in captured.out
