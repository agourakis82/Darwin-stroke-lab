from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter

from sounio_stroke_lab.atlas import build_aspects_atlas
from sounio_stroke_lab.config import DEFAULT_TARGET_SHAPE
from sounio_stroke_lab.main import create_app
from sounio_stroke_lab.schemas import (
    AgentRun,
    AgentRunRequest,
    ArtifactRef,
    BenchmarkRequest,
    BenchmarkRun,
    CampaignRecord,
    CampaignRequest,
    JobRecord,
    PortfolioReport,
    ProgramCampaignAttachRequest,
    ProgramRecord,
    ProgramPortfolioReport,
    ProgramRequest,
    RecordOwnerType,
)
from sounio_stroke_lab.service import StrokeResearchService


CONTRACT_MODELS: dict[str, type[Any]] = {
    "JobRecord": JobRecord,
    "ArtifactRef": ArtifactRef,
    "BenchmarkRun": BenchmarkRun,
    "CampaignRecord": CampaignRecord,
    "ProgramRecord": ProgramRecord,
    "PortfolioReport": PortfolioReport,
    "ProgramPortfolioReport": ProgramPortfolioReport,
    "AgentRun": AgentRun,
}

FIXTURE_FILE_NAMES: dict[str, str] = {
    "JobRecord": "job_record.json",
    "ArtifactRef": "artifact_ref.json",
    "BenchmarkRun": "benchmark_run.json",
    "CampaignRecord": "campaign_record.json",
    "ProgramRecord": "program_record.json",
    "PortfolioReport": "portfolio_report.json",
    "ProgramPortfolioReport": "program_portfolio_report.json",
    "AgentRun": "agent_run.json",
}

COMPARISON_REGISTRY: dict[str, Any] = {
    "registry_version": "v1",
    "thesis": "Sounio plus F# is the target rewrite stack; Stan is the probabilistic baseline lane.",
    "selection_policy": "role_based",
    "subsystems": [
        {
            "name": "scientific_kernel",
            "primary_language": "sounio",
            "comparison_against": ["python", "julia", "rust"],
            "why": "Kernel semantics, manifest execution, provenance, and benchmark-backed scientific claims belong to Sounio.",
        },
        {
            "name": "control_plane",
            "primary_language": "fsharp",
            "comparison_against": ["python", "rust"],
            "why": "Typed orchestration, units-of-measure, ASP.NET Core integration, and lower glue burden than Python.",
        },
        {
            "name": "probabilistic_baseline",
            "primary_language": "stan",
            "comparison_against": ["python", "julia"],
            "why": "Calibration and uncertainty should be benchmarked against a mature probabilistic baseline, not embedded in the workflow shell.",
        },
        {
            "name": "k8s_hpc_edge",
            "primary_language": "fsharp",
            "comparison_against": ["rust"],
            "why": "The primary rewrite stays F#-first, but K8s and systems burden stay explicitly comparable against Rust.",
        },
    ],
    "language_roles": {
        "sounio": {
            "selection_status": "primary_scientific_kernel",
            "adoption_scope": "scientific semantics, kernels, manifests, and runtime contracts",
        },
        "fsharp": {
            "selection_status": "primary_control_plane",
            "adoption_scope": "api, workers, orchestration, jobs, campaigns, programs, portfolio, and storage adapters",
        },
        "stan": {
            "selection_status": "baseline_probabilistic_lane",
            "adoption_scope": "calibration, uncertainty estimation, posterior checks, and comparative evidence",
        },
        "python": {
            "selection_status": "legacy_compatibility_lane",
            "adoption_scope": "frozen contract source of truth until F# reaches parity",
        },
        "rust": {
            "selection_status": "comparison_lane",
            "adoption_scope": "systems burden and K8s/HPC edge comparison, not the chosen primary rewrite",
        },
        "julia": {
            "selection_status": "scientific_reference_lane",
            "adoption_scope": "future scientific cross-checks and probabilistic alternatives, not the primary rewrite shell",
        },
    },
}

_ID_FIELDS = {
    "artifact_id",
    "job_id",
    "run_id",
    "campaign_id",
    "program_id",
    "brief_id",
    "study_id",
    "trace_id",
    "session_id",
    "benchmark_run_id",
    "research_brief_id",
    "supporting_run_id",
    "supporting_campaign_id",
    "leading_campaign_id",
    "leading_program_id",
    "best_run_id",
    "current_best_run_id",
    "top_plan_id",
    "proposal_id",
    "plan_id",
    "entry_id",
    "owner_id",
}

_TIMESTAMP_FIELDS = {
    "created_at",
    "updated_at",
    "generated_at",
    "checked_at",
}


