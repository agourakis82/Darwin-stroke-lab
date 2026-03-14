from __future__ import annotations

import json
import sys
from pathlib import Path

from sounio_stroke_lab.cohort_analysis import summarize_manifest_cohort
from sounio_stroke_lab.dataset_manifest import load_benchmark_manifest, load_manifest_cases, validate_benchmark_manifest
from sounio_stroke_lab.live_research import public_research_available
from sounio_stroke_lab.research_corpus import ResearchSource, recommend_protocol_actions, render_evidence_table, search_research_corpus
from sounio_stroke_lab.schemas import (
    MCPServerDescriptor,
    MCPServerHealth,
    MCPServerHealthStatus,
    MCPTransport,
    ToolTrustLevel,
)
from sounio_stroke_lab.sounio_runtime import SounioRuntime

try:
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - import failure handled by health checks
    FastMCP = None  # type: ignore[assignment]


SERVER_DESCRIPTIONS = {
    "darwin-filesystem": "Scoped local artifact and text filesystem access under the configured storage root.",
    "darwin-dataset": "Manifest validation, cohort summaries, case lookup and provenance resources.",
    "darwin-benchmark": "Benchmark launch, retrieval and artifact inspection tools.",
    "darwin-clinical": "Study creation, analysis, result lookup and heatmap access for local studies.",
    "darwin-research": "Local paper corpus, evidence tables and protocol recommendation tools.",
    "darwin-sounio": "Official runtime status, capabilities and inference diagnostics for Sounio.",
}


SERVER_TOOLSETS = {
    "darwin-filesystem": ["list_artifacts", "read_artifact_text", "write_artifact_text"],
    "darwin-dataset": [
        "validate_manifest",
        "cohort_summary",
        "load_manifest_metadata",
        "load_agent_run_context",
        "validate_agent_run_manifest",
        "summarize_agent_run_cohort",
    ],
    "darwin-benchmark": ["run_benchmark", "get_benchmark_run", "get_benchmark_metrics", "run_benchmark_for_agent_run"],
    "darwin-clinical": [
        "create_study_from_paths",
        "analyze_study",
        "get_study_result",
        "inspect_study_quality",
        "prepare_study_for_agent_run",
        "inspect_agent_run_study_quality",
        "analyze_agent_run_study",
        "generate_clinical_summary_for_agent_run",
    ],
    "darwin-research": [
        "search_local_corpus",
        "search_public_literature",
        "write_evidence_table",
        "protocol_actions",
        "compile_research_brief_for_agent_run",
    ],
    "darwin-sounio": ["runtime_status", "runtime_capabilities", "diagnose_region_scoring"],
}


def mcp_server_command(name: str, storage_root: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "sounio_stroke_lab.mcp_server",
        "--name",
        name,
        "--storage-root",
        str(storage_root),
    ]


def register_builtin_mcp_servers(service) -> list[MCPServerDescriptor]:
    descriptors = []
    for name, description in SERVER_DESCRIPTIONS.items():
        descriptor = MCPServerDescriptor(
            name=name,
            transport=MCPTransport.stdio,
            trust_level=ToolTrustLevel.local if name != "darwin-research" else ToolTrustLevel.public,
            description=description,
            allowed_tools=list(SERVER_TOOLSETS[name]),
            command=mcp_server_command(name, service.storage.root),
            notes=["Built for local stdio execution; streamable HTTP is reserved for future remote nodes."],
        )
        service.storage.save_mcp_server(descriptor)
        descriptors.append(descriptor)
    return descriptors


