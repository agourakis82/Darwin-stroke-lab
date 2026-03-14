from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse

from sounio_stroke_lab.config import APP_NAME
from sounio_stroke_lab.job_backend import UnsupportedJobBackend
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
    MCPServerDescriptor,
    MCPServerHealth,
    PortfolioReport,
    ProgramPortfolioReport,
    ProgramCampaignAttachRequest,
    ProgramRecord,
    ProgramRequest,
    RecordOwnerType,
    ResearchBrief,
    ResearchBriefRequest,
    StudyRecord,
    TraceEvent,
)
from sounio_stroke_lab.service import StrokeResearchService


def create_app(storage_root: Path | None = None, *, start_benchmark_worker: bool | None = False) -> FastAPI:
    app = FastAPI(title=APP_NAME, version="0.1.0")
    service = StrokeResearchService(storage_root=storage_root, start_benchmark_worker=start_benchmark_worker)
    app.state.service = service
    app.add_event_handler("shutdown", service.shutdown)

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):
        auth_settings = service.runtime.auth
        if not auth_settings.enabled or request.url.path in auth_settings.exempt_paths:
            return await call_next(request)
        header = request.headers.get("Authorization", "").strip()
        if not header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing bearer token."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = header.removeprefix("Bearer ").strip()
        if token not in auth_settings.tokens:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid bearer token."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/studies", response_model=StudyRecord, status_code=201)
    async def create_study(files: list[UploadFile] = File(...)) -> StudyRecord:
        try:
            return await service.create_study_from_uploads(files)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/studies/{study_id}/analyze", response_model=AnalysisResult)
    def analyze_study(study_id: str, request: AnalyzeStudyRequest) -> AnalysisResult:
        try:
            return service.analyze_study(study_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/studies/{study_id}/result", response_model=AnalysisResult)
    def get_study_result(study_id: str) -> AnalysisResult:
        try:
            return service.get_analysis_result(study_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/benchmark/runs", response_model=BenchmarkRun, status_code=201)
    def create_benchmark_run(request: BenchmarkRequest) -> BenchmarkRun:
        try:
            return service.run_benchmark(request)
        except UnsupportedJobBackend as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/benchmark/jobs", response_model=JobRecord, status_code=202)
    def submit_benchmark_job(request: BenchmarkRequest) -> JobRecord:
        try:
            return service.submit_benchmark(request)
        except UnsupportedJobBackend as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/benchmark/runs/{run_id}", response_model=BenchmarkRun)
    def get_benchmark_run(run_id: str) -> BenchmarkRun:
        try:
            return service.get_benchmark_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/campaigns", response_model=CampaignRecord, status_code=202)
    def create_campaign(request: CampaignRequest) -> CampaignRecord:
        return service.create_campaign(request)

    @app.get("/campaigns", response_model=list[CampaignRecord])
    def list_campaigns() -> list[CampaignRecord]:
        return service.list_campaigns()

    @app.post("/programs", response_model=ProgramRecord, status_code=201)
    def create_program(request: ProgramRequest) -> ProgramRecord:
        return service.create_program(request)

    @app.get("/programs", response_model=list[ProgramRecord])
    def list_programs() -> list[ProgramRecord]:
        return service.list_programs()

    @app.get("/programs/{program_id}", response_model=ProgramRecord)
    def get_program(program_id: str) -> ProgramRecord:
        try:
            return service.get_program(program_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/programs/{program_id}/campaigns", response_model=CampaignRecord, status_code=202)
    def create_program_campaign(program_id: str, request: CampaignRequest) -> CampaignRecord:
        try:
            return service.create_program_campaign(program_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/programs/{program_id}/campaigns/attach", response_model=ProgramRecord)
    def attach_program_campaign(program_id: str, request: ProgramCampaignAttachRequest) -> ProgramRecord:
        try:
            return service.attach_program_campaign(program_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/portfolio/campaigns", response_model=PortfolioReport)
    def get_campaign_portfolio() -> PortfolioReport:
        return service.get_campaign_portfolio()

    @app.get("/portfolio/programs", response_model=ProgramPortfolioReport)
    def get_program_portfolio() -> ProgramPortfolioReport:
        return service.get_program_portfolio()

    @app.get("/campaigns/{campaign_id}", response_model=CampaignRecord)
    def get_campaign(campaign_id: str) -> CampaignRecord:
        try:
            return service.get_campaign(campaign_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/campaigns/{campaign_id}/followup", response_model=list[CampaignFollowUpProposal])
    def get_campaign_follow_up_proposals(campaign_id: str) -> list[CampaignFollowUpProposal]:
        try:
            return service.get_campaign_follow_up_proposals(campaign_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/campaigns/{campaign_id}/followup", response_model=CampaignRecord, status_code=202)
    def launch_campaign_follow_up(
        campaign_id: str,
        request: CampaignFollowUpLaunchRequest | None = None,
    ) -> CampaignRecord:
        try:
            return service.launch_campaign_follow_up(campaign_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/campaigns/{campaign_id}/plans", response_model=list[CampaignExperimentPlan])
    def get_campaign_experiment_plans(campaign_id: str) -> list[CampaignExperimentPlan]:
        try:
            return service.get_campaign_experiment_plans(campaign_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/campaigns/{campaign_id}/plan-reports", response_model=list[CampaignExperimentPlanReport])
    def get_campaign_experiment_plan_reports(campaign_id: str) -> list[CampaignExperimentPlanReport]:
        try:
            return service.get_campaign_experiment_plan_reports(campaign_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/campaigns/{campaign_id}/plans/{plan_id}/campaign", response_model=CampaignRecord, status_code=202)
    def launch_campaign_experiment_plan(
        campaign_id: str,
        plan_id: str,
        request: CampaignExperimentPlanLaunchRequest | None = None,
    ) -> CampaignRecord:
        try:
            return service.launch_campaign_experiment_plan(campaign_id, plan_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/campaigns/{campaign_id}/plans/{plan_id}/agent-run", response_model=AgentRun, status_code=202)
    def launch_campaign_experiment_plan_agent_run(
        campaign_id: str,
        plan_id: str,
        request: CampaignExperimentPlanAgentLaunchRequest | None = None,
    ) -> AgentRun:
        try:
            return service.launch_campaign_experiment_plan_agent_run(campaign_id, plan_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/campaigns/{campaign_id}/brief", response_model=ResearchBrief, status_code=201)
    def create_campaign_brief(campaign_id: str, request: ResearchBriefRequest | None = None) -> ResearchBrief:
        try:
            question = request.question if request else None
            return service.create_campaign_brief(campaign_id, question=question)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/jobs/{job_id}", response_model=JobRecord)
    def get_job(job_id: str) -> JobRecord:
        try:
            return service.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/jobs/{job_id}/manifest")
    def get_job_manifest(job_id: str) -> dict:
        try:
            return service.render_job_manifest(job_id)
        except UnsupportedJobBackend as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/jobs/{job_id}/cancel", response_model=JobRecord)
    def cancel_job(job_id: str) -> JobRecord:
        try:
            return service.cancel_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/artifacts/{owner_type}/{owner_id}", response_model=list[ArtifactRef])
    def list_artifacts(owner_type: RecordOwnerType, owner_id: str) -> list[ArtifactRef]:
        return service.list_artifacts(owner_type, owner_id)

    @app.post("/agent/runs", response_model=AgentRun, status_code=202)
    def create_agent_run(request: AgentRunRequest) -> AgentRun:
        return service.create_agent_run(request)

    @app.get("/agent/runs/{run_id}", response_model=AgentRun)
    def get_agent_run(run_id: str) -> AgentRun:
        try:
            return service.get_agent_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/agent/runs/{run_id}/cancel", response_model=AgentRun)
    def cancel_agent_run(run_id: str) -> AgentRun:
        try:
            return service.cancel_agent_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/agent/runs/{run_id}/events")
    def get_agent_run_events(run_id: str) -> StreamingResponse:
        def generate():
            sent_ids: set[str] = set()
            while True:
                try:
                    run = service.get_agent_run(run_id)
                except KeyError as exc:
                    raise HTTPException(status_code=404, detail=str(exc)) from exc
                for step in service.list_agent_steps(run_id):
                    if step.step_id in sent_ids:
                        continue
                    sent_ids.add(step.step_id)
                    yield f"event: step\ndata: {json.dumps(step.model_dump(mode='json'))}\n\n"
                yield f"event: status\ndata: {json.dumps({'run_id': run.run_id, 'status': run.status})}\n\n"
                if run.status in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.1)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/agent/runs/{run_id}/trace", response_model=list[TraceEvent])
    def get_agent_run_trace(run_id: str) -> list[TraceEvent]:
        try:
            service.get_agent_run(run_id)
            return service.list_trace_events(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/mcp/servers", response_model=list[MCPServerDescriptor])
    def list_mcp_servers() -> list[MCPServerDescriptor]:
        return service.list_mcp_servers()

    @app.get("/mcp/servers/{name}/health", response_model=MCPServerHealth)
    def get_mcp_server_health(name: str) -> MCPServerHealth:
        try:
            return service.get_mcp_server_health(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/research/briefs", response_model=ResearchBrief, status_code=201)
    def create_research_brief(request: ResearchBriefRequest) -> ResearchBrief:
        return service.create_research_brief(
            question=request.question,
            benchmark_run_id=request.benchmark_run_id,
            campaign_id=request.campaign_id,
            dataset_manifest_path=request.dataset_manifest_path,
        )

    return app


app = create_app(
    start_benchmark_worker=os.getenv("SOUNIO_STROKE_START_EMBEDDED_BENCHMARK_WORKER", "").strip().lower() in {"1", "true", "yes", "on"}
)
