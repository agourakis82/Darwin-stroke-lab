from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from sounio_stroke_lab.live_research import PublicResearchBundle
from sounio_stroke_lab.mcp_registry import SERVER_TOOLSETS, mcp_server_health, register_builtin_mcp_servers
from sounio_stroke_lab.research_corpus import ResearchSource, merge_research_sources, recommend_protocol_actions, render_evidence_table, search_research_corpus
from sounio_stroke_lab.schemas import (
    AgentRun,
    AgentRunRequest,
    AgentRunStatus,
    AgentStep,
    AgentStepKind,
    AgentStepStatus,
    AnalysisResult,
    AutonomyMode,
    JobStatus,
    MCPServerDescriptor,
    PrivacyMode,
    SafetyPolicy,
    TraceEvent,
    ToolAudit,
    ToolTrustLevel,
)
from sounio_stroke_lab.trace_audit import isolated_trace_processor

try:  # pragma: no cover - runtime use is optional in tests
    from agents import Agent, GuardrailFunctionOutput, InputGuardrail, OutputGuardrail, RunConfig, Runner
    from agents.lifecycle import RunHooks
    from agents.mcp import MCPServerManager, MCPServerStdio
except Exception:  # pragma: no cover - import failure falls back to deterministic runtime
    Agent = None  # type: ignore[assignment]
    GuardrailFunctionOutput = None  # type: ignore[assignment]
    InputGuardrail = None  # type: ignore[assignment]
    OutputGuardrail = None  # type: ignore[assignment]
    RunConfig = None  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]
    RunHooks = None  # type: ignore[assignment]
    MCPServerManager = None  # type: ignore[assignment]
    MCPServerStdio = None  # type: ignore[assignment]


class AgentRunCancelled(RuntimeError):
    pass


class AgentWorkflowReport(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    summary: str
    research_summary: str = ""
    clinical_summary: str = ""
    benchmark_run_id: str | None = None
    study_id: str | None = None
    research_brief_id: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)