def _base_volume() -> np.ndarray:
    shape = DEFAULT_TARGET_SHAPE
    zz, yy, xx = np.meshgrid(
        np.linspace(-1.0, 1.0, shape[0], dtype=np.float32),
        np.linspace(-1.0, 1.0, shape[1], dtype=np.float32),
        np.linspace(-1.0, 1.0, shape[2], dtype=np.float32),
        indexing="ij",
    )
    radial = np.sqrt((xx * 1.2) ** 2 + yy**2 + (zz * 0.9) ** 2)
    return np.clip(0.82 - 0.45 * radial + 0.02 * np.sin(yy * np.pi * 2.0), 0.0, 1.0).astype(np.float32)


def _write_case(
    case_dir: Path,
    case_id: str,
    hemisphere: str,
    regions: list[str],
    lesion_drop: float = 0.22,
    spread_sigma: float = 1.0,
) -> tuple[str, str]:
    atlas = build_aspects_atlas(DEFAULT_TARGET_SHAPE)[hemisphere]
    volume = _base_volume()
    lesion_mask = np.zeros(DEFAULT_TARGET_SHAPE, dtype=np.float32)
    for region in regions:
        lesion_mask[atlas[region]] = 1.0
    if regions:
        lesion_field = gaussian_filter(lesion_mask, sigma=spread_sigma)
        lesion_field = lesion_field / (float(lesion_field.max()) or 1.0)
        volume -= lesion_drop * lesion_field
        volume -= 0.05 * gaussian_filter(lesion_field, sigma=1.2)
        volume = np.clip(volume, 0.0, 1.0)
    volume_path = case_dir / f"{case_id}_volume.npy"
    mask_path = case_dir / f"{case_id}_mask.npy"
    np.save(volume_path, volume)
    np.save(mask_path, lesion_mask)
    return str(volume_path), str(mask_path)


def _create_small_benchmark_manifest(tmp_path: Path) -> Path:
    case_dir = tmp_path / "small_cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    case_specs = [
        ("mini-001", "train", "right", [], 0.20, 0.9),
        ("mini-002", "train", "left", ["insula", "m2"], 0.24, 1.2),
        ("mini-003", "train", "right", ["caudate"], 0.22, 1.0),
        ("mini-004", "train", "left", ["m5"], 0.19, 0.8),
        ("mini-005", "test", "right", [], 0.20, 0.9),
        ("mini-006", "test", "left", ["m4"], 0.22, 1.1),
    ]
    manifest = {
        "dataset_name": "fixture-mini-benchmark",
        "dataset_version": "fixture-mini-v1",
        "split_policy": "fixed train/test mini fixture",
        "source": "rewrite contract snapshot fixture",
        "cases": [],
    }
    for case_id, split, hemisphere, regions, lesion_drop, spread_sigma in case_specs:
        volume_path, mask_path = _write_case(
            case_dir,
            case_id,
            hemisphere,
            regions,
            lesion_drop=lesion_drop,
            spread_sigma=spread_sigma,
        )
        manifest["cases"].append(
            {
                "case_id": case_id,
                "split": split,
                "volume_path": volume_path,
                "lesion_mask_path": mask_path,
                "hemisphere": hemisphere,
                "aspects_score": 10 - len(regions),
            }
        )
    manifest_path = tmp_path / "small_benchmark_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def _canonicalize(value: Any, *, key: str | None = None, path_roots: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {k: _canonicalize(v, key=k, path_roots=path_roots) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonicalize(item, key=key, path_roots=path_roots) for item in value]
    if isinstance(value, str):
        if key in _ID_FIELDS and value:
            return f"<{key}>"
        if key in _TIMESTAMP_FIELDS and value:
            return f"<{key}>"
        rendered = value
        for root in path_roots:
            if root and root in rendered:
                rendered = rendered.replace(root, "<snapshot_root>")
        return rendered
    return value


def _wait_for_job(service: StrokeResearchService, job_id: str, timeout_seconds: float = 120.0) -> JobRecord:
    return service.job_backend.wait_for_job(job_id, timeout_seconds=timeout_seconds, poll_interval=0.1)


def _wait_for_campaign(service: StrokeResearchService, campaign_id: str, timeout_seconds: float = 120.0) -> CampaignRecord:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        campaign = service.get_campaign(campaign_id)
        if campaign.status in {"completed", "failed", "cancelled"}:
            return campaign
        time.sleep(0.1)
    raise TimeoutError(f"Campaign {campaign_id} did not reach a terminal state within {timeout_seconds} seconds.")


def _wait_for_agent_run(service: StrokeResearchService, run_id: str, timeout_seconds: float = 120.0) -> AgentRun:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        run = service.get_agent_run(run_id)
        if run.status in {"completed", "failed", "cancelled"}:
            return run
        time.sleep(0.1)
    raise TimeoutError(f"Agent run {run_id} did not reach a terminal state within {timeout_seconds} seconds.")


