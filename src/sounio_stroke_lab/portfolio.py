from __future__ import annotations

from sounio_stroke_lab.campaigns import CampaignCoordinator
from sounio_stroke_lab.programs import ProgramCoordinator
from sounio_stroke_lab.schemas import (
    CampaignStatus,
    PortfolioCampaignSummary,
    PortfolioReport,
    PortfolioProgramSummary,
    ProgramPortfolioReport,
    ProgramStatus,
    RecordOwnerType,
)
from sounio_stroke_lab.storage import StorageManager


class PortfolioCoordinator:
    CAMPAIGN_PORTFOLIO_ID = "campaign-portfolio"
    PROGRAM_PORTFOLIO_ID = "program-portfolio"

    def __init__(self, storage: StorageManager, campaigns: CampaignCoordinator, programs: ProgramCoordinator):
        self.storage = storage
        self.campaigns = campaigns
        self.programs = programs

    def get_campaign_portfolio(self) -> PortfolioReport:
        campaigns = self.campaigns.list_campaigns()
        summaries = [self._summarize_campaign(campaign) for campaign in campaigns]
        summaries.sort(key=self._sort_key, reverse=True)
        completed = sum(1 for item in summaries if item.status == CampaignStatus.completed)
        running = sum(1 for item in summaries if item.status == CampaignStatus.running)
        failed = sum(1 for item in summaries if item.status == CampaignStatus.failed)
        leading_campaign_id = summaries[0].campaign_id if summaries else None
        report = PortfolioReport(
            portfolio_id=self.CAMPAIGN_PORTFOLIO_ID,
            total_campaigns=len(summaries),
            completed_campaigns=completed,
            running_campaigns=running,
            failed_campaigns=failed,
            leading_campaign_id=leading_campaign_id,
            campaigns=summaries,
            next_actions=self._next_actions(summaries),
        )
        return self._write_portfolio_artifacts(report)

    def get_program_portfolio(self) -> ProgramPortfolioReport:
        programs = self.programs.list_programs()
        summaries = [self._summarize_program(program) for program in programs]
        summaries.sort(key=self._program_sort_key, reverse=True)
        active = sum(1 for item in summaries if item.status == ProgramStatus.active)
        completed = sum(1 for item in summaries if item.status == ProgramStatus.completed)
        blocked = sum(1 for item in summaries if item.status == ProgramStatus.blocked)
        leading_program_id = summaries[0].program_id if summaries else None
        report = ProgramPortfolioReport(
            portfolio_id=self.PROGRAM_PORTFOLIO_ID,
            total_programs=len(summaries),
            active_programs=active,
            completed_programs=completed,
            blocked_programs=blocked,
            leading_program_id=leading_program_id,
            programs=summaries,
            next_actions=self._program_next_actions(summaries),
        )
        return self._write_program_portfolio_artifacts(report)

    def _summarize_campaign(self, campaign) -> PortfolioCampaignSummary:
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
    def _summarize_program(program) -> PortfolioProgramSummary:
        summary = program.summary or {}
        campaigns = summary.get("campaigns", [])
        leader = campaigns[0] if campaigns else {}
        return PortfolioProgramSummary(
            program_id=program.program_id,
            name=program.name,
            status=program.status,
            total_campaigns=int(summary.get("total_campaigns", 0) or 0),
            completed_campaigns=int(summary.get("completed_campaigns", 0) or 0),
            running_campaigns=int(summary.get("running_campaigns", 0) or 0),
            failed_campaigns=int(summary.get("failed_campaigns", 0) or 0),
            leading_campaign_id=summary.get("leading_campaign_id"),
            leading_model=leader.get("leading_model"),
            sounio_isles_dice=float(leader.get("sounio_isles_dice", 0.0) or 0.0),
            sounio_auc=float(leader.get("sounio_auc", 0.0) or 0.0),
            sounio_aspects_mae=float(leader.get("sounio_aspects_mae", 999.0) or 999.0),
            ready_plan_count=sum(int(item.get("ready_plan_count", 0) or 0) for item in campaigns),
            watch_plan_count=sum(int(item.get("watch_plan_count", 0) or 0) for item in campaigns),
            blocked_plan_count=sum(int(item.get("blocked_plan_count", 0) or 0) for item in campaigns),
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
    def _program_sort_key(summary: PortfolioProgramSummary) -> tuple[float, float, float, int, int]:
        return (
            summary.sounio_isles_dice,
            summary.sounio_auc,
            -summary.sounio_aspects_mae,
            summary.ready_plan_count,
            summary.completed_campaigns,
        )

    @staticmethod
    def _next_actions(summaries: list[PortfolioCampaignSummary]) -> list[str]:
        if not summaries:
            return ["Create the first benchmark campaign before attempting portfolio-level comparisons."]
        actions: list[str] = []
        leader = summaries[0]
        actions.append(
            f"Use campaign `{leader.name}` as the current portfolio leader and prioritize plan `{leader.top_plan_id or 'n/a'}` for the next launch."
        )
        blocked = [item for item in summaries if item.blocked_plan_count]
        if blocked:
            actions.append(
                f"{len(blocked)} campaign(s) still have blocked plans; stabilize their parent evidence before promoting them."
            )
        watch = [item for item in summaries if item.watch_plan_count]
        if watch:
            actions.append(
                f"{len(watch)} campaign(s) are in watch status; treat them as control-sensitive until their acceptance notes clear."
            )
        if all(item.leading_model != "sounio_hypercomplex" for item in summaries if item.best_run_id):
            actions.append("No campaign is Sounio-led yet; treat the current portfolio as baseline-constrained and tune the Sounio path next.")
        return actions

    @staticmethod
    def _program_next_actions(summaries: list[PortfolioProgramSummary]) -> list[str]:
        if not summaries:
            return ["Create the first program before attempting program-level portfolio comparisons."]
        actions: list[str] = []
        leader = summaries[0]
        actions.append(
            f"Use program `{leader.name}` as the current scientific lead and prioritize its ready campaign plans first."
        )
        blocked = [item for item in summaries if item.blocked_plan_count or item.status == ProgramStatus.blocked]
        if blocked:
            actions.append(
                f"{len(blocked)} program(s) remain blocked or partially blocked; clear their campaign acceptance issues before expanding scope."
            )
        if all(item.leading_model != "sounio_hypercomplex" for item in summaries if item.leading_campaign_id):
            actions.append("No program is Sounio-led yet; the next program increment should explicitly challenge the portfolio's control winner.")
        return actions

    def _write_portfolio_artifacts(self, report: PortfolioReport) -> PortfolioReport:
        summary_path = self.storage.write_artifact_json(
            "portfolio/campaigns/portfolio_summary.json",
            report.model_dump(mode="json"),
        )
        report_path = self.storage.write_artifact_text(
            "portfolio/campaigns/portfolio_report.md",
            self._render_portfolio_report(report),
        )
        self.storage.register_artifact(
            RecordOwnerType.portfolio,
            self.CAMPAIGN_PORTFOLIO_ID,
            artifact_id=f"{self.CAMPAIGN_PORTFOLIO_ID}:portfolio_summary",
            name="portfolio_summary",
            kind="json",
            path=summary_path,
            description="Global campaign portfolio summary with current ranking and next actions.",
        )
        self.storage.register_artifact(
            RecordOwnerType.portfolio,
            self.CAMPAIGN_PORTFOLIO_ID,
            artifact_id=f"{self.CAMPAIGN_PORTFOLIO_ID}:portfolio_report",
            name="portfolio_report",
            kind="markdown",
            path=report_path,
            description="Human-readable cross-campaign portfolio report.",
        )
        return report

    def _write_program_portfolio_artifacts(self, report: ProgramPortfolioReport) -> ProgramPortfolioReport:
        summary_path = self.storage.write_artifact_json(
            "portfolio/programs/program_portfolio_summary.json",
            report.model_dump(mode="json"),
        )
        report_path = self.storage.write_artifact_text(
            "portfolio/programs/program_portfolio_report.md",
            self._render_program_portfolio_report(report),
        )
        self.storage.register_artifact(
            RecordOwnerType.portfolio,
            self.PROGRAM_PORTFOLIO_ID,
            artifact_id=f"{self.PROGRAM_PORTFOLIO_ID}:portfolio_summary",
            name="portfolio_summary",
            kind="json",
            path=summary_path,
            description="Global program portfolio summary with scientific ranking and next actions.",
        )
        self.storage.register_artifact(
            RecordOwnerType.portfolio,
            self.PROGRAM_PORTFOLIO_ID,
            artifact_id=f"{self.PROGRAM_PORTFOLIO_ID}:portfolio_report",
            name="portfolio_report",
            kind="markdown",
            path=report_path,
            description="Human-readable cross-program portfolio report.",
        )
        return report

    @staticmethod
    def _render_portfolio_report(report: PortfolioReport) -> str:
        lines = [
            "# Campaign Portfolio Report",
            "",
            f"- Portfolio ID: `{report.portfolio_id}`",
            f"- Total campaigns: `{report.total_campaigns}`",
            f"- Completed campaigns: `{report.completed_campaigns}`",
            f"- Running campaigns: `{report.running_campaigns}`",
            f"- Failed campaigns: `{report.failed_campaigns}`",
            f"- Leading campaign: `{report.leading_campaign_id or 'n/a'}`",
            "",
            "## Campaign Ranking",
            "",
        ]
        for item in report.campaigns:
            lines.extend(
                [
                    f"### {item.name}",
                    f"- Campaign ID: `{item.campaign_id}`",
                    f"- Status: `{item.status}`",
                    f"- Leading model: `{item.leading_model or 'n/a'}`",
                    f"- Sounio Dice: `{item.sounio_isles_dice}`",
                    f"- Sounio AUC: `{item.sounio_auc}`",
                    f"- Sounio ASPECTS MAE: `{item.sounio_aspects_mae}`",
                    f"- Ready plans: `{item.ready_plan_count}` / Watch: `{item.watch_plan_count}` / Blocked: `{item.blocked_plan_count}`",
                    "",
                ]
            )
        if report.next_actions:
            lines.extend(["## Next Actions", ""])
            lines.extend([f"- {item}" for item in report.next_actions])
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_program_portfolio_report(report: ProgramPortfolioReport) -> str:
        lines = [
            "# Program Portfolio Report",
            "",
            f"- Portfolio ID: `{report.portfolio_id}`",
            f"- Total programs: `{report.total_programs}`",
            f"- Active programs: `{report.active_programs}`",
            f"- Completed programs: `{report.completed_programs}`",
            f"- Blocked programs: `{report.blocked_programs}`",
            f"- Leading program: `{report.leading_program_id or 'n/a'}`",
            "",
            "## Program Ranking",
            "",
        ]
        for item in report.programs:
            lines.extend(
                [
                    f"### {item.name}",
                    f"- Program ID: `{item.program_id}`",
                    f"- Status: `{item.status}`",
                    f"- Leading campaign: `{item.leading_campaign_id or 'n/a'}`",
                    f"- Leading model: `{item.leading_model or 'n/a'}`",
                    f"- Sounio Dice: `{item.sounio_isles_dice}`",
                    f"- Sounio AUC: `{item.sounio_auc}`",
                    f"- Sounio ASPECTS MAE: `{item.sounio_aspects_mae}`",
                    f"- Ready plans: `{item.ready_plan_count}` / Watch: `{item.watch_plan_count}` / Blocked: `{item.blocked_plan_count}`",
                    "",
                ]
            )
        if report.next_actions:
            lines.extend(["## Next Actions", ""])
            lines.extend([f"- {item}" for item in report.next_actions])
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