AGENT_SERVER_TOOL_ACCESS: dict[str, dict[str, list[str]]] = {
    "ResearchManagerAgent": {"darwin-dataset": ["load_agent_run_context"]},
    "LiteratureScout": {"darwin-research": ["search_local_corpus", "search_public_literature"]},
    "DataQCAgent": {
        "darwin-dataset": [
            "load_agent_run_context",
            "validate_agent_run_manifest",
            "summarize_agent_run_cohort",
        ]
    },
    "BenchmarkOperator": {
        "darwin-benchmark": ["run_benchmark_for_agent_run", "get_benchmark_run", "get_benchmark_metrics"]
    },
    "ManuscriptAgent": {"darwin-research": ["compile_research_brief_for_agent_run"]},
    "ClinicalManagerAgent": {"darwin-dataset": ["load_agent_run_context"]},
    "StudyIntakeAgent": {"darwin-clinical": ["prepare_study_for_agent_run"]},
    "ImageQAAgent": {"darwin-clinical": ["inspect_agent_run_study_quality"]},
    "SounioAnalysisAgent": {"darwin-clinical": ["analyze_agent_run_study"]},
    "ExplanationAgent": {"darwin-clinical": ["generate_clinical_summary_for_agent_run"]},
    "SafetyDisclosureAgent": {},
    "LabDirectorAgent": {},
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    server_name: str
    trust_level: ToolTrustLevel
    write_capable: bool
    func: Callable[..., Any]


class PublicPayloadFilter:
    ABSOLUTE_PATH = re.compile(r"(/[^ \n\t]+|[A-Za-z]:\\\\[^ \n\t]+)")
    IMAGE_SUFFIX = re.compile(r"\b[^ \n\t]+\.(dcm|nii|nii\.gz|npy|npz)\b", re.IGNORECASE)
    PATIENT_TOKEN = re.compile(r"\b(patient|mrn|dob|accession)\b[^ \n\t]*", re.IGNORECASE)

    @classmethod
    def sanitize_text(cls, value: str) -> str:
        sanitized = cls.ABSOLUTE_PATH.sub("[REDACTED_PATH]", value)
        sanitized = cls.IMAGE_SUFFIX.sub("[REDACTED_IMAGE_REF]", sanitized)
        sanitized = cls.PATIENT_TOKEN.sub("[REDACTED_IDENTIFIER]", sanitized)
        return sanitized

    @classmethod
    def sanitize_payload(cls, payload: Any) -> Any:
        if isinstance(payload, str):
            return cls.sanitize_text(payload)
        if isinstance(payload, Path):
            return "[REDACTED_PATH]"
        if isinstance(payload, list):
            return [cls.sanitize_payload(item) for item in payload]
        if isinstance(payload, dict):
            return {str(key): cls.sanitize_payload(value) for key, value in payload.items()}
        return payload

    @classmethod
    def preview(cls, payload: Any, *, max_length: int = 400) -> str:
        rendered = json.dumps(payload, ensure_ascii=True, default=str, sort_keys=True)
        return rendered[:max_length]


class DeterministicAgentRuntime:
    def execute(self, run: AgentRun, request: AgentRunRequest, service) -> AgentRun:
        context = ExecutionContext(service, run)
        try:
            context.step("LabDirectorAgent", AgentStepKind.review, "Run started.", status=AgentStepStatus.started)
            research_summary = ""
            clinical_summary = ""
            surface = request.surface.value if hasattr(request.surface, "value") else str(request.surface)

            if surface in {"research", "dual"}:
                context.step("LabDirectorAgent", AgentStepKind.handoff, "Routing workflow to ResearchManagerAgent.")
                research_summary = self._run_research(request, context)

            if surface in {"clinical", "dual"}:
                context.step("LabDirectorAgent", AgentStepKind.handoff, "Routing workflow to ClinicalManagerAgent.")
                clinical_summary = self._run_clinical(request, context)

            final_output = self._compose_final_output(research_summary, clinical_summary)
            if OpenAIAgentsRuntime.is_available():
                final_output = OpenAIAgentsRuntime(context.service).synthesize(
                    context.run,
                    PublicPayloadFilter.sanitize_text(research_summary),
                    PublicPayloadFilter.sanitize_text(clinical_summary),
                )
            return context.complete_run(final_output)
        except AgentRunCancelled:
            return context.cancel_run("Agent run cancelled by user request.")
        except Exception as exc:
            return context.fail_run(str(exc))

    def _run_research(self, request: AgentRunRequest, context: "ExecutionContext") -> str:
        question = request.research_question or request.objective
        context.step("ResearchManagerAgent", AgentStepKind.review, "Research manager accepted the objective.")
        local_source_payloads = context.call_tool("LiteratureScout", "search_local_corpus", {"question": question, "limit": 6})
        public_bundle_payload = context.call_tool(
            "LiteratureScout",
            "search_public_literature",
            {
                "question": question,
                "limit": 5,
                "run_id": context.run.run_id,
                "trace_id": context.run.trace_id,
            },
        )
        local_sources = _coerce_research_sources(local_source_payloads)
        public_bundle = PublicResearchBundle.model_validate(public_bundle_payload)
        public_sources = context.service.public_bundle_to_sources(public_bundle)
        combined_sources = merge_research_sources(
            public_sources,
            local_sources,
            limit=10,
        )
        evidence_relative_path = f"{context.run.run_id}/research/evidence_table.md"
        evidence_path = context.call_tool(
            "CorpusSynthesizer",
            "write_evidence_table",
            {
                "question": question,
                "relative_path": evidence_relative_path,
                "sources": [source.__dict__ for source in combined_sources],
            },
        )
        source_bundle_path = context.service.storage.write_artifact_json(
            f"{context.run.run_id}/research/source_bundle.json",
            {
                "question": question,
                "local_sources": local_source_payloads,
                "public_bundle": public_bundle_payload,
            },
        )
        context.step(
            "CorpusSynthesizer",
            AgentStepKind.artifact_write,
            "Rendered evidence table from local plus public research sources.",
            artifact_path=evidence_path,
        )
        context.step(
            "LiteratureScout",
            AgentStepKind.artifact_write,
            "Persisted combined local and public literature bundle.",
            artifact_path=source_bundle_path,
        )
        if request.dataset_manifest_path:
            validation = context.call_tool(
                "DataQCAgent",
                "validate_manifest",
                {"manifest_path": request.dataset_manifest_path, "required_split": request.test_split},
            )
            context.step(
                "DataQCAgent",
                AgentStepKind.review,
                "Validated benchmark manifest for research workflow.",
                payload=validation,
            )
            cohort = context.call_tool(
                "DataQCAgent",
                "cohort_summary",
                {"manifest_path": request.dataset_manifest_path, "split": request.test_split},
            )
            cohort_path = context.service.storage.write_artifact_json(
                f"{context.run.run_id}/research/cohort_summary.json",
                cohort,
            )
            context.step(
                "DataQCAgent",
                AgentStepKind.artifact_write,
                "Persisted research cohort summary artifact.",
                artifact_path=cohort_path,
                payload=cohort,
            )
        benchmark_run = None
        if request.dataset_manifest_path:
            benchmark_payload = context.call_tool(
                "BenchmarkOperator",
                "run_benchmark",
                {
                    "manifest_path": request.dataset_manifest_path,
                    "external_test_manifest_path": request.external_test_manifest_path,
                    "seed": request.seed,
                },
            )
            benchmark_run = context.service.get_benchmark_run(benchmark_payload["run_id"])
            context.update_run(benchmark_run_id=benchmark_run.run_id)
            context.step(
                "BenchmarkOperator",
                AgentStepKind.review,
                "Completed benchmark execution from the research workflow.",
                payload={"run_id": benchmark_run.run_id, "dataset_version": benchmark_run.dataset_version},
            )
        protocol_actions = context.call_tool(
            "ManuscriptAgent",
            "protocol_actions",
            {
                "question": question,
                "sources": [source.__dict__ for source in combined_sources],
            },
        )
        research_brief = context.service.create_research_brief_from_values(
            question=question,
            benchmark_run_id=benchmark_run.run_id if benchmark_run else None,
            dataset_manifest_path=request.dataset_manifest_path,
            source_links=[source.url for source in combined_sources],
            protocol_recommendations=protocol_actions,
            evidence_table_path=evidence_path,
        )
        context.update_run(research_brief_id=research_brief.brief_id)
        context.step(
            "ManuscriptAgent",
            AgentStepKind.review,
            "Generated research brief and protocol recommendations.",
            payload={"brief_id": research_brief.brief_id},
        )
        metric_summary = ""
        if benchmark_run is not None:
            sounio_metrics = benchmark_run.metrics.get("sounio_hypercomplex", {})
            metric_summary = (
                f" Benchmark run `{benchmark_run.run_id}` completed with "
                f"AUC {sounio_metrics.get('auc', {}).get('value', 'n/a')} and "
                f"Dice {sounio_metrics.get('isles_dice', {}).get('value', 'n/a')}."
            )
        public_mode = public_bundle_payload.get("mode", "unknown")
        return (
            f"Research OS completed a literature-backed brief for '{question}'. "
            f"Evidence table saved to {evidence_path}. Public research mode: {public_mode}.{metric_summary}"
        )

    def _run_clinical(self, request: AgentRunRequest, context: "ExecutionContext") -> str:
        context.step("ClinicalManagerAgent", AgentStepKind.review, "Clinical manager accepted the objective.")
        study_id = request.study_id
        if study_id is None:
            if not request.study_file_paths:
                raise ValueError("Clinical or dual agent runs require study_id or study_file_paths.")
            study_payload = context.call_tool(
                "StudyIntakeAgent",
                "create_study_from_paths",
                {"paths": request.study_file_paths},
            )
            study_id = study_payload["study_id"]
            context.update_run(study_id=study_id)
            context.step(
                "StudyIntakeAgent",
                AgentStepKind.review,
                "Created local study from trusted paths.",
                payload={"study_id": study_id},
            )
        quality = context.call_tool("ImageQAAgent", "inspect_study_quality", {"study_id": study_id})
        quality_path = context.service.storage.write_artifact_json(
            f"{context.run.run_id}/clinical/study_quality.json",
            quality,
        )
        context.step(
            "ImageQAAgent",
            AgentStepKind.artifact_write,
            "Persisted study quality and warning summary.",
            artifact_path=quality_path,
            payload=quality,
        )
        analysis_payload = context.call_tool(
            "SounioAnalysisAgent",
            "analyze_study",
            {
                "study_id": study_id,
                "include_baseline_comparison": request.include_baseline_comparison,
                "model_family": request.model_family.value if hasattr(request.model_family, "value") else str(request.model_family),
            },
        )
        analysis = AnalysisResult.model_validate(analysis_payload)
        top_regions = [item.region for item in analysis.region_scores if item.affected][:3]
        explanation_lines = [
            f"# Clinical Copilot Summary",
            "",
            f"Study: {study_id}",
            f"ASPECTS: {analysis.aspects_score}",
            f"Global confidence: {analysis.global_confidence}",
            f"Affected regions: {', '.join(top_regions) if top_regions else 'none above threshold'}",
            "",
            "This output is for research support only and must not be used as a sole diagnostic decision.",
        ]
        explanation_path = context.service.storage.write_artifact_text(
            f"{context.run.run_id}/clinical/explanation.md",
            "\n".join(explanation_lines) + "\n",
        )
        context.step(
            "ExplanationAgent",
            AgentStepKind.artifact_write,
            "Wrote clinician-facing explanation artifact.",
            artifact_path=explanation_path,
        )
        disclaimer_path = context.service.storage.write_artifact_text(
            f"{context.run.run_id}/clinical/safety_disclosure.txt",
            "Research support output only. Maintain human review and local governance controls.\n",
        )
        context.step(
            "SafetyDisclosureAgent",
            AgentStepKind.artifact_write,
            "Persisted mandatory safety disclosure.",
            artifact_path=disclaimer_path,
        )
        return (
            f"Clinical Copilot analyzed study `{study_id}` with ASPECTS {analysis.aspects_score} "
            f"and saved explanation artifacts under {Path(explanation_path).parent}."
        )

    @staticmethod
    def _compose_final_output(research_summary: str, clinical_summary: str) -> str:
        parts = [part for part in (research_summary, clinical_summary) if part]
        if not parts:
            return "No workflow branch produced output."
        return "\n".join(parts)


class OpenAIAgentsRuntime:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def is_available() -> bool:
        return (
            Agent is not None
            and Runner is not None
            and RunConfig is not None
            and MCPServerManager is not None
            and MCPServerStdio is not None
            and bool(os.getenv("OPENAI_API_KEY"))
            and os.getenv("DARWIN_DISABLE_OFFICIAL_AGENT_RUNTIME", "0") != "1"
        )

    def execute(self, run: AgentRun, request: AgentRunRequest, service) -> AgentRun:
        if not self.is_available():  # pragma: no cover - networked path
            raise RuntimeError("Official OpenAI Agents runtime is not available.")
        context = ExecutionContext(service, run)
        context.step(
            "LabDirectorAgent",
            AgentStepKind.review,
            "Run started with the official OpenAI Agents SDK runtime.",
            status=AgentStepStatus.started,
        )
        report = asyncio.run(self._execute_async(context, request))
        updates: dict[str, Any] = {}
        if report.benchmark_run_id:
            updates["benchmark_run_id"] = report.benchmark_run_id
        if report.study_id:
            updates["study_id"] = report.study_id
        if report.research_brief_id:
            updates["research_brief_id"] = report.research_brief_id
        if report.artifact_paths:
            merged = list(context.run.artifact_paths)
            for artifact_path in report.artifact_paths:
                if artifact_path not in merged:
                    merged.append(artifact_path)
            updates["artifact_paths"] = merged
        if updates:
            context.update_run(**updates)
        return context.complete_run(report.summary)

    def synthesize(self, run: AgentRun, research_summary: str, clinical_summary: str) -> str:
        if not self.is_available():  # pragma: no cover - networked path
            return DeterministicAgentRuntime._compose_final_output(research_summary, clinical_summary)
        report = asyncio.run(
            self._run_director_synthesis(
                run,
                research_summary=research_summary,
                clinical_summary=clinical_summary,
                benchmark_run_id=None,
                study_id=None,
                research_brief_id=None,
                artifact_paths=[],
            )
        )
        return report.summary

    async def _execute_async(self, context: "ExecutionContext", request: AgentRunRequest) -> AgentWorkflowReport:
        run = context.run
        hooks = AgentLifecycleHooks(context)
        servers_by_agent = self._build_mcp_servers_by_agent()
        all_servers = [server for servers in servers_by_agent.values() for server in servers]
        with isolated_trace_processor(self.service.storage, run.run_id, run.trace_id):
            async with MCPServerManager(all_servers, strict=False, drop_failed_servers=True, connect_in_parallel=True) as manager:
                active_servers = {server.name: server for server in manager.active_servers}
                research_summary = ""
                clinical_summary = ""
                benchmark_run_id = None
                study_id = None
                research_brief_id = None
                artifact_paths: list[str] = []
                surface = request.surface.value if hasattr(request.surface, "value") else str(request.surface)

                if surface in {"research", "dual"}:
                    research_result = await self._run_research_branch(context, hooks, active_servers)
                    research_summary = research_result.summary
                    benchmark_run_id = research_result.benchmark_run_id or benchmark_run_id
                    research_brief_id = research_result.research_brief_id or research_brief_id
                    artifact_paths.extend(research_result.artifact_paths)

                if surface in {"clinical", "dual"}:
                    clinical_result = await self._run_clinical_branch(context, hooks, active_servers)
                    clinical_summary = clinical_result.summary
                    study_id = clinical_result.study_id or study_id
                    artifact_paths.extend(clinical_result.artifact_paths)

                final_report = await self._run_director_synthesis(
                    run,
                    research_summary=research_summary,
                    clinical_summary=clinical_summary,
                    benchmark_run_id=benchmark_run_id,
                    study_id=study_id,
                    research_brief_id=research_brief_id,
                    artifact_paths=artifact_paths,
                )
                return final_report

    def _build_mcp_servers_by_agent(self) -> dict[str, list[Any]]:
        descriptors = {descriptor.name: descriptor for descriptor in self.service.list_mcp_servers()}
        servers_by_agent: dict[str, list[Any]] = {}
        for agent_name, server_map in AGENT_SERVER_TOOL_ACCESS.items():
            servers: list[Any] = []
            for server_name, allowed_tools in server_map.items():
                descriptor = descriptors[server_name]
                params = {
                    "command": descriptor.command[0],
                    "args": descriptor.command[1:],
                    "cwd": str(Path.cwd()),
                    "env": dict(os.environ),
                }
                server = MCPServerStdio(
                    params=params,
                    cache_tools_list=True,
                    name=f"{server_name}:{agent_name}",
                    tool_filter={"allowed_tool_names": allowed_tools},
                    require_approval="never",
                    max_retry_attempts=1,
                )
                servers.append(server)
            servers_by_agent[agent_name] = servers
        return servers_by_agent

    def _agent_servers(self, active_servers: dict[str, Any], agent_name: str) -> list[Any]:
        prefix = f":{agent_name}"
        return [server for name, server in active_servers.items() if name.endswith(prefix)]

    def _run_config(self, run: AgentRun):
        return RunConfig(
            workflow_name="Darwin Stroke Lab Agentic OS",
            trace_id=run.trace_id,
            group_id=run.run_id,
            trace_metadata={"run_id": run.run_id, "surface": str(run.surface), "objective": run.objective[:200]},
            input_guardrails=[InputGuardrail(_input_guardrail)] if InputGuardrail is not None else [],
            output_guardrails=[OutputGuardrail(_output_guardrail)] if OutputGuardrail is not None else [],
            trace_include_sensitive_data=False,
        )

    async def _run_research_branch(self, context: "ExecutionContext", hooks, active_servers: dict[str, Any]) -> AgentWorkflowReport:
        run = context.run
        context.step("LabDirectorAgent", AgentStepKind.handoff, "Routing official runtime to ResearchManagerAgent.")
        research_manager = Agent(
            name="ResearchManagerAgent",
            instructions=(
                "You coordinate the research branch for one Darwin Stroke Lab run. "
                "Use the provided run_id to inspect safe run context, then delegate or summarize the branch status."
            ),
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            mcp_servers=self._agent_servers(active_servers, "ResearchManagerAgent"),
        )
        await Runner.run(
            research_manager,
            self._sanitize_prompt(f"Run research management check for run_id {run.run_id}."),
            max_turns=2,
            hooks=hooks,
            run_config=self._run_config(run),
        )

        context.step("ResearchManagerAgent", AgentStepKind.handoff, "Delegating literature scouting to LiteratureScout.")
        literature_agent = Agent(
            name="LiteratureScout",
            instructions=(
                "Use local corpus and public literature search to gather de-identified evidence for the current run. "
                "Summarize whether live public search was available and what the strongest source themes are."
            ),
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            mcp_servers=self._agent_servers(active_servers, "LiteratureScout"),
            output_type=AgentWorkflowReport,
        )
        literature_result = await Runner.run(
            literature_agent,
            self._sanitize_prompt(
                f"Use run_id {run.run_id}. Search the research sources and return a short research summary. "
                "Do not expose local file paths."
            ),
            max_turns=6,
            hooks=hooks,
            run_config=self._run_config(run),
        )

        qc_summary = ""
        request = self.service.get_agent_run_request(run.run_id)
        if request.dataset_manifest_path:
            context.step("ResearchManagerAgent", AgentStepKind.handoff, "Delegating manifest QC to DataQCAgent.")
            data_qc_agent = Agent(
                name="DataQCAgent",
                instructions=(
                    "Validate the configured manifest for the run and summarize the cohort composition without exposing any local paths."
                ),
                model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
                mcp_servers=self._agent_servers(active_servers, "DataQCAgent"),
                output_type=AgentWorkflowReport,
            )
            qc_result = await Runner.run(
                data_qc_agent,
                self._sanitize_prompt(
                    f"Use run_id {run.run_id}. Validate the manifest and summarize the configured evaluation cohort."
                ),
                max_turns=4,
                hooks=hooks,
                run_config=self._run_config(run),
            )
            qc_summary = qc_result.final_output_as(AgentWorkflowReport).summary

            context.step("ResearchManagerAgent", AgentStepKind.handoff, "Delegating benchmark execution to BenchmarkOperator.")
            benchmark_agent = Agent(
                name="BenchmarkOperator",
                instructions=(
                    "Launch the benchmark configured on this run and return the run id plus a concise metrics-oriented summary."
                ),
                model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
                mcp_servers=self._agent_servers(active_servers, "BenchmarkOperator"),
                output_type=AgentWorkflowReport,
            )
            benchmark_result = await Runner.run(
                benchmark_agent,
                self._sanitize_prompt(
                    f"Use run_id {run.run_id}. Run the configured benchmark and report the resulting benchmark_run_id."
                ),
                max_turns=4,
                hooks=hooks,
                run_config=self._run_config(run),
            )
            benchmark_report = benchmark_result.final_output_as(AgentWorkflowReport)
            persisted_run = self.service.get_agent_run(run.run_id)
            benchmark_run_id = benchmark_report.benchmark_run_id or persisted_run.benchmark_run_id
        else:
            benchmark_run_id = None

        context.step("ResearchManagerAgent", AgentStepKind.handoff, "Delegating manuscript packaging to ManuscriptAgent.")
        manuscript_agent = Agent(
            name="ManuscriptAgent",
            instructions=(
                "Compile the run-scoped research brief and return the research_brief_id plus a concise summary. "
                "Assume benchmark_run_id may already be attached to the persisted run."
            ),
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            mcp_servers=self._agent_servers(active_servers, "ManuscriptAgent"),
            output_type=AgentWorkflowReport,
        )
        manuscript_result = await Runner.run(
            manuscript_agent,
            self._sanitize_prompt(
                f"Use run_id {run.run_id}. Compile the research brief and return the research_brief_id."
            ),
            max_turns=4,
            hooks=hooks,
            run_config=self._run_config(run),
        )
        manuscript_report = manuscript_result.final_output_as(AgentWorkflowReport)
        persisted_run = self.service.get_agent_run(run.run_id)
        research_fragments = [
            literature_result.final_output_as(AgentWorkflowReport).summary,
            qc_summary,
            manuscript_report.summary,
        ]
        if benchmark_run_id:
            research_fragments.append(f"Benchmark run: {benchmark_run_id}.")
        return AgentWorkflowReport(
            summary=" ".join(fragment for fragment in research_fragments if fragment),
            benchmark_run_id=benchmark_run_id,
            research_brief_id=manuscript_report.research_brief_id or persisted_run.research_brief_id,
            artifact_paths=persisted_run.artifact_paths,
        )

    async def _run_clinical_branch(self, context: "ExecutionContext", hooks, active_servers: dict[str, Any]) -> AgentWorkflowReport:
        run = context.run
        context.step("LabDirectorAgent", AgentStepKind.handoff, "Routing official runtime to ClinicalManagerAgent.")
        clinical_manager = Agent(
            name="ClinicalManagerAgent",
            instructions=(
                "You coordinate the clinical branch for one Darwin Stroke Lab run. "
                "Confirm safe context and keep all outputs in research-support framing."
            ),
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            mcp_servers=self._agent_servers(active_servers, "ClinicalManagerAgent"),
        )
        await Runner.run(
            clinical_manager,
            self._sanitize_prompt(f"Run clinical management check for run_id {run.run_id}."),
            max_turns=2,
            hooks=hooks,
            run_config=self._run_config(run),
        )

        context.step("ClinicalManagerAgent", AgentStepKind.handoff, "Delegating intake to StudyIntakeAgent.")
        intake_agent = Agent(
            name="StudyIntakeAgent",
            instructions="Prepare or fetch the configured study for this run and return the study_id.",
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            mcp_servers=self._agent_servers(active_servers, "StudyIntakeAgent"),
            output_type=AgentWorkflowReport,
        )
        intake_result = await Runner.run(
            intake_agent,
            self._sanitize_prompt(f"Use run_id {run.run_id}. Prepare the configured study and return the study_id."),
            max_turns=3,
            hooks=hooks,
            run_config=self._run_config(run),
        )
        intake_report = intake_result.final_output_as(AgentWorkflowReport)
        persisted_run = self.service.get_agent_run(run.run_id)
        study_id = intake_report.study_id or persisted_run.study_id

        context.step("ClinicalManagerAgent", AgentStepKind.handoff, "Delegating quality checks to ImageQAAgent.")
        qa_agent = Agent(
            name="ImageQAAgent",
            instructions="Inspect the prepared study quality and summarize warnings plus volume characteristics.",
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            mcp_servers=self._agent_servers(active_servers, "ImageQAAgent"),
            output_type=AgentWorkflowReport,
        )
        qa_result = await Runner.run(
            qa_agent,
            self._sanitize_prompt(f"Use run_id {run.run_id}. Inspect the study quality and summarize the findings."),
            max_turns=3,
            hooks=hooks,
            run_config=self._run_config(run),
        )

        context.step("ClinicalManagerAgent", AgentStepKind.handoff, "Delegating analysis to SounioAnalysisAgent.")
        analysis_agent = Agent(
            name="SounioAnalysisAgent",
            instructions="Run the configured Sounio study analysis and summarize ASPECTS, confidence and regional findings.",
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            mcp_servers=self._agent_servers(active_servers, "SounioAnalysisAgent"),
            output_type=AgentWorkflowReport,
        )
        analysis_result = await Runner.run(
            analysis_agent,
            self._sanitize_prompt(f"Use run_id {run.run_id}. Analyze the study and summarize the result."),
            max_turns=4,
            hooks=hooks,
            run_config=self._run_config(run),
        )

        context.step("ClinicalManagerAgent", AgentStepKind.handoff, "Delegating explanation packaging to ExplanationAgent.")
        explanation_agent = Agent(
            name="ExplanationAgent",
            instructions=(
                "Generate the clinician-facing explanation and safety disclosure artifacts for this run, and summarize what was written."
            ),
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            mcp_servers=self._agent_servers(active_servers, "ExplanationAgent"),
            output_type=AgentWorkflowReport,
        )
        explanation_result = await Runner.run(
            explanation_agent,
            self._sanitize_prompt(
                f"Use run_id {run.run_id}. Generate the explanation and disclosure artifacts and summarize the result."
            ),
            max_turns=4,
            hooks=hooks,
            run_config=self._run_config(run),
        )
        explanation_report = explanation_result.final_output_as(AgentWorkflowReport)
        persisted_run = self.service.get_agent_run(run.run_id)

        context.step("ClinicalManagerAgent", AgentStepKind.handoff, "Delegating safety close-out to SafetyDisclosureAgent.")
        safety_agent = Agent(
            name="SafetyDisclosureAgent",
            instructions=(
                "Restate the final safety position for this run in one sentence. Always emphasize research support only."
            ),
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            output_type=AgentWorkflowReport,
        )
        safety_result = await Runner.run(
            safety_agent,
            self._sanitize_prompt(
                f"Use run_id {run.run_id}. Return one sentence that frames the clinical branch as research support only."
            ),
            max_turns=2,
            hooks=hooks,
            run_config=self._run_config(run),
        )
        safety_report = safety_result.final_output_as(AgentWorkflowReport)
        return AgentWorkflowReport(
            summary=" ".join(
                fragment
                for fragment in (
                    intake_report.summary,
                    qa_result.final_output_as(AgentWorkflowReport).summary,
                    analysis_result.final_output_as(AgentWorkflowReport).summary,
                    explanation_report.summary,
                    safety_report.summary,
                )
                if fragment
            ),
            study_id=study_id,
            artifact_paths=explanation_report.artifact_paths or persisted_run.artifact_paths,
        )

    async def _run_director_synthesis(
        self,
        run: AgentRun,
        *,
        research_summary: str,
        clinical_summary: str,
        benchmark_run_id: str | None,
        study_id: str | None,
        research_brief_id: str | None,
        artifact_paths: list[str],
    ) -> AgentWorkflowReport:
        director = Agent(
            name="LabDirectorAgent",
            instructions=(
                "Synthesize a final Darwin Stroke Lab report. Preserve research-only framing, mention completed branches, "
                "and do not reveal local file paths or protected identifiers."
            ),
            model=os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
            output_type=AgentWorkflowReport,
            input_guardrails=[InputGuardrail(_input_guardrail)] if InputGuardrail is not None else [],
            output_guardrails=[OutputGuardrail(_output_guardrail)] if OutputGuardrail is not None else [],
        )
        prompt = self._sanitize_prompt(
            (
                f"Objective: {run.objective}\n"
                f"Surface: {run.surface}\n"
                f"Research summary: {research_summary or 'none'}\n"
                f"Clinical summary: {clinical_summary or 'none'}\n"
                f"benchmark_run_id: {benchmark_run_id or 'none'}\n"
                f"study_id: {study_id or 'none'}\n"
                f"research_brief_id: {research_brief_id or 'none'}\n"
                f"artifact_paths: {artifact_paths}\n"
                "Return a concise final report and carry through the ids and artifact paths you received."
            )
        )
        result = await Runner.run(
            director,
            prompt,
            max_turns=3,
            run_config=self._run_config(run),
        )
        report = result.final_output_as(AgentWorkflowReport)
        return report.model_copy(
            update={
                "benchmark_run_id": report.benchmark_run_id or benchmark_run_id,
                "study_id": report.study_id or study_id,
                "research_brief_id": report.research_brief_id or research_brief_id,
                "artifact_paths": report.artifact_paths or artifact_paths,
            }
        )

    @staticmethod
    def _sanitize_prompt(prompt: str) -> str:
        return PublicPayloadFilter.sanitize_text(prompt)


def _input_guardrail(_, __, payload):
    sanitized = PublicPayloadFilter.sanitize_payload(payload)
    tripwire = PublicPayloadFilter.preview(payload) != PublicPayloadFilter.preview(sanitized)
    return GuardrailFunctionOutput(
        output_info={"sanitized_preview": PublicPayloadFilter.preview(sanitized)},
        tripwire_triggered=tripwire,
    )


def _output_guardrail(_, __, output):
    rendered = str(output)
    sanitized = PublicPayloadFilter.sanitize_text(rendered)
    missing_disclosure = "research support" not in rendered.lower()
    has_sensitive_leak = rendered != sanitized
    return GuardrailFunctionOutput(
        output_info={
            "has_sensitive_leak": has_sensitive_leak,
            "missing_disclosure": missing_disclosure,
        },
        tripwire_triggered=bool(has_sensitive_leak),
    )


if RunHooks is not None:
    class AgentLifecycleHooks(RunHooks):
        def __init__(self, context: "ExecutionContext"):
            self.context = context

        async def on_agent_start(self, context, agent) -> None:
            self.context.step(agent.name, AgentStepKind.review, f"{agent.name} started under the official runtime.")

        async def on_agent_end(self, context, agent, output) -> None:
            self.context.step(
                agent.name,
                AgentStepKind.review,
                f"{agent.name} finished under the official runtime.",
                payload={"output_preview": PublicPayloadFilter.preview(output)},
            )

        async def on_handoff(self, context, from_agent, to_agent) -> None:
            self.context.step(
                from_agent.name,
                AgentStepKind.handoff,
                f"Official runtime handoff from {from_agent.name} to {to_agent.name}.",
            )

        async def on_tool_start(self, context, agent, tool) -> None:
            self.context.step(
                agent.name,
                AgentStepKind.tool_call,
                f"{agent.name} is invoking tool {tool.name}.",
                status=AgentStepStatus.started,
                tool_name=tool.name,
            )

        async def on_tool_end(self, context, agent, tool, result) -> None:
            self.context.step(
                agent.name,
                AgentStepKind.tool_call,
                f"{agent.name} completed tool {tool.name}.",
                tool_name=tool.name,
                payload={"result_preview": PublicPayloadFilter.preview(result)},
            )
else:  # pragma: no cover - only used when official runtime imports fail
    class AgentLifecycleHooks:  # type: ignore[no-redef]
        def __init__(self, context: "ExecutionContext"):
            self.context = context


class AgentRunWorker:
    def __init__(self, service, poll_interval_seconds: float = 0.2):
        self.service = service
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="darwin-agent-worker", daemon=True)
        self.runtime = DeterministicAgentRuntime()
        self.official_runtime = OpenAIAgentsRuntime(service)

    def start(self) -> None:
        self.service.storage.requeue_inflight_agent_runs()
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            claimed = self.service.storage.claim_next_agent_run()
            if claimed is None:
                time.sleep(self.poll_interval_seconds)
                continue
            self._process_claimed_run(claimed)

    def run_run(self, run_id: str, *, job_id: str | None = None) -> None:
        run = self.service.storage.get_agent_run(run_id)
        if run.status == AgentRunStatus.queued:
            run = self.service.storage.update_agent_run(run_id, status=AgentRunStatus.running, error="")
        self._process_claimed_run(run, forced_job_id=job_id)

    def _process_claimed_run(self, claimed: AgentRun, *, forced_job_id: str | None = None) -> None:
        if forced_job_id:
            try:
                self.service.storage.update_job(forced_job_id, status=JobStatus.running)
            except KeyError:
                pass
        request = self.service.agent_requests.pop(claimed.run_id, None)
        if request is None and claimed.request_payload:
            request = AgentRunRequest.model_validate(claimed.request_payload)
        if request is None:
            claimed = self.service.storage.update_agent_run(
                claimed.run_id,
                status=AgentRunStatus.failed,
                error="Missing persisted request payload for agent run.",
            )
            if forced_job_id:
                self.service.storage.update_job(
                    forced_job_id,
                    status=JobStatus.failed,
                    result_payload={"backend": self.service.runtime.agent_backend, "agent_run_id": claimed.run_id},
                    error="Missing persisted request payload for agent run.",
                )
            return
        if self.official_runtime.is_available():
            try:
                self.official_runtime.execute(claimed, request, self.service)
                self._mirror_job_status(claimed.run_id, forced_job_id)
                return
            except Exception as exc:
                refreshed = self.service.storage.get_agent_run(claimed.run_id)
                warnings = list(refreshed.warnings)
                warnings.append(f"Official Agents SDK runtime failed; fell back to deterministic runtime: {exc}")
                self.service.storage.update_agent_run(claimed.run_id, warnings=warnings)
        self.runtime.execute(self.service.storage.get_agent_run(claimed.run_id), request, self.service)
        self._mirror_job_status(claimed.run_id, forced_job_id)

    def _mirror_job_status(self, run_id: str, job_id: str | None) -> None:
        if not job_id:
            return
        run = self.service.storage.get_agent_run(run_id)
        status_map = {
            AgentRunStatus.completed: JobStatus.completed,
            AgentRunStatus.failed: JobStatus.failed,
            AgentRunStatus.cancelled: JobStatus.cancelled,
        }
        payload = {
            "backend": self.service.runtime.agent_backend,
            "agent_run_id": run_id,
            "agent_run_status": run.status,
            "research_brief_id": run.research_brief_id,
            "benchmark_run_id": run.benchmark_run_id,
            "study_id": run.study_id,
            "final_output_present": bool(run.final_output),
            "cancel_requested": run.status == AgentRunStatus.cancelled,
        }
        mapped_status = status_map.get(run.status, JobStatus.running)
        self.service.storage.update_job(
            job_id,
            status=mapped_status,
            result_payload=payload,
            error=run.error,
        )