def mcp_server_health(name: str, service) -> MCPServerHealth:
    notes: list[str] = []
    status = MCPServerHealthStatus.healthy
    if FastMCP is None:
        status = MCPServerHealthStatus.unavailable
        notes.append("mcp package is not installed.")
    if name == "darwin-research":
        if not public_research_available():
            notes.append("Public literature search will fall back to the curated local corpus until OPENAI_API_KEY is available.")
        else:
            notes.append("Public literature search is available through the official OpenAI Agents SDK web search tool.")
    if name == "darwin-sounio":
        runtime = SounioRuntime.auto()
        if runtime is None:
            status = MCPServerHealthStatus.degraded if status == MCPServerHealthStatus.healthy else status
            notes.append("Official GitHub Sounio runtime not detected; runtime diagnostics will use fallback availability checks.")
        else:
            notes.append(f"Sounio runtime detected at {runtime.souc_path}.")
    return MCPServerHealth(
        name=name,
        health_status=status,
        available_tools=list(SERVER_TOOLSETS.get(name, [])),
        notes=notes,
    )


def build_mcp_server(name: str, service):
    if FastMCP is None:
        raise RuntimeError("mcp package is required to build MCP servers.")
    if name not in SERVER_DESCRIPTIONS:
        raise KeyError(f"Unknown MCP server: {name}")

    server = FastMCP(
        name=name,
        instructions=SERVER_DESCRIPTIONS[name],
        dependencies=("mcp", "openai-agents"),
    )

    if name == "darwin-filesystem":
        _register_filesystem_server(server, service)
    elif name == "darwin-dataset":
        _register_dataset_server(server, service)
    elif name == "darwin-benchmark":
        _register_benchmark_server(server, service)
    elif name == "darwin-clinical":
        _register_clinical_server(server, service)
    elif name == "darwin-research":
        _register_research_server(server, service)
    elif name == "darwin-sounio":
        _register_sounio_server(server, service)
    return server


def _scoped_artifact_path(service, relative_path: str) -> Path:
    root = service.storage.root.resolve()
    path = (root / relative_path).resolve()
    if root not in (path, *path.parents):
        raise ValueError("Filesystem MCP access is restricted to the storage root.")
    return path


def _safe_local_ref(service, path: str) -> str:
    root = service.storage.root.resolve()
    try:
        resolved = Path(path).expanduser().resolve()
    except Exception:
        return "[LOCAL_REF]"
    try:
        return str(resolved.relative_to(root))
    except Exception:
        return "[LOCAL_REF]"


