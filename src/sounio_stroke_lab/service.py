from __future__ import annotations

from pathlib import Path

from sounio_stroke_lab.benchmark import BenchmarkHarness
from sounio_stroke_lab.campaigns import CampaignCoordinator
from sounio_stroke_lab.config import get_runtime_config, get_storage_root
from sounio_stroke_lab.job_backend import BenchmarkWorker, build_job_backend
from sounio_stroke_lab.live_research import PublicResearchBundle
from sounio_stroke_lab.platform_core import PlatformCore
from sounio_stroke_lab.portfolio import PortfolioCoordinator
from sounio_stroke_lab.programs import ProgramCoordinator
from sounio_stroke_lab.schemas import (
    AgentRun,
    AgentRunRequest,
    AnalysisResult,
    AnalyzeStudyRequest,
    ArtifactRef,
    BenchmarkRequest,
    BenchmarkRun,
    CampaignExperimentPlan,
    CampaignExperimentPlanReport,
    CampaignExperimentPlanAgentLaunchRequest,
    CampaignExperimentPlanLaunchRequest,
    CampaignFollowUpLaunchRequest,
    CampaignFollowUpProposal,
    CampaignRecord,
    CampaignRequest,
    JobRecord,
    MCPServerHealth,
    PortfolioReport,
    ProgramPortfolioReport,
    ProgramCampaignAttachRequest,
    ProgramRecord,
    ProgramRequest,
    RecordOwnerType,
    ResearchBrief,
    TraceEvent,
    StudyRecord,
)
from sounio_stroke_lab.stroke_lab import StrokeLab
from sounio_stroke_lab.storage import StorageManager