class ExecutionContext:
    def __init__(self, service, run: AgentRun):
        self.service = service
        self.run = run
        self.tool_specs = _build_tool_specs(service)

    def step(
        self,
        agent_name: str,
        kind: AgentStepKind,
        summary: str,
        *,
        status: AgentStepStatus = AgentStepStatus.completed,
        server_name: str | None = None,
        tool_name: str | None = None,
        artifact_path: str | None = None,
        duration_ms: float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentStep:
        step = AgentStep(
            step_id=uuid.uuid4().hex,
            run_id=self.run.run_id,
            agent_name=agent_name,
            kind=kind,
            status=status,
            summary=summary,
            server_name=server_name,
            tool_name=tool_name,
            artifact_path=artifact_path,
            duration_ms=duration_ms,
            payload=payload or {},
        )
        self.service.storage.save_agent_step(step)
        trace_event = TraceEvent(
            event_id=uuid.uuid4().hex,
            run_id=self.run.run_id,
            trace_id=self.run.trace_id,
            event_type="agent_step",
            payload=step.model_dump(mode="json"),
        )
        self.service.storage.save_trace_event(trace_event)
        self.service.storage.append_artifact_text(
            f"{self.run.run_id}/traces/trace_events.jsonl",
            json.dumps(trace_event.model_dump(mode="json"), ensure_ascii=True) + "\n",
        )
        if artifact_path:
            self._append_artifact_path(artifact_path)
        return step

    def call_tool(self, agent_name: str, tool_name: str, payload: dict[str, Any]) -> Any:
        self._check_cancelled()
        spec = self.tool_specs[tool_name]
        if spec.trust_level == ToolTrustLevel.public:
            sanitized_payload = PublicPayloadFilter.sanitize_payload(payload)
        else:
            sanitized_payload = payload
        audit = ToolAudit(
            audit_id=uuid.uuid4().hex,
            run_id=self.run.run_id,
            agent_name=agent_name,
            server_name=spec.server_name,
            tool_name=spec.name,
            trust_level=spec.trust_level,
            input_preview=PublicPayloadFilter.preview(payload),
            sanitized_input_preview=PublicPayloadFilter.preview(sanitized_payload),
        )
        self.service.storage.save_tool_audit(audit)
        if sanitized_payload != payload:
            self.step(
                agent_name,
                AgentStepKind.guardrail,
                f"Sanitized payload before calling public tool {spec.server_name}.{spec.name}.",
                server_name=spec.server_name,
                tool_name=spec.name,
                payload={
                    "original_preview": audit.input_preview,
                    "sanitized_preview": audit.sanitized_input_preview,
                },
            )
        try:
            self._ensure_tool_allowed(spec)
        except Exception as exc:
            blocked_audit = audit.model_copy(update={"blocked_reason": str(exc)})
            self.service.storage.save_tool_audit(blocked_audit)
            self.step(
                agent_name,
                AgentStepKind.guardrail,
                f"Blocked tool call to {spec.server_name}.{spec.name}: {exc}",
                status=AgentStepStatus.blocked,
                server_name=spec.server_name,
                tool_name=spec.name,
            )
            raise
        started = time.perf_counter()
        try:
            result = spec.func(**sanitized_payload)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000.0, 4)
            self.service.storage.save_tool_audit(audit.model_copy(update={"blocked_reason": str(exc)}))
            self.step(
                agent_name,
                AgentStepKind.tool_call,
                f"Tool call failed for {spec.server_name}.{spec.name}: {exc}",
                status=AgentStepStatus.failed,
                server_name=spec.server_name,
                tool_name=spec.name,
                duration_ms=duration_ms,
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000.0, 4)
        result_preview = PublicPayloadFilter.preview(result)
        self.service.storage.save_tool_audit(audit.model_copy(update={"result_preview": result_preview}))
        self.step(
            agent_name,
            AgentStepKind.tool_call,
            f"Completed tool call to {spec.server_name}.{spec.name}.",
            server_name=spec.server_name,
            tool_name=spec.name,
            duration_ms=duration_ms,
            payload={"input_preview": audit.sanitized_input_preview, "result_preview": result_preview},
        )
        return result

    def update_run(self, **updates) -> AgentRun:
        self.run = self.service.storage.update_agent_run(self.run.run_id, **updates)
        return self.run

    def complete_run(self, final_output: str) -> AgentRun:
        self.run = self.service.storage.update_agent_run(
            self.run.run_id,
            status=AgentRunStatus.completed,
            final_output=final_output,
        )
        self.step("LabDirectorAgent", AgentStepKind.review, "Run completed successfully.")
        return self.run

    def fail_run(self, error: str) -> AgentRun:
        self.run = self.service.storage.update_agent_run(
            self.run.run_id,
            status=AgentRunStatus.failed,
            error=error,
        )
        self.step(
            "LabDirectorAgent",
            AgentStepKind.review,
            f"Run failed: {error}",
            status=AgentStepStatus.failed,
        )
        return self.run

    def cancel_run(self, reason: str) -> AgentRun:
        self.run = self.service.storage.update_agent_run(
            self.run.run_id,
            status=AgentRunStatus.cancelled,
            error=reason,
        )
        self.step(
            "LabDirectorAgent",
            AgentStepKind.review,
            reason,
            status=AgentStepStatus.blocked,
        )
        return self.run

    def _append_artifact_path(self, artifact_path: str) -> None:
        artifact_paths = list(self.run.artifact_paths)
        if artifact_path not in artifact_paths:
            artifact_paths.append(artifact_path)
            self.update_run(artifact_paths=artifact_paths)

    def _check_cancelled(self) -> None:
        current = self.service.storage.get_agent_run(self.run.run_id)
        self.run = current
        if current.status == AgentRunStatus.cancelled:
            raise AgentRunCancelled

    def _ensure_tool_allowed(self, spec: ToolSpec) -> None:
        policy = self.run.safety_policy
        allowed = policy.local_tool_allowlist if spec.trust_level == ToolTrustLevel.local else policy.public_tool_allowlist
        if spec.name not in allowed:
            raise ValueError(f"Tool {spec.name} is not allowed by the current safety policy.")