def build_contract_snapshot(output_root: Path) -> dict[str, str]:
    output_root = output_root.resolve()
    openapi_dir = output_root / "openapi"
    schema_dir = output_root / "json_schema"
    golden_dir = output_root / "golden"
    registry_dir = output_root / "registry"
    for directory in (openapi_dir, schema_dir, golden_dir, registry_dir):
        directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="darwin-openapi-runtime-") as openapi_runtime_dir:
        app = create_app(storage_root=Path(openapi_runtime_dir), start_benchmark_worker=False)
        openapi_path = openapi_dir / "openapi.json"
        openapi_path.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True), encoding="utf-8")
        app.state.service.shutdown()

    with tempfile.TemporaryDirectory(prefix="darwin-contract-snapshot-") as runtime_dir:
        runtime_root = Path(runtime_dir)
        service = StrokeResearchService(storage_root=runtime_root, start_benchmark_worker=True)
        try:
            manifest_path = _create_small_benchmark_manifest(runtime_root / "fixtures")
            benchmark_request = BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=13)
            benchmark_job = service.submit_benchmark(benchmark_request)
            benchmark_job = _wait_for_job(service, benchmark_job.job_id)
            benchmark_run = service.get_benchmark_run(str(benchmark_job.result_payload["benchmark_run_id"]))

            campaign = service.create_campaign(
                CampaignRequest(
                    name="rewrite-parity-campaign",
                    objective="Freeze the current benchmark-first semantics before the F# rewrite.",
                    benchmark_specs=[
                        {
                            "label": "mini-fixture-seed-13",
                            "request": benchmark_request.model_dump(mode="json"),
                        }
                    ],
                    notes="Generated by rewrite_contracts.py",
                )
            )
            campaign = _wait_for_campaign(service, campaign.campaign_id)

            program = service.create_program(
                ProgramRequest(
                    name="rewrite-parity-program",
                    objective="Track parity between the Python source of truth and the F# rewrite lane.",
                    hypothesis="F# can replace Python orchestration without changing benchmark semantics.",
                    notes="Generated by rewrite_contracts.py",
                    campaign_ids=[campaign.campaign_id],
                )
            )
            program = service.attach_program_campaign(
                program.program_id,
                ProgramCampaignAttachRequest(campaign_id=campaign.campaign_id),
            )

            agent_run = service.create_agent_run(
                AgentRunRequest(
                    objective="Freeze an agent-run contract for the Sounio + F# rewrite lane.",
                    surface="research",
                    dataset_manifest_path=str(manifest_path),
                    research_question="What evidence must remain stable during the rewrite to F#?",
                    notes="Generated by rewrite_contracts.py",
                )
            )
            agent_run = _wait_for_agent_run(service, agent_run.run_id)

            artifact_refs = sorted(
                service.list_artifacts(RecordOwnerType.benchmark_run, benchmark_run.run_id),
                key=lambda item: (item.name, item.kind, item.artifact_id),
            )
            campaign_portfolio = service.get_campaign_portfolio()
            program_portfolio = service.get_program_portfolio()

            snapshots = {
                "JobRecord": benchmark_job.model_dump(mode="json"),
                "ArtifactRef": artifact_refs[0].model_dump(mode="json") if artifact_refs else {},
                "BenchmarkRun": benchmark_run.model_dump(mode="json"),
                "CampaignRecord": campaign.model_dump(mode="json"),
                "ProgramRecord": program.model_dump(mode="json"),
                "PortfolioReport": campaign_portfolio.model_dump(mode="json"),
                "ProgramPortfolioReport": program_portfolio.model_dump(mode="json"),
                "AgentRun": agent_run.model_dump(mode="json"),
            }

            path_roots = (str(output_root), str(runtime_root), str(manifest_path.parent))
            written: dict[str, str] = {"openapi": str(openapi_path)}
            for model_name, model in CONTRACT_MODELS.items():
                schema = model.model_json_schema()
                schema_path = schema_dir / f"{model_name}.schema.json"
                schema_path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
                written[f"schema:{model_name}"] = str(schema_path)

                fixture_path = golden_dir / FIXTURE_FILE_NAMES[model_name]
                fixture_path.write_text(
                    json.dumps(_canonicalize(snapshots[model_name], path_roots=path_roots), indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                written[f"golden:{model_name}"] = str(fixture_path)

            comparison_registry_path = registry_dir / "comparison_registry.json"
            comparison_registry_path.write_text(
                json.dumps(COMPARISON_REGISTRY, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            written["comparison_registry"] = str(comparison_registry_path)

            summary_path = output_root / "snapshot_manifest.json"
            summary_path.write_text(json.dumps(written, indent=2, sort_keys=True), encoding="utf-8")
            written["snapshot_manifest"] = str(summary_path)
            return written
        finally:
            service.shutdown()