class StrokeResearchService:
    def __init__(self, storage_root: Path | None = None, *, start_benchmark_worker: bool | None = None):
        runtime = get_runtime_config(storage_root=storage_root or get_storage_root(), start_embedded_benchmark_worker=start_benchmark_worker)
        self.runtime = runtime
        self.storage = StorageManager(runtime.storage_root)
        self.storage.initialize()
        self.benchmark = BenchmarkHarness(self.storage)
        self.job_backend = build_job_backend(self.storage, self.benchmark)
        self.benchmark_worker = BenchmarkWorker(self.storage, self.benchmark)
        if runtime.start_embedded_benchmark_worker:
            self.benchmark_worker.start_in_background()
        self.job_backend.start()
        self.campaigns = CampaignCoordinator(self.storage, self.job_backend)
        self.programs = ProgramCoordinator(self.storage, self.campaigns)
        self.portfolio = PortfolioCoordinator(self.storage, self.campaigns, self.programs)
        self.stroke_lab = StrokeLab(self.storage, self.benchmark, self.job_backend)
        self.platform_core = PlatformCore(self)

    def shutdown(self) -> None:
        self.benchmark_worker.stop_background()
        self.job_backend.shutdown()
        self.platform_core.shutdown()

    async def create_study_from_uploads(self, uploads) -> StudyRecord:
        return await self.stroke_lab.create_study_from_uploads(uploads)

    def create_study_from_paths(self, paths: list[Path]) -> StudyRecord:
        return self.stroke_lab.create_study_from_paths(paths)

    def analyze_study(self, study_id: str, request: AnalyzeStudyRequest) -> AnalysisResult:
        return self.stroke_lab.analyze_study(study_id, request)

    def analyze_study_from_values(
        self,
        study_id: str,
        model_family: str,
        include_baseline_comparison: bool = True,
    ) -> AnalysisResult:
        return self.stroke_lab.analyze_study_from_values(study_id, model_family, include_baseline_comparison)

    def get_analysis_result(self, study_id: str) -> AnalysisResult:
        return self.stroke_lab.get_analysis_result(study_id)

    def run_benchmark(self, request: BenchmarkRequest) -> BenchmarkRun:
        return self.stroke_lab.run_benchmark(request)

    def submit_benchmark(self, request: BenchmarkRequest) -> JobRecord:
        return self.job_backend.submit_benchmark(request)

    def get_benchmark_run(self, run_id: str) -> BenchmarkRun:
        return self.stroke_lab.get_benchmark_run(run_id)

    def create_campaign(self, request: CampaignRequest) -> CampaignRecord:
        return self.campaigns.create_campaign(request)

    def get_campaign(self, campaign_id: str) -> CampaignRecord:
        return self.campaigns.get_campaign(campaign_id)

    def list_campaigns(self) -> list[CampaignRecord]:
        return self.campaigns.list_campaigns()

    def get_campaign_portfolio(self) -> PortfolioReport:
        return self.portfolio.get_campaign_portfolio()

    def get_program_portfolio(self) -> ProgramPortfolioReport:
        return self.portfolio.get_program_portfolio()

    def create_program(self, request: ProgramRequest) -> ProgramRecord:
        return self.programs.create_program(request)

    def get_program(self, program_id: str) -> ProgramRecord:
        return self.programs.get_program(program_id)

    def list_programs(self) -> list[ProgramRecord]:
        return self.programs.list_programs()

    def attach_program_campaign(self, program_id: str, request: ProgramCampaignAttachRequest) -> ProgramRecord:
        return self.programs.attach_campaign(program_id, request)

    def create_program_campaign(self, program_id: str, request: CampaignRequest) -> CampaignRecord:
        return self.programs.create_campaign(program_id, request)

    def get_campaign_follow_up_proposals(self, campaign_id: str) -> list[CampaignFollowUpProposal]:
        return self.campaigns.get_follow_up_proposals(campaign_id)

    def launch_campaign_follow_up(
        self,
        campaign_id: str,
        request: CampaignFollowUpLaunchRequest | None = None,
    ) -> CampaignRecord:
        return self.campaigns.launch_follow_up_campaign(campaign_id, request)

    def get_campaign_experiment_plans(self, campaign_id: str) -> list[CampaignExperimentPlan]:
        return self.campaigns.get_experiment_plans(campaign_id)

    def get_campaign_experiment_plan_reports(self, campaign_id: str) -> list[CampaignExperimentPlanReport]:
        return self.campaigns.get_experiment_plan_reports(campaign_id)

    def launch_campaign_experiment_plan(
        self,
        campaign_id: str,
        plan_id: str,
        request: CampaignExperimentPlanLaunchRequest | None = None,
    ) -> CampaignRecord:
        return self.campaigns.launch_experiment_plan(campaign_id, plan_id, request)

    def launch_campaign_experiment_plan_agent_run(
        self,
        campaign_id: str,
        plan_id: str,
        request: CampaignExperimentPlanAgentLaunchRequest | None = None,
    ) -> AgentRun:
        request = request or CampaignExperimentPlanAgentLaunchRequest()
        plan = self.campaigns.get_experiment_plan(campaign_id, plan_id)
        payload = dict(plan.recommended_agent_request)
        if request.objective:
            payload["objective"] = request.objective
            payload["research_question"] = request.objective
        if request.notes:
            existing_notes = payload.get("notes", "")
            payload["notes"] = "\n".join(part for part in (existing_notes, request.notes.strip()) if part)
        agent_request = AgentRunRequest.model_validate(payload)
        return self.platform_core.create_agent_run(agent_request)

    def create_campaign_brief(self, campaign_id: str, question: str | None = None) -> ResearchBrief:
        campaign = self.get_campaign(campaign_id)
        brief_question = question or (
            campaign.objective or f"Summarize the evidence and next experiments for campaign {campaign.name}."
        )
        return self.platform_core.create_research_brief(
            question=brief_question,
            campaign_id=campaign_id,
        )

    def run_benchmark_from_paths(
        self,
        manifest_path: str,
        external_test_manifest_path: str | None = None,
        seed: int = 13,
        train_split: str = "train",
        test_split: str = "test",
    ) -> BenchmarkRun:
        return self.stroke_lab.run_benchmark_from_paths(
            manifest_path=manifest_path,
            external_test_manifest_path=external_test_manifest_path,
            seed=seed,
            train_split=train_split,
            test_split=test_split,
        )

    def validate_manifest(self, manifest_path: str, required_split: str | None = None) -> list[str]:
        return self.stroke_lab.validate_manifest(manifest_path, required_split=required_split)

    def cohort_summary(self, manifest_path: str, split: str = "test") -> dict:
        return self.stroke_lab.cohort_summary(manifest_path, split=split)

    def inspect_study_quality(self, study_id: str) -> dict:
        return self.stroke_lab.inspect_study_quality(study_id)

    def create_agent_run(self, request: AgentRunRequest) -> AgentRun:
        return self.platform_core.create_agent_run(request)

    def get_agent_run_request(self, run_id: str) -> AgentRunRequest:
        return self.platform_core.get_agent_run_request(run_id)

    def get_agent_run(self, run_id: str) -> AgentRun:
        return self.platform_core.get_agent_run(run_id)

    def list_agent_steps(self, run_id: str):
        return self.platform_core.list_agent_steps(run_id)

    def cancel_agent_run(self, run_id: str) -> AgentRun:
        return self.platform_core.cancel_agent_run(run_id)

    def list_mcp_servers(self):
        return self.platform_core.list_mcp_servers()

    def get_mcp_server_health(self, name: str) -> MCPServerHealth:
        return self.platform_core.get_mcp_server_health(name)

    def get_agent_run_context_summary(self, run_id: str) -> dict:
        return self.platform_core.get_agent_run_context_summary(run_id)

    def list_trace_events(self, run_id: str) -> list[TraceEvent]:
        return self.platform_core.list_trace_events(run_id)

    def get_job(self, job_id: str) -> JobRecord:
        return self.job_backend.get_job(job_id)

    def cancel_job(self, job_id: str) -> JobRecord:
        return self.job_backend.cancel_job(job_id)

    def render_job_manifest(self, job_id: str) -> dict:
        return self.job_backend.render_job_manifest(job_id)

    def list_artifacts(self, owner_type: RecordOwnerType, owner_id: str) -> list[ArtifactRef]:
        artifacts = self.storage.list_artifact_refs(owner_type, owner_id)
        if artifacts or owner_type != RecordOwnerType.agent_run:
            return artifacts
        run = self.get_agent_run(owner_id)
        legacy_names = {
            "evidence_table.md": ("research_evidence_table", "markdown"),
            "source_bundle.json": ("research_source_bundle", "json"),
            "explanation.md": ("clinical_explanation", "markdown"),
            "safety_disclosure.txt": ("clinical_safety_disclosure", "text"),
        }
        for artifact_path in run.artifact_paths:
            path = Path(artifact_path)
            if "[REDACTED" in artifact_path:
                name, kind = ("research_evidence_table", "markdown")
            else:
                name, kind = legacy_names.get(path.name, (path.stem, path.suffix.lstrip(".") or "artifact"))
            self.storage.register_artifact(
                owner_type,
                owner_id,
                name=name,
                kind=kind,
                path=str(path),
                description="Recovered artifact reference from the legacy agent run artifact path list.",
            )
        return self.storage.list_artifact_refs(owner_type, owner_id)

    def search_public_literature(self, question: str, limit: int = 5, run_id: str | None = None, trace_id: str | None = None) -> PublicResearchBundle:
        return self.platform_core.search_public_literature(question, limit=limit, run_id=run_id, trace_id=trace_id)

    @staticmethod
    def public_bundle_to_sources(bundle: PublicResearchBundle):
        return PlatformCore.public_bundle_to_sources(bundle)

    def prepare_study_for_agent_run(self, run_id: str) -> StudyRecord:
        return self.platform_core.prepare_study_for_agent_run(run_id)

    def run_benchmark_for_agent_run(self, run_id: str) -> BenchmarkRun:
        return self.platform_core.run_benchmark_for_agent_run(run_id)

    def validate_agent_run_manifest(self, run_id: str) -> dict:
        return self.platform_core.validate_agent_run_manifest(run_id)

    def summarize_agent_run_cohort(self, run_id: str) -> dict:
        return self.platform_core.summarize_agent_run_cohort(run_id)

    def analyze_agent_run_study(self, run_id: str) -> AnalysisResult:
        return self.platform_core.analyze_agent_run_study(run_id)

    def inspect_agent_run_study_quality(self, run_id: str) -> dict:
        return self.platform_core.inspect_agent_run_study_quality(run_id)

    def compile_research_brief_for_agent_run(self, run_id: str) -> ResearchBrief:
        return self.platform_core.compile_research_brief_for_agent_run(run_id)

    def generate_clinical_summary_for_agent_run(self, run_id: str) -> dict:
        return self.platform_core.generate_clinical_summary_for_agent_run(run_id)

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
        return self.platform_core.create_research_brief_from_values(
            question=question,
            benchmark_run_id=benchmark_run_id,
            campaign_id=campaign_id,
            dataset_manifest_path=dataset_manifest_path,
            source_links=source_links,
            protocol_recommendations=protocol_recommendations,
            evidence_table_path=evidence_table_path,
        )

    def create_research_brief(
        self,
        question: str,
        benchmark_run_id: str | None = None,
        campaign_id: str | None = None,
        dataset_manifest_path: str | None = None,
    ) -> ResearchBrief:
        return self.platform_core.create_research_brief(
            question=question,
            benchmark_run_id=benchmark_run_id,
            campaign_id=campaign_id,
            dataset_manifest_path=dataset_manifest_path,
        )
