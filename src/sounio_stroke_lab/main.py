from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

from sounio_stroke_lab.config import APP_NAME
from sounio_stroke_lab.schemas import (
    AnalysisResult,
    AnalyzeStudyRequest,
    BenchmarkRequest,
    BenchmarkRun,
    ResumeSummary,
    RunEvent,
    RunRecord,
    RunSubmitRequest,
    StudyRecord,
)
from sounio_stroke_lab.service import StrokeResearchService


def create_app(storage_root: Path | None = None) -> FastAPI:
    app = FastAPI(title=APP_NAME, version="0.1.0")
    service = StrokeResearchService(storage_root=storage_root)
    app.state.service = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
        return service.run_benchmark(request)

    @app.get("/benchmark/runs/{run_id}", response_model=BenchmarkRun)
    def get_benchmark_run(run_id: str) -> BenchmarkRun:
        try:
            return service.get_benchmark_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/runs", response_model=RunRecord, status_code=201)
    def create_run(request: RunSubmitRequest) -> RunRecord:
        try:
            return service.submit_run(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/runs/{run_id}", response_model=RunRecord)
    def get_run(run_id: str) -> RunRecord:
        try:
            return service.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/runs/{run_id}/resume-summary", response_model=ResumeSummary)
    def get_resume_summary(run_id: str) -> ResumeSummary:
        try:
            return service.get_resume_summary(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/runs/{run_id}/events", response_model=list[RunEvent])
    def get_run_events(run_id: str) -> list[RunEvent]:
        try:
            return service.get_run_events(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


app = create_app()
