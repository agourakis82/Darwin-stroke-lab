from __future__ import annotations

import uuid

from sounio_stroke_lab.job_backend import BaseJobBackend
from sounio_stroke_lab.schemas import (
    BenchmarkRequest,
    CampaignEntry,
    CampaignBenchmarkSpec,
    CampaignExperimentPlan,
    CampaignExperimentPlanReport,
    CampaignExperimentPlanLaunchRequest,
    CampaignFollowUpLaunchRequest,
    CampaignFollowUpProposal,
    CampaignRecord,
    CampaignRequest,
    CampaignStatus,
    ExperimentSuccessCriterion,
    JobStatus,
    ModelFamily,
    RecordOwnerType,
    utc_now,
)
from sounio_stroke_lab.storage import StorageManager


class CampaignCoordinator:
    _UNSET = object()

    def __init__(self, storage: StorageManager, job_backend: BaseJobBackend):
        self.storage = storage
        self.job_backend = job_backend

    def create_campaign(self, request: CampaignRequest) -> CampaignRecord:
        entries: list[CampaignEntry] = []
        for index, spec in enumerate(request.benchmark_specs, start=1):
            job = self.job_backend.submit_benchmark(spec.request)
            entries.append(
                CampaignEntry(
                    entry_id=uuid.uuid4().hex,
                    label=spec.label or f"run-{index:02d}-seed-{spec.request.seed}",
                    request=spec.request,
                    job_id=job.job_id,
                    status=job.status,
                )
            )

        campaign = CampaignRecord(
            campaign_id=uuid.uuid4().hex,
            name=request.name,
            objective=request.objective,
            status=CampaignStatus.running if entries else CampaignStatus.created,
            program_id=request.program_id,
            benchmark_specs=entries,
            notes=request.notes,
        )
        campaign = self._reconcile_campaign(campaign)
        self.storage.save_campaign(campaign)
        return self._write_campaign_artifacts(campaign)

    def get_campaign(self, campaign_id: str) -> CampaignRecord:
        campaign = self.storage.get_campaign(campaign_id)
        refreshed = self._reconcile_campaign(campaign)
        if refreshed != campaign:
            self.storage.save_campaign(refreshed)
        return self._write_campaign_artifacts(refreshed)

    def list_campaigns(self) -> list[CampaignRecord]:
        return [self.get_campaign(campaign.campaign_id) for campaign in self.storage.list_campaigns()]

    def get_follow_up_proposals(self, campaign_id: str) -> list[CampaignFollowUpProposal]:
        campaign = self.get_campaign(campaign_id)
        return self._deserialize_follow_up_proposals(campaign.summary.get("follow_up_proposals", []))

    def launch_follow_up_campaign(
        self,
        campaign_id: str,
        request: CampaignFollowUpLaunchRequest | None = None,
    ) -> CampaignRecord:
        request = request or CampaignFollowUpLaunchRequest()
        campaign = self.get_campaign(campaign_id)
        proposals = self.get_follow_up_proposals(campaign_id)
        if not proposals:
            raise ValueError(f"Campaign {campaign_id} does not have any follow-up proposals to launch.")
        selected = proposals[0]
        if request.proposal_id:
            matching = [proposal for proposal in proposals if proposal.proposal_id == request.proposal_id]
            if not matching:
                raise ValueError(f"Unknown follow-up proposal for campaign {campaign_id}: {request.proposal_id}")
            selected = matching[0]

        follow_up_request = CampaignRequest(
            name=request.name or f"{campaign.name} / {selected.title}",
            objective=selected.rationale,
            benchmark_specs=selected.benchmark_specs,
            program_id=campaign.program_id,
            notes="\n".join(
                part
                for part in (
                    f"Follow-up to campaign {campaign.campaign_id}.",
                    f"Selected proposal: {selected.proposal_id}.",
                    request.notes.strip(),
                )
                if part
            ),
        )
        return self.create_campaign(follow_up_request)

    def get_experiment_plans(self, campaign_id: str) -> list[CampaignExperimentPlan]:
        campaign = self.get_campaign(campaign_id)
        return self._deserialize_experiment_plans(campaign.summary.get("experiment_plans", []))

    def get_experiment_plan(self, campaign_id: str, plan_id: str) -> CampaignExperimentPlan:
        plans = self.get_experiment_plans(campaign_id)
        matches = [plan for plan in plans if plan.plan_id == plan_id]
        if not matches:
            raise ValueError(f"Unknown experiment plan for campaign {campaign_id}: {plan_id}")
        return matches[0]

    def get_experiment_plan_reports(self, campaign_id: str) -> list[CampaignExperimentPlanReport]:
        campaign = self.get_campaign(campaign_id)
        return self._deserialize_experiment_plan_reports(campaign.summary.get("experiment_plan_reports", []))

    def launch_experiment_plan(
        self,
        campaign_id: str,
        plan_id: str,
        request: CampaignExperimentPlanLaunchRequest | None = None,
    ) -> CampaignRecord:
        request = request or CampaignExperimentPlanLaunchRequest()
        campaign = self.get_campaign(campaign_id)
        plan = self.get_experiment_plan(campaign_id, plan_id)
        follow_up_request = CampaignRequest(
            name=request.name or f"{campaign.name} / {plan.title}",
            objective=plan.hypothesis,
            benchmark_specs=plan.benchmark_specs,
            program_id=campaign.program_id,
            notes="\n".join(
                part
                for part in (
                    f"Experiment plan follow-up to campaign {campaign.campaign_id}.",
                    f"Selected plan: {plan.plan_id}.",
                    request.notes.strip(),
                )
                if part
            ),
        )
        return self.create_campaign(follow_up_request)

    def _reconcile_campaign(self, campaign: CampaignRecord) -> CampaignRecord:
        entries: list[CampaignEntry] = []
        completed_run_ids: list[str] = []
        counts = {status.value: 0 for status in JobStatus}

        def _status_value(value) -> str:
            return value.value if hasattr(value, "value") else str(value)

        for entry in campaign.benchmark_specs:
            if not entry.job_id:
                entries.append(entry)
                continue
            try:
                job = self.job_backend.get_job(entry.job_id)
            except KeyError:
                updated_entry = entry.model_copy(
                    update={
                        "status": JobStatus.failed,
                        "error": f"Missing job record: {entry.job_id}",
                    }
                )
            else:
                benchmark_run_id = job.result_payload.get("benchmark_run_id")
                if benchmark_run_id:
                    completed_run_ids.append(str(benchmark_run_id))
                updated_entry = entry.model_copy(
                    update={
                        "status": job.status,
                        "benchmark_run_id": str(benchmark_run_id) if benchmark_run_id else None,
                        "error": job.error,
                    }
                )
            entry_status = _status_value(updated_entry.status)
            counts[entry_status] = counts.get(entry_status, 0) + 1
            entries.append(updated_entry)

        terminal_statuses = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}
        statuses = {_status_value(entry.status) for entry in entries}
        terminal_values = {_status_value(status) for status in terminal_statuses}
        if not entries:
            campaign_status = CampaignStatus.created
        elif statuses <= {JobStatus.completed.value}:
            campaign_status = CampaignStatus.completed
        elif statuses <= {JobStatus.cancelled.value}:
            campaign_status = CampaignStatus.cancelled
        elif statuses.issubset(terminal_values) and JobStatus.failed.value in statuses:
            campaign_status = CampaignStatus.failed
        else:
            campaign_status = CampaignStatus.running

        summary = {
            "total_runs": len(entries),
            "queued_runs": counts.get(JobStatus.queued.value, 0),
            "running_runs": counts.get(JobStatus.running.value, 0),
            "completed_runs": counts.get(JobStatus.completed.value, 0),
            "failed_runs": counts.get(JobStatus.failed.value, 0),
            "cancelled_runs": counts.get(JobStatus.cancelled.value, 0),
            "completed_benchmark_run_ids": completed_run_ids,
        }
        summary.update(self._benchmark_summary(entries))
        summary["next_experiment_recommendations"] = self._recommend_next_experiments(summary)
        follow_up_proposals = self._build_follow_up_proposals(campaign, entries, summary)
        summary["follow_up_proposals"] = [proposal.model_dump(mode="json") for proposal in follow_up_proposals]
        experiment_plans = self._build_experiment_plans(campaign, summary, follow_up_proposals)
        summary["experiment_plans"] = [plan.model_dump(mode="json") for plan in experiment_plans]
        summary["experiment_plan_reports"] = [
            report.model_dump(mode="json")
            for report in self._build_experiment_plan_reports(summary, experiment_plans)
        ]
        changed = (
            campaign.status != campaign_status
            or campaign.benchmark_specs != entries
            or campaign.summary != summary
        )
        return campaign.model_copy(
            update={
                "updated_at": utc_now() if changed else campaign.updated_at,
                "status": campaign_status,
                "benchmark_specs": entries,
                "summary": summary,
            }
        )

    def _benchmark_summary(self, entries: list[CampaignEntry]) -> dict:
        run_summaries: list[dict] = []
        best_entry: dict | None = None
        best_sort_key: tuple[float, float, float] | None = None
        for entry in entries:
            if not entry.benchmark_run_id:
                continue
            try:
                run = self.storage.get_benchmark_run(entry.benchmark_run_id)
            except KeyError:
                continue
            sounio_metrics = run.metrics.get(ModelFamily.sounio_hypercomplex.value, {})
            sounio_dice = float(sounio_metrics.get("isles_dice", {}).get("value", 0.0) or 0.0)
            sounio_auc = float(sounio_metrics.get("auc", {}).get("value", 0.0) or 0.0)
            sounio_aspects_mae = float(sounio_metrics.get("aspects_mae", {}).get("value", 999.0) or 999.0)
            overall_rank = run.leaderboard.get("overall_rank", [])
            leading_model = overall_rank[0]["model_name"] if overall_rank else None
            run_summary = {
                "label": entry.label,
                "benchmark_run_id": run.run_id,
                "dataset_version": run.dataset_version,
                "leading_model": leading_model,
                "sounio_isles_dice": round(sounio_dice, 4),
                "sounio_auc": round(sounio_auc, 4),
                "sounio_aspects_mae": round(sounio_aspects_mae, 4),
            }
            run_summaries.append(run_summary)
            sort_key = (sounio_dice, sounio_auc, -sounio_aspects_mae)
            if best_sort_key is None or sort_key > best_sort_key:
                best_sort_key = sort_key
                best_entry = run_summary
        return {
            "run_summaries": run_summaries,
            "best_run": best_entry,
        }

    def _recommend_next_experiments(self, summary: dict) -> list[str]:
        recommendations: list[str] = []
        failed_runs = int(summary.get("failed_runs", 0) or 0)
        if failed_runs:
            recommendations.append(
                "Inspect failed benchmark jobs first and stabilize the manifest or backend before widening the sweep."
            )

        run_summaries = summary.get("run_summaries", [])
        best_run = summary.get("best_run") or {}
        if best_run:
            leading_model = best_run.get("leading_model")
            if leading_model and leading_model != ModelFamily.sounio_hypercomplex.value:
                recommendations.append(
                    f"Use `{leading_model}` as the control run and tune the Sounio path against the current campaign winner."
                )
            else:
                recommendations.append(
                    "Promote the best Sounio run to the next small-realistic cohort and hold the same baseline set fixed."
                )
            aspects_mae = float(best_run.get("sounio_aspects_mae", 999.0) or 999.0)
            if aspects_mae > 0.75:
                recommendations.append(
                    "Prioritize ASPECTS calibration and region threshold tuning before scaling to larger cohorts."
                )
            auc = float(best_run.get("sounio_auc", 0.0) or 0.0)
            if auc < 0.75:
                recommendations.append(
                    "Add a harder lesion-sensitivity sweep or a trained artifact comparison because Sounio AUC is still modest."
                )

        if len(run_summaries) >= 2:
            dice_values = [float(item.get("sounio_isles_dice", 0.0) or 0.0) for item in run_summaries]
            if max(dice_values) - min(dice_values) < 0.03:
                recommendations.append(
                    "The campaign spread is narrow; vary cohort composition or ablation settings instead of repeating nearby seeds."
                )

        if not recommendations:
            recommendations.append("Expand this campaign with one stronger cohort variant and one ablation-focused control run.")
        return recommendations

    def _build_follow_up_proposals(
        self,
        campaign: CampaignRecord,
        entries: list[CampaignEntry],
        summary: dict,
    ) -> list[CampaignFollowUpProposal]:
        proposals: list[CampaignFollowUpProposal] = []
        best_run = summary.get("best_run") or {}
        source_entry = self._find_source_entry(entries, best_run)
        if source_entry is None:
            return proposals

        base_request = source_entry.request
        seen_manifests = {entry.request.dataset_manifest_path for entry in entries}
        reproducibility_specs: list[CampaignBenchmarkSpec] = []
        for offset in range(3):
            seed = base_request.seed + offset
            reproducibility_specs.append(
                CampaignBenchmarkSpec(
                    label=f"{source_entry.label}-replica-seed-{seed}",
                    request=self._clone_request(base_request, seed=seed),
                )
            )
        proposals.append(
            CampaignFollowUpProposal(
                proposal_id="winner-reproducibility-sweep",
                title="Winner Reproducibility Sweep",
                rationale=(
                    "Confirm that the current best run stays on top across nearby seeds "
                    "before widening the cohort or changing the protocol."
                ),
                benchmark_specs=reproducibility_specs,
            )
        )

        alternate_manifests = sorted(path for path in seen_manifests if path != base_request.dataset_manifest_path)
        if alternate_manifests:
            transfer_specs: list[CampaignBenchmarkSpec] = []
            for manifest_path in alternate_manifests:
                manifest_label = self._short_manifest_label(manifest_path)
                transfer_specs.append(
                    CampaignBenchmarkSpec(
                        label=f"{source_entry.label}-transfer-{manifest_label}",
                        request=self._clone_request(
                            base_request,
                            external_test_manifest_path=manifest_path,
                        ),
                    )
                )
            proposals.append(
                CampaignFollowUpProposal(
                    proposal_id="cross-manifest-transfer-check",
                    title="Cross-Manifest Transfer Check",
                    rationale=(
                        "Hold the winning training setup fixed and test it against the alternate "
                        "manifest(s) already present in this campaign to see whether the gain is cohort-specific."
                    ),
                    benchmark_specs=transfer_specs,
                )
            )
        return proposals

    def _build_experiment_plans(
        self,
        campaign: CampaignRecord,
        summary: dict,
        proposals: list[CampaignFollowUpProposal],
    ) -> list[CampaignExperimentPlan]:
        best_run = summary.get("best_run") or {}
        best_dice = float(best_run.get("sounio_isles_dice", 0.0) or 0.0)
        best_auc = float(best_run.get("sounio_auc", 0.0) or 0.0)
        best_mae = float(best_run.get("sounio_aspects_mae", 999.0) or 999.0)
        leading_model = best_run.get("leading_model") or ModelFamily.sounio_hypercomplex.value
        required_baselines = [
            ModelFamily.python_3d_conventional,
            ModelFamily.julia_equivalent,
            ModelFamily.cpp_equivalent,
        ]
        plans: list[CampaignExperimentPlan] = []
        for proposal in proposals:
            primary_spec = proposal.benchmark_specs[0] if proposal.benchmark_specs else None
            if primary_spec is None:
                continue
            target_cohort, hypothesis = self._plan_context_for_proposal(proposal, leading_model)
            success_criteria = [
                ExperimentSuccessCriterion(
                    metric="sounio_isles_dice",
                    comparator=">=",
                    target=round(max(best_dice - 0.03, 0.45), 4),
                    rationale="Keep lesion overlap close to the strongest observed campaign run.",
                ),
                ExperimentSuccessCriterion(
                    metric="sounio_auc",
                    comparator=">=",
                    target=round(max(best_auc - 0.02, 0.7), 4),
                    rationale="Preserve lesion ranking quality while changing only one campaign variable.",
                ),
                ExperimentSuccessCriterion(
                    metric="sounio_aspects_mae",
                    comparator="<=",
                    target=round(min(best_mae + 0.15, 1.0), 4),
                    rationale="Do not let ASPECTS calibration drift while expanding the sweep.",
                ),
                ExperimentSuccessCriterion(
                    metric="leaderboard_rank",
                    comparator="<=",
                    target=2.0,
                    rationale="Sounio should remain competitive against the required control baselines.",
                ),
            ]
            recommended_agent_request = {
                "objective": (
                    f"Execute experiment plan '{proposal.title}' and determine whether Sounio meets the "
                    "success criteria against the required baselines."
                ),
                "surface": "research",
                "dataset_manifest_path": primary_spec.request.dataset_manifest_path,
                "external_test_manifest_path": primary_spec.request.external_test_manifest_path,
                "train_split": primary_spec.request.train_split,
                "test_split": primary_spec.request.test_split,
                "seed": primary_spec.request.seed,
                "include_baseline_comparison": True,
                "model_family": ModelFamily.sounio_hypercomplex.value,
                "research_question": (
                    f"Evaluate the plan '{proposal.title}' for campaign {campaign.campaign_id}. "
                    f"Hypothesis: {hypothesis}"
                ),
                "notes": (
                    f"campaign_id={campaign.campaign_id};proposal_id={proposal.proposal_id};"
                    f"plan_id=plan-{proposal.proposal_id}"
                ),
            }
            plans.append(
                CampaignExperimentPlan(
                    plan_id=f"plan-{proposal.proposal_id}",
                    title=proposal.title,
                    hypothesis=hypothesis,
                    target_cohort=target_cohort,
                    rationale=proposal.rationale,
                    required_baselines=required_baselines,
                    success_criteria=success_criteria,
                    benchmark_specs=proposal.benchmark_specs,
                    recommended_agent_request=recommended_agent_request,
                )
            )
        return plans

    def _build_experiment_plan_reports(
        self,
        summary: dict,
        plans: list[CampaignExperimentPlan],
    ) -> list[CampaignExperimentPlanReport]:
        best_run = summary.get("best_run") or {}
        completed_runs = int(summary.get("completed_runs", 0) or 0)
        failed_runs = int(summary.get("failed_runs", 0) or 0)
        current_leading_model = best_run.get("leading_model")
        reports: list[CampaignExperimentPlanReport] = []
        for plan in plans:
            launch_ready = bool(best_run) and completed_runs > 0
            acceptance_status = "ready"
            notes: list[str] = []
            if not best_run:
                acceptance_status = "blocked"
                launch_ready = False
                notes.append("No completed benchmark run exists yet, so this plan has no empirical anchor.")
            if failed_runs:
                acceptance_status = "watch" if acceptance_status == "ready" else acceptance_status
                notes.append("The parent campaign had failed runs; stabilize the workflow before scaling this plan.")
            if current_leading_model and current_leading_model != ModelFamily.sounio_hypercomplex.value:
                acceptance_status = "watch" if acceptance_status == "ready" else acceptance_status
                notes.append(
                    f"The current control winner is `{current_leading_model}`, so this plan should be evaluated as a control-constrained challenge."
                )
            if float(best_run.get("sounio_auc", 0.0) or 0.0) < 0.75:
                notes.append("Sounio AUC is still modest in the parent campaign; keep lesion-sensitivity under close review.")
            if float(best_run.get("sounio_aspects_mae", 999.0) or 999.0) > 0.75:
                notes.append("ASPECTS calibration is still loose; inspect regional thresholds during acceptance review.")
            reports.append(
                CampaignExperimentPlanReport(
                    plan_id=plan.plan_id,
                    title=plan.title,
                    acceptance_status=acceptance_status,
                    launch_ready=launch_ready,
                    target_cohort=plan.target_cohort,
                    current_best_run_id=best_run.get("benchmark_run_id"),
                    current_leading_model=current_leading_model,
                    baseline_control=current_leading_model or ModelFamily.sounio_hypercomplex.value,
                    acceptance_criteria=plan.success_criteria,
                    acceptance_notes=notes or ["Plan is ready to launch against the current campaign evidence."],
                )
            )
        return reports

    @staticmethod
    def _clone_request(
        request: BenchmarkRequest,
        *,
        seed: int | None = None,
        external_test_manifest_path: str | None | object = _UNSET,
    ) -> BenchmarkRequest:
        external_path = request.external_test_manifest_path
        if external_test_manifest_path is not CampaignCoordinator._UNSET:
            external_path = external_test_manifest_path
        return request.model_copy(
            update={
                "seed": request.seed if seed is None else seed,
                "external_test_manifest_path": external_path,
            }
        )

    @staticmethod
    def _find_source_entry(entries: list[CampaignEntry], best_run: dict) -> CampaignEntry | None:
        benchmark_run_id = best_run.get("benchmark_run_id")
        label = best_run.get("label")
        for entry in entries:
            if benchmark_run_id and entry.benchmark_run_id == benchmark_run_id:
                return entry
        for entry in entries:
            if label and entry.label == label:
                return entry
        return None

    @staticmethod
    def _short_manifest_label(path: str) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, path).hex[:8]

    @staticmethod
    def _deserialize_follow_up_proposals(payload: list[dict]) -> list[CampaignFollowUpProposal]:
        return [CampaignFollowUpProposal.model_validate(item) for item in payload]

    @staticmethod
    def _deserialize_experiment_plans(payload: list[dict]) -> list[CampaignExperimentPlan]:
        return [CampaignExperimentPlan.model_validate(item) for item in payload]

    @staticmethod
    def _deserialize_experiment_plan_reports(payload: list[dict]) -> list[CampaignExperimentPlanReport]:
        return [CampaignExperimentPlanReport.model_validate(item) for item in payload]

    @staticmethod
    def _plan_context_for_proposal(proposal: CampaignFollowUpProposal, leading_model: str) -> tuple[str, str]:
        if proposal.proposal_id == "winner-reproducibility-sweep":
            target = "Current winning cohort, repeated with nearby seeds to measure stability and variance."
            if leading_model == ModelFamily.sounio_hypercomplex.value:
                hypothesis = "The current Sounio winner remains stable across nearby seeds and keeps control baselines behind it."
            else:
                hypothesis = (
                    "The current control winner defines a strong reference point, and the Sounio path should narrow the gap "
                    "under a reproducibility sweep."
                )
            return target, hypothesis
        target = "Alternate manifest(s) already present in the campaign, holding the winning setup fixed during transfer."
        hypothesis = (
            "The current winning setup is not just cohort-specific and should transfer to the alternate manifest while "
            "keeping the required baselines as fair controls."
        )
        return target, hypothesis

    def write_campaign_artifacts(self, campaign_id: str) -> CampaignRecord:
        return self._write_campaign_artifacts(self.storage.get_campaign(campaign_id))

    def _write_campaign_artifacts(self, campaign: CampaignRecord) -> CampaignRecord:
        campaign_id = campaign.campaign_id
        summary_path = self.storage.write_artifact_json(
            f"campaigns/{campaign_id}/campaign_summary.json",
            campaign.summary,
        )
        report_path = self.storage.write_artifact_text(
            f"campaigns/{campaign_id}/campaign_report.md",
            self._render_campaign_report(campaign),
        )
        plans_path = self.storage.write_artifact_json(
            f"campaigns/{campaign_id}/experiment_plans.json",
            campaign.summary.get("experiment_plans", []),
        )
        plan_reports_path = self.storage.write_artifact_json(
            f"campaigns/{campaign_id}/experiment_plan_reports.json",
            campaign.summary.get("experiment_plan_reports", []),
        )
        self.storage.register_artifact(
            RecordOwnerType.campaign,
            campaign_id,
            artifact_id=f"{campaign_id}:campaign_summary",
            name="campaign_summary",
            kind="json",
            path=summary_path,
            description="Aggregated campaign summary with run-level comparisons.",
        )
        self.storage.register_artifact(
            RecordOwnerType.campaign,
            campaign_id,
            artifact_id=f"{campaign_id}:campaign_report",
            name="campaign_report",
            kind="markdown",
            path=report_path,
            description="Human-readable report for the benchmark campaign.",
        )
        self.storage.register_artifact(
            RecordOwnerType.campaign,
            campaign_id,
            artifact_id=f"{campaign_id}:experiment_plans",
            name="experiment_plans",
            kind="json",
            path=plans_path,
            description="Structured experiment plans derived from the campaign evidence and follow-up proposals.",
        )
        self.storage.register_artifact(
            RecordOwnerType.campaign,
            campaign_id,
            artifact_id=f"{campaign_id}:experiment_plan_reports",
            name="experiment_plan_reports",
            kind="json",
            path=plan_reports_path,
            description="Acceptance-oriented plan reports derived from the campaign evidence, ready for cross-campaign comparison.",
        )
        return campaign

    def _render_campaign_report(self, campaign: CampaignRecord) -> str:
        lines = [
            f"# Campaign Report: {campaign.name}",
            "",
            f"- Campaign ID: `{campaign.campaign_id}`",
            f"- Status: `{campaign.status}`",
            f"- Objective: {campaign.objective or 'n/a'}",
            "",
            "## Summary",
            "",
        ]
        for key in (
            "total_runs",
            "queued_runs",
            "running_runs",
            "completed_runs",
            "failed_runs",
            "cancelled_runs",
        ):
            if key in campaign.summary:
                lines.append(f"- {key}: `{campaign.summary[key]}`")
        best_run = campaign.summary.get("best_run")
        if best_run:
            lines.extend(
                [
                    "",
                    "## Best Run",
                    "",
                    f"- Label: `{best_run.get('label', 'n/a')}`",
                    f"- Benchmark run: `{best_run.get('benchmark_run_id', 'n/a')}`",
                    f"- Leading model: `{best_run.get('leading_model', 'n/a')}`",
                    f"- Sounio Dice: `{best_run.get('sounio_isles_dice', 'n/a')}`",
                    f"- Sounio AUC: `{best_run.get('sounio_auc', 'n/a')}`",
                    f"- Sounio ASPECTS MAE: `{best_run.get('sounio_aspects_mae', 'n/a')}`",
                ]
            )
        run_summaries = campaign.summary.get("run_summaries", [])
        if run_summaries:
            lines.extend(["", "## Run Summaries", ""])
            for item in run_summaries:
                lines.extend(
                    [
                        f"### {item.get('label', 'run')}",
                        f"- Benchmark run: `{item.get('benchmark_run_id', 'n/a')}`",
                        f"- Dataset version: `{item.get('dataset_version', 'n/a')}`",
                        f"- Leading model: `{item.get('leading_model', 'n/a')}`",
                        f"- Sounio Dice: `{item.get('sounio_isles_dice', 'n/a')}`",
                        f"- Sounio AUC: `{item.get('sounio_auc', 'n/a')}`",
                        f"- Sounio ASPECTS MAE: `{item.get('sounio_aspects_mae', 'n/a')}`",
                        "",
                    ]
                )
        recommendations = campaign.summary.get("next_experiment_recommendations", [])
        if recommendations:
            lines.extend(["## Next Experiments", ""])
            lines.extend([f"- {item}" for item in recommendations])
            lines.append("")
        follow_ups = self._deserialize_follow_up_proposals(campaign.summary.get("follow_up_proposals", []))
        if follow_ups:
            lines.extend(["## Follow-Up Proposals", ""])
            for proposal in follow_ups:
                lines.extend(
                    [
                        f"### {proposal.title}",
                        f"- Proposal ID: `{proposal.proposal_id}`",
                        f"- Rationale: {proposal.rationale}",
                        f"- Planned runs: `{len(proposal.benchmark_specs)}`",
                        "",
                    ]
                )
        plans = self._deserialize_experiment_plans(campaign.summary.get("experiment_plans", []))
        if plans:
            lines.extend(["## Experiment Plans", ""])
            for plan in plans:
                lines.extend(
                    [
                        f"### {plan.title}",
                        f"- Plan ID: `{plan.plan_id}`",
                        f"- Hypothesis: {plan.hypothesis}",
                        f"- Target cohort: {plan.target_cohort}",
                        f"- Required baselines: `{', '.join(plan.required_baselines)}`",
                        "- Success criteria:",
                    ]
                )
                for criterion in plan.success_criteria:
                    lines.append(
                        f"  - `{criterion.metric}` {criterion.comparator} `{criterion.target}`: {criterion.rationale}"
                    )
                lines.append("")
        plan_reports = self._deserialize_experiment_plan_reports(campaign.summary.get("experiment_plan_reports", []))
        if plan_reports:
            lines.extend(["## Plan Reports", ""])
            for report in plan_reports:
                lines.extend(
                    [
                        f"### {report.title}",
                        f"- Plan ID: `{report.plan_id}`",
                        f"- Acceptance status: `{report.acceptance_status}`",
                        f"- Launch ready: `{report.launch_ready}`",
                        f"- Baseline control: `{report.baseline_control}`",
                    ]
                )
                if report.current_best_run_id:
                    lines.append(f"- Current best run: `{report.current_best_run_id}`")
                lines.append("- Acceptance notes:")
                for note in report.acceptance_notes:
                    lines.append(f"  - {note}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"