def default_safety_policy(service) -> SafetyPolicy:
    local_allowlist: list[str] = []
    public_allowlist: list[str] = []
    for server_name, tools in SERVER_TOOLSETS.items():
        if server_name == "darwin-research":
            public_allowlist.extend(tools)
        else:
            local_allowlist.extend(tools)
    return SafetyPolicy(
        privacy_mode=PrivacyMode.local_deidentified,
        autonomy_mode=AutonomyMode.autonomous_lab,
        remote_tool_policy="public_only_deidentified",
        write_scope=[str(service.storage.root), str(service.storage.artifact_dir), str(service.storage.study_dir)],
        public_tool_allowlist=sorted(public_allowlist),
        local_tool_allowlist=sorted(local_allowlist),
    )


def bootstrap_agentic_environment(service) -> list[MCPServerDescriptor]:
    descriptors = register_builtin_mcp_servers(service)
    for descriptor in descriptors:
        health = mcp_server_health(descriptor.name, service)
        service.storage.save_mcp_server(
            descriptor.model_copy(update={"health_status": health.health_status, "notes": descriptor.notes + health.notes})
        )
    return service.storage.list_mcp_servers()


def build_agent_run(run_request: AgentRunRequest, service) -> AgentRun:
    return AgentRun(
        run_id=uuid.uuid4().hex,
        objective=run_request.objective,
        surface=run_request.surface,
        status=AgentRunStatus.queued,
        root_agent="LabDirectorAgent",
        trace_id=f"trace-{uuid.uuid4().hex}",
        session_id=f"session-{uuid.uuid4().hex}",
        safety_policy=default_safety_policy(service),
        request_payload=run_request.model_dump(mode="json"),
        dataset_manifest_path=run_request.dataset_manifest_path,
        external_test_manifest_path=run_request.external_test_manifest_path,
    )


