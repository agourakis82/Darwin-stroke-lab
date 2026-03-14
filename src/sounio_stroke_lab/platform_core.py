from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from sounio_stroke_lab.agentic import AgentRunWorker, bootstrap_agentic_environment, build_agent_run
from sounio_stroke_lab.config import get_kubernetes_settings
from sounio_stroke_lab.live_research import PublicResearchBundle, bundle_to_research_sources, search_public_research
from sounio_stroke_lab.research_corpus import (
    merge_research_sources,
    recommend_protocol_actions,
    render_evidence_table,
    search_research_corpus,
)
from sounio_stroke_lab.schemas import (
    AgentRun,
    AgentRunRequest,
    AgentRunStatus,
    AnalyzeStudyRequest,
    MCPServerHealth,
    MCPServerHealthStatus,
    RecordOwnerType,
    ResearchBrief,
    StudyRecord,
    TraceEvent,
)
from sounio_stroke_lab.trace_audit import isolated_trace_processor

if TYPE_CHECKING:
    from sounio_stroke_lab.service import StrokeResearchService


class PlatformCore:
    def __init__(self, service: "StrokeResearchService"):
        self.service = service
        self.agent_backend_name = service.runtime.agent_backend
        self.agent_requests: dict[str, AgentRunRequest] = {}
        self.service.agent_requests = self.agent_requests
        bootstrap_agentic_environment(self.service)
        self.agent_worker = AgentRunWorker(self.service) if self.agent_backend_name == "local" else None
        self.service.agent_worker = self.agent_worker
        if self.agent_worker is not None:
            self.agent_worker.start()

    def shutdown(self) -> None:
        if self.agent_worker is not None:
            self.agent_worker.stop()

    def create_agent_run(self, request: AgentRunRequest) -> AgentRun:
        run = build_agent_run(request, self.service)
        if self.agent_backend_name in {"k8s", "kubernetes", "cluster"}:
            self._validate_kubernetes_agent_request(run, request)
        self.service.storage.create_agent_run(run)
        self.agent_requests[run.run_id] = request
        if self.agent_backend_name in {"k8s", "kubernetes", "cluster"}:
            job = self.service.job_backend.submit_agent_run(run)
            run = self.service.storage.update_agent_run(run.run_id, job_id=job.job_id)
        return run

    def get_agent_run_request(self, run_id: str) -> AgentRunRequest:
        run = self.get_agent_run(run_id)
        return AgentRunRequest.model_validate(run.request_payload)

    def get_agent_run(self, run_id: str) -> AgentRun:
        return self.service.storage.get_agent_run(run_id)

    def list_agent_steps(self, run_id: str):
        return self.service.storage.list_agent_steps(run_id)

    def cancel_agent_run(self, run_id: str) -> AgentRun:
        run = self.service.storage.get_agent_run(run_id)
        if run.status in {AgentRunStatus.completed, AgentRunStatus.failed, AgentRunStatus.cancelled}:
            return run
        if run.job_id:
            try:
                self.service.cancel_job(run.job_id)
            except Exception:
                pass
        return self.service.storage.update_agent_run(
            run_id,
            status=AgentRunStatus.cancelled,
            error="Cancellation requested.",
        )

    def list_mcp_servers(self):
        return self.service.storage.list_mcp_servers()

    def get_mcp_server_health(self, name: str) -> MCPServerHealth:
        descriptor = self.service.storage.get_mcp_server(name)
        return MCPServerHealth(
            name=descriptor.name,
            health_status=descriptor.health_status or MCPServerHealthStatus.healthy,
            available_tools=descriptor.allowed_tools,
            notes=descriptor.notes,
        )

    def get_agent_run_context_summary(self, run_id: str) -> dict:
        run = self.get_agent_run(run_id)
        request = self.get_agent_run_request(run_id)
        return {
            "run_id": run.run_id,
            "job_id": run.job_id,
            "objective": run.objective,
            "surface": run.surface,
            "status": run.status,
            "root_agent": run.root_agent,
            "trace_id": run.trace_id,
            "dataset_manifest_available": bool(request.dataset_manifest_path),
            "external_test_manifest_available": bool(request.external_test_manifest_path),
            "study_available": bool(run.study_id or request.study_id or request.study_file_paths),
            "study_id": run.study_id or request.study_id,
            "model_family": request.model_family,
            "include_baseline_comparison": request.include_baseline_comparison,
            "safety_policy": run.safety_policy.model_dump(mode="json"),
            "warnings": run.warnings,
        }

    def _validate_kubernetes_agent_request(self, run: AgentRun, request: AgentRunRequest) -> None:
        shared_prefixes = get_kubernetes_settings().shared_path_prefixes
        if request.dataset_manifest_path and not self._is_cluster_shared_path(request.dataset_manifest_path, shared_prefixes):
            raise ValueError(
                f"Kubernetes agent runs require dataset manifests under shared prefixes {shared_prefixes}; "
                f"got {request.dataset_manifest_path}."
            )
        for path in request.study_file_paths:
            if not self._is_cluster_shared_path(path, shared_prefixes):
                raise ValueError(
                    f"Kubernetes agent runs require study files under shared prefixes {shared_prefixes}; got {path}."
                )
        if request.study_id:
            study = self.service.storage.get_study(request.study_id)
            for path in study.files:
                if not self._is_cluster_shared_path(path, shared_prefixes):
                    raise ValueError(
                        f"Study {request.study_id} is not cluster-accessible; file {path} is outside {shared_prefixes}."
                    )

    @staticmethod
    def _is_cluster_shared_path(raw_path: str, prefixes: tuple[str, ...]) -> bool:
        normalized = str(Path(raw_path).expanduser())
        return any(normalized == prefix or normalized.startswith(f"{prefix.rstrip('/')}/") for prefix in prefixes)

    def list_trace_events(self, run_id: str) -> list[TraceEvent]:
        return self.service.storage.list_trace_events(run_id)

    def search_public_literature(
        self,
        question: str,
        limit: int = 5,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> PublicResearchBundle:
        if run_id and trace_id:
            with isolated_trace_processor(self.service.storage, run_id, trace_id):
                return search_public_research(question, limit=limit, run_id=run_id, trace_id=trace_id)
        return search_public_research(question, limit=limit, run_id=run_id, trace_id=trace_id)

    @staticmethod
    def public_bundle_to_sources(bundle: PublicResearchBundle):
        return bundle_to_research_sources(bundle)

    def prepare_study_for_agent_run(self, run_id: str) -> StudyRecord:
        run = self.get_agent_run(run_id)
        request = self.get_agent_run_request(run_id)
        if run.study_id:
            return self.service.storage.get_study(run.study_id)
        if request.study_id:
            record = self.service.storage.get_study(request.study_id)
            self.service.storage.update_agent_run(run_id, study_id=record.study_id)
            return record
        if request.study_file_paths:
            record = self.service.stroke_lab.create_study_from_paths(
                [Path(item).expanduser().resolve() for item in request.study_file_paths]
            )
            self.service.storage.update_agent_run(run_id, study_id=record.study_id)
            return record
        raise ValueError(f"Agent run {run_id} does not have a study_id or study_file_paths.")

    def run_benchmark_for_agent_run(self, run_id: str):
        request = self.get_agent_run_request(run_id)
        if not request.dataset_manifest_path:
            raise ValueError(f"Agent run {run_id} does not have a dataset manifest configured.")
        run = self.service.stroke_lab.run_benchmark_from_paths(
            manifest_path=request.dataset_manifest_path,
            external_test_manifest_path=request.external_test_manifest_path,
            seed=request.seed,
            train_split=request.train_split,
            test_split=request.test_split,
        )
        self.service.storage.update_agent_run(run_id, benchmark_run_id=run.run_id)
        return run

    def validate_agent_run_manifest(self, run_id: str) -> dict:
        request = self.get_agent_run_request(run_id)
        if not request.dataset_manifest_path:
            raise ValueError(f"Agent run {run_id} does not have a dataset manifest configured.")
        issues = self.service.stroke_lab.validate_manifest(
            request.dataset_manifest_path,
            required_split=request.test_split,
        )
        return {"valid": not issues, "issues": issues}

    def summarize_agent_run_cohort(self, run_id: str) -> dict:
        request = self.get_agent_run_request(run_id)
        if not request.dataset_manifest_path:
            raise ValueError(f"Agent run {run_id} does not have a dataset manifest configured.")
        return self.service.stroke_lab.cohort_summary(request.dataset_manifest_path, split=request.test_split)

    def analyze_agent_run_study(self, run_id: str):
        request = self.get_agent_run_request(run_id)
        study = self.prepare_study_for_agent_run(run_id)
        return self.service.stroke_lab.analyze_study(
            study.study_id,
            AnalyzeStudyRequest(
                model_family=request.model_family,
                include_baseline_comparison=request.include_baseline_comparison,
            ),
        )

    def inspect_agent_run_study_quality(self, run_id: str) -> dict:
        study = self.prepare_study_for_agent_run(run_id)
        return self.service.stroke_lab.inspect_study_quality(study.study_id)

    def compile_research_brief_for_agent_run(self, run_id: str) -> ResearchBrief:
        run = self.get_agent_run(run_id)
        request = self.get_agent_run_request(run_id)
        question = request.research_question or request.objective
        local_sources = search_research_corpus(question, limit=6)
        public_bundle = self.search_public_literature(question, limit=5, run_id=run.run_id, trace_id=run.trace_id)
        combined_sources = merge_research_sources(
            self.public_bundle_to_sources(public_bundle),
            local_sources,
            limit=10,
        )
        evidence_table_path = self.service.storage.write_artifact_text(
            f"{run.run_id}/research/evidence_table.md",
            render_evidence_table(question, combined_sources),
        )
        source_bundle_path = self.service.storage.write_artifact_json(
            f"{run.run_id}/research/source_bundle.json",
            {
                "question": question,
                "public_bundle": public_bundle.model_dump(mode="json"),
                "source_links": [source.url for source in combined_sources],
            },
        )
        self.service.storage.register_artifact(
            RecordOwnerType.agent_run,
            run.run_id,
            name="research_evidence_table",
            kind="markdown",
            path=evidence_table_path,
            description="Merged local plus public evidence table for the agent research branch.",
        )
        self.service.storage.register_artifact(
            RecordOwnerType.agent_run,
            run.run_id,
            name="research_source_bundle",
            kind="json",
            path=source_bundle_path,
            description="Persisted local/public source bundle used to assemble the research brief.",
        )
        brief = self.create_research_brief_from_values(
            question=question,
            benchmark_run_id=run.benchmark_run_id,
            dataset_manifest_path=request.dataset_manifest_path,
            source_links=[source.url for source in combined_sources],
            protocol_recommendations=recommend_protocol_actions(question, combined_sources),
            evidence_table_path=evidence_table_path,
        )
        artifact_paths = list(run.artifact_paths)
        for path in (evidence_table_path,):
            if path not in artifact_paths:
                artifact_paths.append(path)
        self.service.storage.update_agent_run(run_id, research_brief_id=brief.brief_id, artifact_paths=artifact_paths)
        return brief

    def generate_clinical_summary_for_agent_run(self, run_id: str) -> dict:
        run = self.get_agent_run(run_id)
        study = self.prepare_study_for_agent_run(run_id)
        analysis = self.analyze_agent_run_study(run_id)
        top_regions = [item.region for item in analysis.region_scores if item.affected][:3]
        explanation_lines = [
            "# Clinical Copilot Summary",
            "",
            f"Study: {study.study_id}",
            f"ASPECTS: {analysis.aspects_score}",
            f"Global confidence: {analysis.global_confidence}",
            f"Affected regions: {', '.join(top_regions) if top_regions else 'none above threshold'}",
            "",
            "This output is for research support only and must not be used as a sole diagnostic decision.",
        ]
        explanation_path = self.service.storage.write_artifact_text(
            f"{run.run_id}/clinical/explanation.md",
            "\n".join(explanation_lines) + "\n",
        )
        disclosure_path = self.service.storage.write_artifact_text(
            f"{run.run_id}/clinical/safety_disclosure.txt",
            "Research support output only. Maintain human review and local governance controls.\n",
        )
        self.service.storage.register_artifact(
            RecordOwnerType.agent_run,
            run.run_id,
            name="clinical_explanation",
            kind="markdown",
            path=str(explanation_path),
            description="Human-readable clinical copilot explanation generated for the run study.",
        )
        self.service.storage.register_artifact(
            RecordOwnerType.agent_run,
            run.run_id,
            name="clinical_safety_disclosure",
            kind="text",
            path=str(disclosure_path),
            description="Safety disclaimer paired with the clinical summary artifact.",
        )
        artifact_paths = list(run.artifact_paths)
        for path in (str(explanation_path), str(disclosure_path)):
            if path not in artifact_paths:
                artifact_paths.append(path)
        self.service.storage.update_agent_run(run_id, artifact_paths=artifact_paths)
        return {
            "study_id": study.study_id,
            "analysis": analysis.model_dump(mode="json"),
            "explanation_artifact": str(explanation_path),
            "safety_disclosure_artifact": str(disclosure_path),
        }

    def create_research_brief_from_values(
        self,
        question: str,
        benchmark_run_id: str | None = None,
        campaign_id: str | None = None,
        dataset_manifest_path: str | None = None,
        source_links: list[str] | None = None,
        protocol_recommendations: list[str] | None = None,
        evidence_table_path: str | None = None,
    ) -> ResearchBrief:
        brief_id = uuid.uuid4().hex
        question_slug = brief_id[:8]
        if evidence_table_path is None:
            evidence_table_path = self.service.storage.write_artifact_text(
                f"research_briefs/{question_slug}/evidence_table.md",
                f"# Evidence Table\n\nQuestion: {question}\n",
            )
        claim_summary = "Research brief generated from local corpus"
        if campaign_id:
            campaign = self.service.get_campaign(campaign_id)
            best_run = campaign.summary.get("best_run") or {}
            benchmark_run_id = benchmark_run_id or best_run.get("benchmark_run_id")
            dataset_manifest_path = dataset_manifest_path or (
                campaign.benchmark_specs[0].request.dataset_manifest_path if campaign.benchmark_specs else None
            )
            claim_summary = (
                "Research brief generated from campaign evidence. "
                f"Best run `{best_run.get('label', 'n/a')}` with leading model "
                f"`{best_run.get('leading_model', 'n/a')}`."
            )
        if benchmark_run_id:
            run = self.service.stroke_lab.get_benchmark_run(benchmark_run_id)
            sounio_metrics = run.metrics.get("sounio_hypercomplex", {})
            claim_summary = (
                "Research brief generated from local corpus and benchmark evidence. "
                f"Sounio AUC={sounio_metrics.get('auc', {}).get('value', 'n/a')} "
                f"Dice={sounio_metrics.get('isles_dice', {}).get('value', 'n/a')}."
            )
        if campaign_id and protocol_recommendations is None:
            campaign = self.service.get_campaign(campaign_id)
            protocol_recommendations = list(campaign.summary.get("next_experiment_recommendations", []))
        brief = ResearchBrief(
            brief_id=brief_id,
            question=question,
            inclusion_criteria=[
                "Primary sources on MCP, Agents SDK, and stroke benchmark design.",
                "Evidence linked to the current dataset manifest or benchmark run when available.",
            ],
            exclusion_criteria=[
                "Remote public tools receiving raw local study paths or protected imaging payloads.",
            ],
            evidence_table_path=evidence_table_path,
            claim_summary=claim_summary,
            protocol_recommendations=protocol_recommendations or [],
            supporting_run_id=benchmark_run_id,
            supporting_campaign_id=campaign_id,
            dataset_manifest_path=dataset_manifest_path,
            source_links=source_links or [],
        )
        self.service.storage.save_research_brief(brief)
        self.service.storage.register_artifact(
            RecordOwnerType.research_brief,
            brief.brief_id,
            name="research_brief_evidence_table",
            kind="markdown",
            path=evidence_table_path,
            description="Evidence table attached to the persisted research brief.",
        )
        return brief

    def create_research_brief(
        self,
        question: str,
        benchmark_run_id: str | None = None,
        campaign_id: str | None = None,
        dataset_manifest_path: str | None = None,
    ) -> ResearchBrief:
        return self.create_research_brief_from_values(
            question=question,
            benchmark_run_id=benchmark_run_id,
            campaign_id=campaign_id,
            dataset_manifest_path=dataset_manifest_path,
        )
