from __future__ import annotations

import uuid

from sounio_stroke_lab.campaigns import CampaignCoordinator
from sounio_stroke_lab.schemas import (
    CampaignRecord,
    CampaignRequest,
    CampaignStatus,
    PortfolioCampaignSummary,
    ProgramCampaignAttachRequest,
    ProgramRecord,
    ProgramRequest,
    ProgramStatus,
    RecordOwnerType,
    utc_now,
)
from sounio_stroke_lab.storage import StorageManager


class ProgramCoordinator:
    def __init__(self, storage: StorageManager, campaigns: CampaignCoordinator):
        self.storage = storage
        self.campaigns = campaigns

    def create_program(self, request: ProgramRequest) -> ProgramRecord:
        program = ProgramRecord(
            program_id=uuid.uuid4().hex,
            name=request.name,
            objective=request.objective,
            hypothesis=request.hypothesis,
            notes=request.notes,
            campaign_ids=list(dict.fromkeys(request.campaign_ids)),
        )
        program = self._reconcile_program(program)
        self.storage.save_program(program)
        return self._write_program_artifacts(program)

    def get_program(self, program_id: str) -> ProgramRecord:
        program = self.storage.get_program(program_id)
        refreshed = self._reconcile_program(program)
        if refreshed != program:
            self.storage.save_program(refreshed)
        return self._write_program_artifacts(refreshed)

    def list_programs(self) -> list[ProgramRecord]:
        return [self.get_program(program.program_id) for program in self.storage.list_programs()]

    def attach_campaign(self, program_id: str, request: ProgramCampaignAttachRequest) -> ProgramRecord:
        program = self.storage.get_program(program_id)
        campaign = self.storage.get_campaign(request.campaign_id)
        if campaign.program_id != program_id:
            self.storage.save_campaign(campaign.model_copy(update={"program_id": program_id}))
        if request.campaign_id not in program.campaign_ids:
            program = program.model_copy(update={"campaign_ids": [*program.campaign_ids, request.campaign_id]})
            self.storage.save_program(program)
        return self.get_program(program_id)

    def create_campaign(self, program_id: str, request: CampaignRequest) -> CampaignRecord:
        self.storage.get_program(program_id)
        campaign = self.campaigns.create_campaign(request.model_copy(update={"program_id": program_id}))
        program = self.storage.get_program(program_id)
        if campaign.campaign_id not in program.campaign_ids:
            self.storage.save_program(
                program.model_copy(update={"campaign_ids": [*program.campaign_ids, campaign.campaign_id]})
            )
        return self.campaigns.get_campaign(campaign.campaign_id)

    def _reconcile_program(self, program: ProgramRecord) -> ProgramRecord:
        campaigns = self._resolve_program_campaigns(program)
        summaries = [self._summarize_campaign(campaign) for campaign in campaigns]
        summaries.sort(key=self._sort_key, reverse=True)
        status = self._derive_status(campaigns)
        summary = {
            "total_campaigns": len(summaries),
            "completed_campaigns": sum(1 for item in campaigns if item.status == CampaignStatus.completed),
            "running_campaigns": sum(1 for item in campaigns if item.status == CampaignStatus.running),
            "failed_campaigns": sum(1 for item in campaigns if item.status == CampaignStatus.failed),
            "leading_campaign_id": summaries[0].campaign_id if summaries else None,
            "campaigns": [item.model_dump(mode="json") for item in summaries],
            "next_actions": self._next_actions(program, summaries),
        }
        resolved_ids = [item.campaign_id for item in campaigns]
        changed = (
            program.status != status
            or program.campaign_ids != resolved_ids
            or program.summary != summary
        )
        return program.model_copy(
            update={
                "status": status,
                "campaign_ids": resolved_ids,
                "summary": summary,
                "updated_at": utc_now() if changed else program.updated_at,
            }
        )

    def _resolve_program_campaigns(self, program: ProgramRecord) -> list[CampaignRecord]:
        linked: dict[str, CampaignRecord] = {}
        for campaign_id in program.campaign_ids:
            try:
                linked[campaign_id] = self.campaigns.get_campaign(campaign_id)
            except KeyError:
                continue
        for campaign in self.campaigns.list_campaigns():
            if campaign.program_id == program.program_id:
                linked[campaign.campaign_id] = campaign
        return list(linked.values())

    @staticmethod
    def _summarize_campaign(campaign: CampaignRecord) -> PortfolioCampaignSummary:
        summary = campaign.summary or {}
        best_run = summary.get("best_run") or {}
        plan_reports = summary.get("experiment_plan_reports", [])
        ready_count = sum(1 for item in plan_reports if item.get("acceptance_status") == "ready")
        watch_count = sum(1 for item in plan_reports if item.get("acceptance_status") == "watch")
        blocked_count = sum(1 for item in plan_reports if item.get("acceptance_status") == "blocked")
        top_plan_id = next((item.get("plan_id") for item in plan_reports if item.get("launch_ready")), None)
        return PortfolioCampaignSummary(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            status=campaign.status,
            total_runs=int(summary.get("total_runs", 0) or 0),
            completed_runs=int(summary.get("completed_runs", 0) or 0),
            failed_runs=int(summary.get("failed_runs", 0) or 0),
            best_run_id=best_run.get("benchmark_run_id"),
            leading_model=best_run.get("leading_model"),
            sounio_isles_dice=float(best_run.get("sounio_isles_dice", 0.0) or 0.0),
            sounio_auc=float(best_run.get("sounio_auc", 0.0) or 0.0),
            sounio_aspects_mae=float(best_run.get("sounio_aspects_mae", 999.0) or 999.0),
            ready_plan_count=ready_count,
            watch_plan_count=watch_count,
            blocked_plan_count=blocked_count,
            top_plan_id=top_plan_id,
        )

    @staticmethod
    def _sort_key(summary: PortfolioCampaignSummary) -> tuple[float, float, float, int]:
        return (
            summary.sounio_isles_dice,
            summary.sounio_auc,
            -summary.sounio_aspects_mae,
            summary.ready_plan_count,
        )

    @staticmethod
    def _derive_status(campaigns: list[CampaignRecord]) -> ProgramStatus:
        if not campaigns:
            return ProgramStatus.created
        statuses = {campaign.status for campaign in campaigns}
        if statuses <= {CampaignStatus.completed}:
            return ProgramStatus.completed
        if CampaignStatus.running in statuses or CampaignStatus.created in statuses:
            return ProgramStatus.active
        if CampaignStatus.failed in statuses or CampaignStatus.cancelled in statuses:
            return ProgramStatus.blocked
        return ProgramStatus.active

    @staticmethod
    def _next_actions(program: ProgramRecord, summaries: list[PortfolioCampaignSummary]) -> list[str]:
        if not summaries:
            return [f"Program `{program.name}` does not have campaigns yet; create the first campaign to establish a baseline."]
        actions = []
        leader = summaries[0]
        actions.append(
            f"Use campaign `{leader.name}` as the current leader for program `{program.name}` and prioritize plan `{leader.top_plan_id or 'n/a'}`."
        )
        blocked = [item for item in summaries if item.blocked_plan_count]
        if blocked:
            actions.append(
                f"{len(blocked)} campaign(s) in this program still have blocked plans; clear those acceptance blockers before widening scope."
            )
        if all(item.leading_model != "sounio_hypercomplex" for item in summaries if item.best_run_id):
            actions.append("This program is still baseline-led; the next campaign should explicitly challenge the current control winner.")
        return actions

    def _write_program_artifacts(self, program: ProgramRecord) -> ProgramRecord:
        program_id = program.program_id
        summary_path = self.storage.write_artifact_json(
            f"programs/{program_id}/program_summary.json",
            program.summary,
        )
        report_path = self.storage.write_artifact_text(
            f"programs/{program_id}/program_report.md",
            self._render_program_report(program),
        )
        self.storage.register_artifact(
            RecordOwnerType.program,
            program_id,
            artifact_id=f"{program_id}:program_summary",
            name="program_summary",
            kind="json",
            path=summary_path,
            description="Aggregated program summary across linked campaigns.",
        )
        self.storage.register_artifact(
            RecordOwnerType.program,
            program_id,
            artifact_id=f"{program_id}:program_report",
            name="program_report",
            kind="markdown",
            path=report_path,
            description="Human-readable program report with campaign ranking and next actions.",
        )
        return program

    @staticmethod
    def _render_program_report(program: ProgramRecord) -> str:
        lines = [
            f"# Program Report: {program.name}",
            "",
            f"- Program ID: `{program.program_id}`",
            f"- Status: `{program.status}`",
            f"- Objective: {program.objective or 'n/a'}",
            f"- Hypothesis: {program.hypothesis or 'n/a'}",
            "",
            "## Campaigns",
            "",
        ]
        for item in program.summary.get("campaigns", []):
            lines.extend(
                [
                    f"### {item.get('name', 'campaign')}",
                    f"- Campaign ID: `{item.get('campaign_id', 'n/a')}`",
                    f"- Status: `{item.get('status', 'n/a')}`",
                    f"- Leading model: `{item.get('leading_model', 'n/a')}`",
                    f"- Sounio Dice: `{item.get('sounio_isles_dice', 'n/a')}`",
                    f"- Sounio AUC: `{item.get('sounio_auc', 'n/a')}`",
                    f"- Sounio ASPECTS MAE: `{item.get('sounio_aspects_mae', 'n/a')}`",
                    "",
                ]
            )
        actions = program.summary.get("next_actions", [])
        if actions:
            lines.extend(["## Next Actions", ""])
            lines.extend([f"- {item}" for item in actions])
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