def _coerce_research_sources(items: list[dict[str, Any]]) -> list[ResearchSource]:
    return [
        ResearchSource(
            title=item.get("title", "Untitled"),
            url=item.get("url", ""),
            summary=item.get("summary", ""),
            tags=tuple(item.get("tags", ()) or ()),
            source_type=item.get("source_type", "unknown"),
        )
        for item in items
    ]


def _build_tool_specs(service) -> dict[str, ToolSpec]:
    return {
        "search_local_corpus": ToolSpec(
            name="search_local_corpus",
            server_name="darwin-research",
            trust_level=ToolTrustLevel.public,
            write_capable=False,
            func=lambda question, limit=6: [source.__dict__ for source in search_research_corpus(question, limit=limit)],
        ),
        "search_public_literature": ToolSpec(
            name="search_public_literature",
            server_name="darwin-research",
            trust_level=ToolTrustLevel.public,
            write_capable=False,
            func=lambda question, limit=5, run_id=None, trace_id=None: service.search_public_literature(
                question,
                limit=limit,
                run_id=run_id,
                trace_id=trace_id,
            ).model_dump(mode="json"),
        ),
        "write_evidence_table": ToolSpec(
            name="write_evidence_table",
            server_name="darwin-research",
            trust_level=ToolTrustLevel.public,
            write_capable=True,
            func=lambda question, relative_path, sources=None: service.storage.write_artifact_text(
                relative_path,
                render_evidence_table(
                    question,
                    _coerce_research_sources(sources or []) if sources else search_research_corpus(question),
                ),
            ),
        ),
        "protocol_actions": ToolSpec(
            name="protocol_actions",
            server_name="darwin-research",
            trust_level=ToolTrustLevel.public,
            write_capable=False,
            func=lambda question, sources=None: recommend_protocol_actions(
                question,
                _coerce_research_sources(sources or []) if sources else search_research_corpus(question),
            ),
        ),
        "validate_manifest": ToolSpec(
            name="validate_manifest",
            server_name="darwin-dataset",
            trust_level=ToolTrustLevel.local,
            write_capable=False,
            func=lambda manifest_path, required_split=None: {
                "valid": not validate_issues,
                "issues": validate_issues,
            }
            if not (validate_issues := service.validate_manifest(manifest_path, required_split))
            else {"valid": False, "issues": validate_issues},
        ),
        "cohort_summary": ToolSpec(
            name="cohort_summary",
            server_name="darwin-dataset",
            trust_level=ToolTrustLevel.local,
            write_capable=False,
            func=lambda manifest_path, split="test": service.cohort_summary(manifest_path, split),
        ),
        "run_benchmark": ToolSpec(
            name="run_benchmark",
            server_name="darwin-benchmark",
            trust_level=ToolTrustLevel.local,
            write_capable=True,
            func=lambda manifest_path, external_test_manifest_path=None, seed=13: service.run_benchmark_from_paths(
                manifest_path,
                external_test_manifest_path,
                seed=seed,
            ).model_dump(mode="json"),
        ),
        "create_study_from_paths": ToolSpec(
            name="create_study_from_paths",
            server_name="darwin-clinical",
            trust_level=ToolTrustLevel.local,
            write_capable=True,
            func=lambda paths: service.create_study_from_paths([Path(item).expanduser().resolve() for item in paths]).model_dump(mode="json"),
        ),
        "inspect_study_quality": ToolSpec(
            name="inspect_study_quality",
            server_name="darwin-clinical",
            trust_level=ToolTrustLevel.local,
            write_capable=False,
            func=lambda study_id: service.inspect_study_quality(study_id),
        ),
        "analyze_study": ToolSpec(
            name="analyze_study",
            server_name="darwin-clinical",
            trust_level=ToolTrustLevel.local,
            write_capable=True,
            func=lambda study_id, include_baseline_comparison=True, model_family="sounio_hypercomplex": service.analyze_study_from_values(
                study_id=study_id,
                include_baseline_comparison=include_baseline_comparison,
                model_family=model_family,
            ).model_dump(mode="json"),
        ),
    }