def _register_filesystem_server(server, service) -> None:
    @server.tool(name="list_artifacts", description="List artifact files under the local storage root.")
    def list_artifacts() -> list[str]:
        return sorted(str(path) for path in service.storage.artifact_dir.rglob("*") if path.is_file())

    @server.tool(name="read_artifact_text", description="Read a UTF-8 text or JSON artifact from the scoped storage root.")
    def read_artifact_text(relative_path: str) -> str:
        return _scoped_artifact_path(service, relative_path).read_text(encoding="utf-8")

    @server.tool(name="write_artifact_text", description="Write a UTF-8 text artifact under the scoped storage root.")
    def write_artifact_text(relative_path: str, content: str) -> str:
        artifact_path = _scoped_artifact_path(service, relative_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(content, encoding="utf-8")
        return str(artifact_path)

    @server.resource("filesystem://storage-root", name="storage_root", mime_type="application/json")
    def storage_root() -> str:
        return json.dumps(
            {
                "storage_root": str(service.storage.root),
                "artifact_dir": str(service.storage.artifact_dir),
                "study_dir": str(service.storage.study_dir),
            },
            indent=2,
        )


def _register_dataset_server(server, service) -> None:
    @server.tool(name="validate_manifest", description="Validate benchmark manifest structure and required file paths.")
    def validate_manifest(manifest_path: str, required_split: str | None = None) -> dict:
        _, issues = validate_benchmark_manifest(
            Path(manifest_path).expanduser().resolve(),
            required_splits={required_split} if required_split else None,
        )
        return {"valid": not issues, "issues": issues}

    @server.tool(name="cohort_summary", description="Summarize one manifest split into a cohort JSON payload.")
    def cohort_summary(manifest_path: str, split: str = "test") -> dict:
        manifest_path_obj = Path(manifest_path).expanduser().resolve()
        manifest, cases = load_manifest_cases(manifest_path_obj, split=split)
        return summarize_manifest_cohort(manifest, cases, manifest_path_obj, split)

    @server.tool(name="load_manifest_metadata", description="Load manifest metadata and explicit case count.")
    def load_manifest_metadata(manifest_path: str) -> dict:
        manifest = load_benchmark_manifest(Path(manifest_path).expanduser().resolve())
        return {
            "dataset_name": manifest.dataset_name,
            "dataset_version": manifest.dataset_version,
            "source": manifest.source,
            "case_count": len(manifest.cases),
            "split_policy": manifest.split_policy,
        }

    @server.tool(name="load_agent_run_context", description="Load a safe summary of one persisted agent run without exposing local file paths.")
    def load_agent_run_context(run_id: str) -> dict:
        return service.get_agent_run_context_summary(run_id)

    @server.tool(name="validate_agent_run_manifest", description="Validate the manifest configured on an agent run.")
    def validate_agent_run_manifest(run_id: str) -> dict:
        return service.validate_agent_run_manifest(run_id)

    @server.tool(name="summarize_agent_run_cohort", description="Summarize the configured test split for an agent run manifest.")
    def summarize_agent_run_cohort(run_id: str) -> dict:
        return service.summarize_agent_run_cohort(run_id)


def _register_benchmark_server(server, service) -> None:
    @server.tool(name="run_benchmark", description="Launch a benchmark run from a manifest.")
    def run_benchmark(manifest_path: str, external_test_manifest_path: str | None = None, seed: int = 13) -> dict:
        run = service.run_benchmark_from_paths(
            manifest_path=manifest_path,
            external_test_manifest_path=external_test_manifest_path,
            seed=seed,
        )
        return run.model_dump(mode="json")

    @server.tool(name="get_benchmark_run", description="Fetch a persisted benchmark run payload.")
    def get_benchmark_run(run_id: str) -> dict:
        run = service.get_benchmark_run(run_id)
        return {
            "run_id": run.run_id,
            "dataset_version": run.dataset_version,
            "pipeline_version": run.pipeline_version,
            "model_family": run.model_family,
            "language_stack": run.language_stack,
            "artifact_count": len(run.artifacts),
        }

    @server.tool(name="get_benchmark_metrics", description="Fetch metrics and artifact paths for a benchmark run.")
    def get_benchmark_metrics(run_id: str) -> dict:
        run = service.get_benchmark_run(run_id)
        return {
            "metrics": run.metrics,
            "comparisons": run.comparisons,
            "artifacts": [
                {
                    "name": item.name,
                    "kind": item.kind,
                    "path": _safe_local_ref(service, item.path),
                    "description": item.description,
                }
                for item in run.artifacts
            ],
        }

    @server.tool(name="run_benchmark_for_agent_run", description="Launch the benchmark configured on a persisted agent run.")
    def run_benchmark_for_agent_run(run_id: str) -> dict:
        run = service.run_benchmark_for_agent_run(run_id)
        primary_metrics = run.metrics.get("sounio_hypercomplex", {})
        return {
            "run_id": run.run_id,
            "dataset_version": run.dataset_version,
            "auc": primary_metrics.get("auc", {}).get("value"),
            "isles_dice": primary_metrics.get("isles_dice", {}).get("value"),
            "artifact_count": len(run.artifacts),
        }


def _register_clinical_server(server, service) -> None:
    @server.tool(name="create_study_from_paths", description="Create a local study from trusted local file paths.")
    def create_study_from_paths(paths: list[str]) -> dict:
        record = service.create_study_from_paths([Path(item).expanduser().resolve() for item in paths])
        return {
            "study_id": record.study_id,
            "input_mode": record.input_mode,
            "status": record.status,
            "warning_count": len(record.warnings),
        }

    @server.tool(name="analyze_study", description="Analyze a local study with the configured model family.")
    def analyze_study(study_id: str, include_baseline_comparison: bool = True, model_family: str = "sounio_hypercomplex") -> dict:
        result = service.analyze_study_from_values(
            study_id=study_id,
            model_family=model_family,
            include_baseline_comparison=include_baseline_comparison,
        )
        return {
            "study_id": result.study_id,
            "model_family": result.model_family,
            "aspects_score": result.aspects_score,
            "global_confidence": result.global_confidence,
            "affected_regions": [item.region for item in result.region_scores if item.affected],
            "heatmap_ref": _safe_local_ref(service, result.heatmap_volume_ref),
            "warning_count": len(result.warnings),
        }

    @server.tool(name="get_study_result", description="Fetch the current result payload for a study.")
    def get_study_result(study_id: str) -> dict:
        result = service.get_analysis_result(study_id)
        return {
            "study_id": result.study_id,
            "model_family": result.model_family,
            "aspects_score": result.aspects_score,
            "global_confidence": result.global_confidence,
            "affected_regions": [item.region for item in result.region_scores if item.affected],
            "heatmap_ref": _safe_local_ref(service, result.heatmap_volume_ref),
            "warning_count": len(result.warnings),
        }

    @server.tool(name="inspect_study_quality", description="Inspect study volume quality and warnings before inference.")
    def inspect_study_quality(study_id: str) -> dict:
        return service.inspect_study_quality(study_id)

    @server.tool(name="prepare_study_for_agent_run", description="Create or fetch the study configured on an agent run without exposing raw local file paths.")
    def prepare_study_for_agent_run(run_id: str) -> dict:
        record = service.prepare_study_for_agent_run(run_id)
        return {
            "study_id": record.study_id,
            "input_mode": record.input_mode,
            "status": record.status,
            "warning_count": len(record.warnings),
        }

    @server.tool(name="inspect_agent_run_study_quality", description="Inspect the prepared study configured on one agent run.")
    def inspect_agent_run_study_quality(run_id: str) -> dict:
        return service.inspect_agent_run_study_quality(run_id)

    @server.tool(name="analyze_agent_run_study", description="Analyze the study configured on one agent run.")
    def analyze_agent_run_study(run_id: str) -> dict:
        result = service.analyze_agent_run_study(run_id)
        return {
            "study_id": result.study_id,
            "model_family": result.model_family,
            "aspects_score": result.aspects_score,
            "global_confidence": result.global_confidence,
            "affected_regions": [item.region for item in result.region_scores if item.affected],
            "heatmap_ref": _safe_local_ref(service, result.heatmap_volume_ref),
        }

    @server.tool(name="generate_clinical_summary_for_agent_run", description="Produce explanation and disclosure artifacts for the configured agent run study.")
    def generate_clinical_summary_for_agent_run(run_id: str) -> dict:
        payload = service.generate_clinical_summary_for_agent_run(run_id)
        analysis = payload["analysis"]
        return {
            "study_id": payload["study_id"],
            "aspects_score": analysis["aspects_score"],
            "global_confidence": analysis["global_confidence"],
            "explanation_artifact": _safe_local_ref(service, payload["explanation_artifact"]),
            "safety_disclosure_artifact": _safe_local_ref(service, payload["safety_disclosure_artifact"]),
        }


def _register_research_server(server, service) -> None:
    def _coerce_sources(sources: list[dict] | None):
        return [
            ResearchSource(
                title=item.get("title", "Untitled"),
                url=item.get("url", ""),
                summary=item.get("summary", ""),
                tags=tuple(item.get("tags", ()) or ()),
                source_type=item.get("source_type", "unknown"),
            )
            for item in (sources or [])
        ]

    @server.tool(name="search_local_corpus", description="Search the local curated research corpus.")
    def search_local_corpus(question: str, limit: int = 6) -> list[dict]:
        return [source.__dict__ for source in search_research_corpus(question, limit=limit)]

    @server.tool(name="search_public_literature", description="Search public literature and official docs through the live web path when available.")
    def search_public_literature(question: str, limit: int = 5, run_id: str | None = None, trace_id: str | None = None) -> dict:
        return service.search_public_literature(question, limit=limit, run_id=run_id, trace_id=trace_id).model_dump(mode="json")

    @server.tool(name="write_evidence_table", description="Render an evidence table markdown artifact under the artifact root.")
    def write_evidence_table(question: str, relative_path: str, sources: list[dict] | None = None) -> str:
        resolved_sources = _coerce_sources(sources) if sources else search_research_corpus(question)
        table = render_evidence_table(question, resolved_sources)
        return service.storage.write_artifact_text(relative_path, table)

    @server.tool(name="protocol_actions", description="Generate protocol recommendations from the local research corpus.")
    def protocol_actions(question: str, sources: list[dict] | None = None) -> list[str]:
        resolved_sources = _coerce_sources(sources) if sources else search_research_corpus(question)
        return recommend_protocol_actions(question, resolved_sources)

    @server.tool(name="compile_research_brief_for_agent_run", description="Compile an evidence table and research brief for one persisted agent run.")
    def compile_research_brief_for_agent_run(run_id: str) -> dict:
        brief = service.compile_research_brief_for_agent_run(run_id)
        return {
            "brief_id": brief.brief_id,
            "question": brief.question,
            "claim_summary": brief.claim_summary,
            "protocol_recommendation_count": len(brief.protocol_recommendations),
            "source_link_count": len(brief.source_links),
            "evidence_table_artifact": _safe_local_ref(service, brief.evidence_table_path),
            "supporting_run_id": brief.supporting_run_id,
        }

    @server.resource("research://corpus", name="local_research_corpus", mime_type="application/json")
    def local_research_corpus() -> str:
        sources = [source.__dict__ for source in search_research_corpus("stroke mcp agents benchmark", limit=20)]
        return json.dumps(sources, indent=2)


def _register_sounio_server(server, service) -> None:
    @server.tool(name="runtime_status", description="Report the current Sounio runtime status.")
    def runtime_status() -> dict:
        runtime = SounioRuntime.auto()
        return {
            "available": runtime is not None,
            "source": runtime.source if runtime else "",
            "souc_path": str(runtime.souc_path) if runtime else "",
            "stdlib_path": str(runtime.stdlib_path) if runtime else "",
        }

    @server.tool(name="runtime_capabilities", description="Report the currently enabled Sounio runtime capabilities for this lab.")
    def runtime_capabilities() -> dict:
        runtime = SounioRuntime.auto()
        return {
            "official_runtime": runtime is not None,
            "volumetric_hypercomplex_inference": runtime is not None,
            "volumetric_artifact_inference": runtime is not None,
            "stdio_transport_only": True,
            "future_streamable_http": False,
        }

    @server.tool(name="diagnose_region_scoring", description="Run a diagnostic hypercomplex regional score computation.")
    def diagnose_region_scoring() -> dict:
        runtime = SounioRuntime.auto()
        if runtime is None:
            return {"available": False, "message": "Official Sounio runtime not detected."}
        scores = runtime.score_regions(
            [0.05, 0.80, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05],
            [0.02, 0.70, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02],
            [0.80, 0.91, 0.80, 0.80, 0.80, 0.80, 0.80, 0.80, 0.80, 0.80],
            [0.74, 0.87, 0.74, 0.74, 0.74, 0.74, 0.74, 0.74, 0.74, 0.74],
        )
        return {"available": True, "scores": scores}

    @server.resource("sounio://runtime", name="runtime_status", mime_type="application/json")
    def runtime_status_resource() -> str:
        return json.dumps(runtime_status(), indent=2)
