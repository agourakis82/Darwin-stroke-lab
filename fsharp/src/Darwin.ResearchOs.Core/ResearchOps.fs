namespace Darwin.ResearchOs.Core

open System
open System.Collections.Generic
open Darwin.ResearchOs.Contracts

module internal ResearchValues =
    let sounioModelFamily = "sounio_hypercomplex"

    let resize (items: seq<'T>) = ResizeArray<'T>(items)

    let dict (pairs: seq<string * obj>) =
        let target = Dictionary<string, obj>()
        for (key, value) in pairs do
            target[key] <- value
        target

    let utcNow () = DateTime.UtcNow.ToString("O")

    let tryGetObj (key: string) (source: IDictionary<string, obj>) =
        if isNull (box source) then
            None
        elif source.ContainsKey(key) then
            let value = source[key]
            if isNull value then None else Some value
        else
            None

    let asString (value: obj) =
        match value with
        | null -> None
        | :? string as text -> Some text
        | _ -> Some(string value)

    let asFloat (value: obj) =
        match value with
        | null -> None
        | :? float as v -> Some v
        | :? float32 as v -> Some(float v)
        | :? decimal as v -> Some(float v)
        | :? int as v -> Some(float v)
        | :? int64 as v -> Some(float v)
        | :? string as text ->
            match Double.TryParse(text) with
            | true, parsed -> Some parsed
            | _ -> None
        | _ -> None

    let asInt (value: obj) =
        match value with
        | null -> None
        | :? int as v -> Some v
        | :? int64 as v -> Some(int v)
        | :? float as v -> Some(int v)
        | :? string as text ->
            match Int32.TryParse(text) with
            | true, parsed -> Some parsed
            | _ -> None
        | _ -> None

    let asDictionary (value: obj) =
        match value with
        | null -> None
        | :? Dictionary<string, obj> as d -> Some d
        | :? IDictionary<string, obj> as d ->
            let copy = Dictionary<string, obj>()
            for KeyValue(key, item) in d do
                copy[key] <- item
            Some copy
        | _ -> None

    let asDictionaryArray (value: obj) =
        match value with
        | null -> ResizeArray()
        | :? ResizeArray<Dictionary<string, obj>> as xs -> xs
        | :? seq<Dictionary<string, obj>> as xs -> resize xs
        | :? ResizeArray<obj> as xs ->
            resize (
                xs
                |> Seq.choose asDictionary
            )
        | :? seq<obj> as xs ->
            resize (
                xs
                |> Seq.choose asDictionary
            )
        | :? seq<IDictionary<string, obj>> as xs ->
            resize (
                xs
                |> Seq.map (fun entry ->
                    let copy = Dictionary<string, obj>()
                    for KeyValue(key, item) in entry do
                        copy[key] <- item
                    copy)
            )
        | _ -> ResizeArray()

    let asStringArray (value: obj) =
        match value with
        | null -> ResizeArray()
        | :? ResizeArray<string> as xs -> xs
        | :? seq<string> as xs -> resize xs
        | :? ResizeArray<obj> as xs ->
            resize (
                xs
                |> Seq.choose asString
            )
        | :? seq<obj> as xs ->
            resize (
                xs
                |> Seq.choose asString
            )
        | _ -> ResizeArray()

    let metricValue metricName (metrics: Dictionary<string, obj>) =
        match tryGetObj sounioModelFamily metrics |> Option.bind asDictionary with
        | None -> None
        | Some sounioMetrics ->
            tryGetObj metricName sounioMetrics
            |> Option.bind asDictionary
            |> Option.bind (tryGetObj "value")
            |> Option.bind asFloat

    let leadingModel (run: BenchmarkRunRecord) =
        run.Leaderboard
        |> tryGetObj "overall_rank"
        |> Option.map asDictionaryArray
        |> Option.defaultValue (ResizeArray())
        |> Seq.tryHead
        |> Option.bind (fun item -> tryGetObj "model_name" item)
        |> Option.bind asString

    let serializeCriterion (criterion: ExperimentSuccessCriterion) =
        dict
            [ "metric", box criterion.Metric
              "comparator", box criterion.Comparator
              "target", box criterion.Target
              "rationale", box criterion.Rationale ]

    let serializeBenchmarkRequest (request: BenchmarkRequest) =
        dict
            [ "dataset_manifest_path", box request.DatasetManifestPath
              "external_test_manifest_path", box (defaultArg request.ExternalTestManifestPath "")
              "train_split", box request.TrainSplit
              "test_split", box request.TestSplit
              "seed", box request.Seed ]

    let serializeBenchmarkSpec (spec: CampaignBenchmarkSpec) =
        dict
            [ "label", box spec.Label
              "request", box (serializeBenchmarkRequest spec.Request) ]

    let serializeFollowUpProposal (proposal: CampaignFollowUpProposal) =
        dict
            [ "proposal_id", box proposal.ProposalId
              "title", box proposal.Title
              "rationale", box proposal.Rationale
              "benchmark_specs", box (resize (proposal.BenchmarkSpecs |> Seq.map serializeBenchmarkSpec)) ]

    let serializePlan (plan: CampaignExperimentPlan) =
        dict
            [ "plan_id", box plan.PlanId
              "title", box plan.Title
              "hypothesis", box plan.Hypothesis
              "target_cohort", box plan.TargetCohort
              "rationale", box plan.Rationale
              "required_baselines", box (resize plan.RequiredBaselines)
              "success_criteria", box (resize (plan.SuccessCriteria |> Seq.map serializeCriterion))
              "benchmark_specs", box (resize (plan.BenchmarkSpecs |> Seq.map serializeBenchmarkSpec))
              "recommended_agent_request", box plan.RecommendedAgentRequest ]

    let serializePlanReport (report: CampaignExperimentPlanReport) =
        dict
            [ "plan_id", box report.PlanId
              "title", box report.Title
              "acceptance_status", box report.AcceptanceStatus
              "launch_ready", box report.LaunchReady
              "target_cohort", box report.TargetCohort
              "current_best_run_id", box (defaultArg report.CurrentBestRunId "")
              "current_leading_model", box (defaultArg report.CurrentLeadingModel "")
              "baseline_control", box report.BaselineControl
              "acceptance_criteria", box (resize (report.AcceptanceCriteria |> Seq.map serializeCriterion))
              "acceptance_notes", box (resize report.AcceptanceNotes) ]

module CampaignSemantics =
    open ResearchValues

    type Dependencies =
        { TryGetJob: string -> JobRecord option
          TryGetBenchmarkRun: string -> BenchmarkRunRecord option }

    let private requestWith (request: BenchmarkRequest) seed externalTestManifestPath =
        { request with
            Seed = defaultArg seed request.Seed
            ExternalTestManifestPath =
                match externalTestManifestPath with
                | Some value -> value
                | None -> request.ExternalTestManifestPath }

    let private benchmarkSummary (deps: Dependencies) (entries: seq<CampaignEntry>) =
        let runSummaries = ResizeArray<Dictionary<string, obj>>()
        let mutable bestEntry: Dictionary<string, obj> option = None
        let mutable bestSortKey: (float * float * float) option = None

        for entry in entries do
            match entry.BenchmarkRunId with
            | None -> ()
            | Some runId ->
                match deps.TryGetBenchmarkRun runId with
                | None -> ()
                | Some run ->
                    let dice = metricValue "isles_dice" run.Metrics |> Option.defaultValue 0.0
                    let auc = metricValue "auc" run.Metrics |> Option.defaultValue 0.0
                    let mae = metricValue "aspects_mae" run.Metrics |> Option.defaultValue 999.0
                    let leader = leadingModel run

                    let summary =
                        dict
                            [ "label", box entry.Label
                              "benchmark_run_id", box run.RunId
                              "dataset_version", box run.DatasetVersion
                              "leading_model", box (defaultArg leader "")
                              "sounio_isles_dice", box (Math.Round(dice, 4))
                              "sounio_auc", box (Math.Round(auc, 4))
                              "sounio_aspects_mae", box (Math.Round(mae, 4)) ]

                    runSummaries.Add(summary)

                    let sortKey = (dice, auc, -mae)
                    match bestSortKey with
                    | None ->
                        bestSortKey <- Some sortKey
                        bestEntry <- Some summary
                    | Some current when sortKey > current ->
                        bestSortKey <- Some sortKey
                        bestEntry <- Some summary
                    | _ -> ()

        dict
            [ "run_summaries", box runSummaries
              "best_run",
              box (
                  match bestEntry with
                  | Some entry -> entry
                  | None -> Dictionary<string, obj>()
              ) ]

    let private recommendNextExperiments (summary: Dictionary<string, obj>) =
        let recommendations = ResizeArray<string>()
        let failedRuns = tryGetObj "failed_runs" summary |> Option.bind asInt |> Option.defaultValue 0

        if failedRuns > 0 then
            recommendations.Add(
                "Inspect failed benchmark jobs first and stabilize the manifest or backend before widening the sweep."
            )

        let runSummaries =
            tryGetObj "run_summaries" summary
            |> Option.map asDictionaryArray
            |> Option.defaultValue (ResizeArray())

        let bestRun =
            tryGetObj "best_run" summary
            |> Option.bind asDictionary
            |> Option.defaultValue (Dictionary())

        if bestRun.Count > 0 then
            let leadingModelValue = tryGetObj "leading_model" bestRun |> Option.bind asString

            match leadingModelValue with
            | Some model when model <> sounioModelFamily ->
                recommendations.Add(
                    $"Use `{model}` as the control run and tune the Sounio path against the current campaign winner."
                )
            | _ ->
                recommendations.Add(
                    "Promote the best Sounio run to the next small-realistic cohort and hold the same baseline set fixed."
                )

            let aspectsMae = tryGetObj "sounio_aspects_mae" bestRun |> Option.bind asFloat |> Option.defaultValue 999.0
            if aspectsMae > 0.75 then
                recommendations.Add(
                    "Prioritize ASPECTS calibration and region threshold tuning before scaling to larger cohorts."
                )

            let auc = tryGetObj "sounio_auc" bestRun |> Option.bind asFloat |> Option.defaultValue 0.0
            if auc < 0.75 then
                recommendations.Add(
                    "Add a harder lesion-sensitivity sweep or a trained artifact comparison because Sounio AUC is still modest."
                )

        if runSummaries.Count >= 2 then
            let diceValues =
                runSummaries
                |> Seq.map (fun item -> tryGetObj "sounio_isles_dice" item |> Option.bind asFloat |> Option.defaultValue 0.0)
                |> Seq.toList

            if diceValues.Length >= 2 && (List.max diceValues - List.min diceValues) < 0.03 then
                recommendations.Add(
                    "The campaign spread is narrow; vary cohort composition or ablation settings instead of repeating nearby seeds."
                )

        if recommendations.Count = 0 then
            recommendations.Add("Expand this campaign with one stronger cohort variant and one ablation-focused control run.")

        recommendations

    let private planContextForProposal (proposal: CampaignFollowUpProposal) (leadingModel: string) =
        match proposal.ProposalId with
        | "winner-reproducibility-sweep" ->
            ( "current winning cohort",
              "The winning configuration should remain stable across nearby seeds before the cohort widens." )
        | "cross-manifest-transfer-check" ->
            ( "alternate manifest transfer cohort",
              $"The winning setup should transfer beyond the current manifest while remaining competitive against `{leadingModel}`." )
        | _ ->
            ( "small-realistic follow-up cohort",
              "The next experiment should preserve the winning signal while changing only one campaign variable at a time." )

    let private buildFollowUpProposals (campaign: CampaignRecord) (entries: ResizeArray<CampaignEntry>) (summary: Dictionary<string, obj>) =
        let proposals = ResizeArray<CampaignFollowUpProposal>()

        let bestRunId =
            tryGetObj "best_run" summary
            |> Option.bind asDictionary
            |> Option.bind (tryGetObj "benchmark_run_id")
            |> Option.bind asString

        let sourceEntry =
            bestRunId
            |> Option.bind (fun runId -> entries |> Seq.tryFind (fun entry -> entry.BenchmarkRunId = Some runId))

        match sourceEntry with
        | None -> proposals
        | Some source ->
            let reproducibilitySpecs =
                resize [
                    for offset in 0 .. 2 do
                        let seed = source.Request.Seed + offset
                        { Label = $"{source.Label}-replica-seed-{seed}"
                          Request = requestWith source.Request (Some seed) None }
                ]

            proposals.Add(
                { ProposalId = "winner-reproducibility-sweep"
                  Title = "Winner Reproducibility Sweep"
                  Rationale =
                      "Confirm that the current best run stays on top across nearby seeds before widening the cohort or changing the protocol."
                  BenchmarkSpecs = reproducibilitySpecs }
            )

            let alternateManifests =
                entries
                |> Seq.map (fun entry -> entry.Request.DatasetManifestPath)
                |> Seq.distinct
                |> Seq.filter (fun manifest -> manifest <> source.Request.DatasetManifestPath)
                |> Seq.toList

            if not alternateManifests.IsEmpty then
                let transferSpecs =
                    alternateManifests
                    |> Seq.map (fun manifest ->
                        let label = IO.Path.GetFileNameWithoutExtension(manifest)
                        { Label = $"{source.Label}-transfer-{label}"
                          Request = requestWith source.Request None (Some(Some manifest)) })
                    |> resize

                proposals.Add(
                    { ProposalId = "cross-manifest-transfer-check"
                      Title = "Cross-Manifest Transfer Check"
                      Rationale =
                          "Hold the winning training setup fixed and test it against alternate manifests already present in this campaign."
                      BenchmarkSpecs = transferSpecs }
                )

            proposals

    let private buildExperimentPlans (campaign: CampaignRecord) (summary: Dictionary<string, obj>) (proposals: ResizeArray<CampaignFollowUpProposal>) =
        let bestRun =
            tryGetObj "best_run" summary
            |> Option.bind asDictionary
            |> Option.defaultValue (Dictionary())

        let bestDice = tryGetObj "sounio_isles_dice" bestRun |> Option.bind asFloat |> Option.defaultValue 0.0
        let bestAuc = tryGetObj "sounio_auc" bestRun |> Option.bind asFloat |> Option.defaultValue 0.0
        let bestMae = tryGetObj "sounio_aspects_mae" bestRun |> Option.bind asFloat |> Option.defaultValue 999.0
        let leadingModel = tryGetObj "leading_model" bestRun |> Option.bind asString |> Option.defaultValue sounioModelFamily
        let requiredBaselines = resize [ "python_3d_conventional"; "julia_equivalent"; "cpp_equivalent" ]

        proposals
        |> Seq.choose (fun proposal ->
            proposal.BenchmarkSpecs |> Seq.tryHead |> Option.map (fun primarySpec ->
                let targetCohort, hypothesis = planContextForProposal proposal leadingModel
                let criteria =
                    resize
                        [ { Metric = "sounio_isles_dice"
                            Comparator = ">="
                            Target = string (Math.Round(max (bestDice - 0.03) 0.45, 4))
                            Rationale = "Keep lesion overlap close to the strongest observed campaign run." }
                          { Metric = "sounio_auc"
                            Comparator = ">="
                            Target = string (Math.Round(max (bestAuc - 0.02) 0.7, 4))
                            Rationale = "Preserve lesion ranking quality while changing only one campaign variable." }
                          { Metric = "sounio_aspects_mae"
                            Comparator = "<="
                            Target = string (Math.Round(min (bestMae + 0.15) 1.0, 4))
                            Rationale = "Do not let ASPECTS calibration drift while expanding the sweep." }
                          { Metric = "leaderboard_rank"
                            Comparator = "<="
                            Target = "2.0"
                            Rationale = "Sounio should remain competitive against the required control baselines." } ]

                let requestPayload =
                    dict
                        [ "objective",
                          box (
                              $"Execute experiment plan '{proposal.Title}' and determine whether Sounio meets the success criteria against the required baselines."
                          )
                          "surface", box "research"
                          "dataset_manifest_path", box primarySpec.Request.DatasetManifestPath
                          "external_test_manifest_path",
                          box (defaultArg primarySpec.Request.ExternalTestManifestPath "")
                          "train_split", box primarySpec.Request.TrainSplit
                          "test_split", box primarySpec.Request.TestSplit
                          "seed", box primarySpec.Request.Seed
                          "include_baseline_comparison", box true
                          "model_family", box sounioModelFamily
                          "research_question",
                          box (
                              $"Evaluate the plan '{proposal.Title}' for campaign {campaign.CampaignId}. Hypothesis: {hypothesis}"
                          )
                          "notes",
                          box (
                              $"campaign_id={campaign.CampaignId};proposal_id={proposal.ProposalId};plan_id=plan-{proposal.ProposalId}"
                          ) ]

                { PlanId = $"plan-{proposal.ProposalId}"
                  Title = proposal.Title
                  Hypothesis = hypothesis
                  TargetCohort = targetCohort
                  Rationale = proposal.Rationale
                  RequiredBaselines = requiredBaselines
                  SuccessCriteria = criteria
                  BenchmarkSpecs = proposal.BenchmarkSpecs
                  RecommendedAgentRequest = requestPayload }))
        |> resize

    let private buildExperimentPlanReports (summary: Dictionary<string, obj>) (plans: ResizeArray<CampaignExperimentPlan>) =
        let bestRun =
            tryGetObj "best_run" summary
            |> Option.bind asDictionary
            |> Option.defaultValue (Dictionary())

        let completedRuns = tryGetObj "completed_runs" summary |> Option.bind asInt |> Option.defaultValue 0
        let failedRuns = tryGetObj "failed_runs" summary |> Option.bind asInt |> Option.defaultValue 0
        let currentLeadingModel = tryGetObj "leading_model" bestRun |> Option.bind asString
        let bestRunId = tryGetObj "benchmark_run_id" bestRun |> Option.bind asString

        plans
        |> Seq.map (fun plan ->
            let mutable acceptanceStatus = "ready"
            let mutable launchReady = bestRun.Count > 0 && completedRuns > 0
            let notes = ResizeArray<string>()

            if bestRun.Count = 0 then
                acceptanceStatus <- "blocked"
                launchReady <- false
                notes.Add("No completed benchmark run exists yet, so this plan has no empirical anchor.")

            if failedRuns > 0 && acceptanceStatus = "ready" then
                acceptanceStatus <- "watch"
                notes.Add("The parent campaign had failed runs; stabilize the workflow before scaling this plan.")

            match currentLeadingModel with
            | Some model when model <> sounioModelFamily && acceptanceStatus = "ready" ->
                acceptanceStatus <- "watch"
                notes.Add(
                    $"The current control winner is `{model}`, so this plan should be evaluated as a control-constrained challenge."
                )
            | Some model when model <> sounioModelFamily ->
                notes.Add(
                    $"The current control winner is `{model}`, so this plan should be evaluated as a control-constrained challenge."
                )
            | _ -> ()

            let auc = tryGetObj "sounio_auc" bestRun |> Option.bind asFloat |> Option.defaultValue 0.0
            if auc < 0.75 then
                notes.Add("Sounio AUC is still modest in the parent campaign; keep lesion-sensitivity under close review.")

            let mae = tryGetObj "sounio_aspects_mae" bestRun |> Option.bind asFloat |> Option.defaultValue 999.0
            if mae > 0.75 then
                notes.Add("ASPECTS calibration is still loose; inspect regional thresholds during acceptance review.")

            if notes.Count = 0 then
                notes.Add("Plan is ready to launch against the current campaign evidence.")

            { PlanId = plan.PlanId
              Title = plan.Title
              AcceptanceStatus = acceptanceStatus
              LaunchReady = launchReady
              TargetCohort = plan.TargetCohort
              CurrentBestRunId = bestRunId
              CurrentLeadingModel = currentLeadingModel
              BaselineControl = defaultArg currentLeadingModel sounioModelFamily
              AcceptanceCriteria = plan.SuccessCriteria
              AcceptanceNotes = notes })
        |> resize

    let reconcile (deps: Dependencies) (campaign: CampaignRecord) =
        let counts = Dictionary<string, int>()

        let bump status =
            let current = if counts.ContainsKey(status) then counts[status] else 0
            counts[status] <- current + 1

        let entries =
            campaign.BenchmarkSpecs
            |> Seq.map (fun entry ->
                match entry.JobId with
                | None ->
                    bump entry.Status
                    entry
                | Some jobId ->
                    match deps.TryGetJob jobId with
                    | None ->
                        bump "failed"
                        { entry with
                            Status = "failed"
                            Error = $"Missing job record: {jobId}" }
                    | Some job ->
                        let benchmarkRunId =
                            tryGetObj "benchmark_run_id" job.ResultPayload
                            |> Option.bind asString

                        bump job.Status
                        { entry with
                            Status = job.Status
                            BenchmarkRunId = benchmarkRunId
                            Error = job.Error })
            |> resize

        let statuses = entries |> Seq.map (fun entry -> entry.Status) |> Set.ofSeq

        let status =
            if entries.Count = 0 then
                "created"
            elif statuses = Set.ofList [ "completed" ] then
                "completed"
            elif statuses = Set.ofList [ "cancelled" ] then
                "cancelled"
            elif statuses.IsSubsetOf(Set.ofList [ "completed"; "failed"; "cancelled" ]) && statuses.Contains("failed") then
                "failed"
            else
                "running"

        let completedRunIds =
            entries
            |> Seq.choose (fun entry -> entry.BenchmarkRunId)
            |> resize

        let benchmarkSummary = benchmarkSummary deps entries
        let summary =
            dict
                [ "total_runs", box entries.Count
                  "queued_runs", box (if counts.ContainsKey("queued") then counts["queued"] else 0)
                  "running_runs", box (if counts.ContainsKey("running") then counts["running"] else 0)
                  "completed_runs", box (if counts.ContainsKey("completed") then counts["completed"] else 0)
                  "failed_runs", box (if counts.ContainsKey("failed") then counts["failed"] else 0)
                  "cancelled_runs", box (if counts.ContainsKey("cancelled") then counts["cancelled"] else 0)
                  "completed_benchmark_run_ids", box completedRunIds ]

        for KeyValue(key, value) in benchmarkSummary do
            summary[key] <- value

        let recommendations = recommendNextExperiments summary
        summary["next_experiment_recommendations"] <- box recommendations

        let followUps = buildFollowUpProposals campaign entries summary
        summary["follow_up_proposals"] <- box (resize (followUps |> Seq.map serializeFollowUpProposal))

        let plans = buildExperimentPlans campaign summary followUps
        summary["experiment_plans"] <- box (resize (plans |> Seq.map serializePlan))

        let planReports = buildExperimentPlanReports summary plans
        summary["experiment_plan_reports"] <- box (resize (planReports |> Seq.map serializePlanReport))

        let changed =
            campaign.Status <> status
            || campaign.BenchmarkSpecs <> entries
            || campaign.Summary.Count <> summary.Count

        { campaign with
            Status = status
            BenchmarkSpecs = entries
            Summary = summary
            UpdatedAt = if changed then utcNow () else campaign.UpdatedAt }

module ProgramSemantics =
    open ResearchValues

    let summarizeCampaign (campaign: CampaignRecord) =
        let summary = campaign.Summary
        let bestRun =
            tryGetObj "best_run" summary
            |> Option.bind asDictionary
            |> Option.defaultValue (Dictionary())

        let planReports =
            tryGetObj "experiment_plan_reports" summary
            |> Option.map asDictionaryArray
            |> Option.defaultValue (ResizeArray())

        let countByStatus value =
            planReports
            |> Seq.filter (fun item -> tryGetObj "acceptance_status" item |> Option.bind asString = Some value)
            |> Seq.length

        let topPlanId =
            planReports
            |> Seq.tryFind (fun item -> tryGetObj "launch_ready" item |> Option.defaultValue (box false) |> unbox<bool>)
            |> Option.bind (fun item -> tryGetObj "plan_id" item |> Option.bind asString)

        { CampaignId = campaign.CampaignId
          Name = campaign.Name
          Status = campaign.Status
          TotalRuns = tryGetObj "total_runs" summary |> Option.bind asInt |> Option.defaultValue 0
          CompletedRuns = tryGetObj "completed_runs" summary |> Option.bind asInt |> Option.defaultValue 0
          FailedRuns = tryGetObj "failed_runs" summary |> Option.bind asInt |> Option.defaultValue 0
          BestRunId = tryGetObj "benchmark_run_id" bestRun |> Option.bind asString
          LeadingModel = tryGetObj "leading_model" bestRun |> Option.bind asString
          SounioIslesDice = tryGetObj "sounio_isles_dice" bestRun |> Option.bind asFloat |> Option.defaultValue 0.0
          SounioAuc = tryGetObj "sounio_auc" bestRun |> Option.bind asFloat |> Option.defaultValue 0.0
          SounioAspectsMae = tryGetObj "sounio_aspects_mae" bestRun |> Option.bind asFloat |> Option.defaultValue 999.0
          ReadyPlanCount = countByStatus "ready"
          WatchPlanCount = countByStatus "watch"
          BlockedPlanCount = countByStatus "blocked"
          TopPlanId = topPlanId }

    let private sortKey (summary: PortfolioCampaignSummary) =
        (summary.SounioIslesDice, summary.SounioAuc, -summary.SounioAspectsMae, summary.ReadyPlanCount)

    let private deriveStatus (campaigns: ResizeArray<CampaignRecord>) =
        if campaigns.Count = 0 then
            "created"
        else
            let statuses = campaigns |> Seq.map (fun campaign -> campaign.Status) |> Set.ofSeq
            if statuses = Set.ofList [ "completed" ] then
                "completed"
            elif statuses.Contains("running") || statuses.Contains("created") then
                "active"
            elif statuses.Contains("failed") || statuses.Contains("cancelled") then
                "blocked"
            else
                "active"

    let private nextActions (program: ProgramRecord) (summaries: ResizeArray<PortfolioCampaignSummary>) =
        if summaries.Count = 0 then
            resize [ $"Program `{program.Name}` does not have campaigns yet; create the first campaign to establish a baseline." ]
        else
            let actions = ResizeArray<string>()
            let leader = summaries[0]
            let leaderPlanId = defaultArg leader.TopPlanId "n/a"
            actions.Add(
                $"Use campaign `{leader.Name}` as the current leader for program `{program.Name}` and prioritize plan `{leaderPlanId}`."
            )

            let blocked = summaries |> Seq.filter (fun item -> item.BlockedPlanCount > 0) |> Seq.length
            if blocked > 0 then
                actions.Add(
                    $"{blocked} campaign(s) in this program still have blocked plans; clear those acceptance blockers before widening scope."
                )

            let anyBestRuns =
                summaries
                |> Seq.filter (fun item -> item.BestRunId.IsSome)
                |> Seq.toList

            if not anyBestRuns.IsEmpty && anyBestRuns |> List.forall (fun item -> item.LeadingModel <> Some sounioModelFamily) then
                actions.Add("This program is still baseline-led; the next campaign should explicitly challenge the current control winner.")

            actions

    let reconcile (allCampaigns: seq<CampaignRecord>) (program: ProgramRecord) =
        let linked =
            allCampaigns
            |> Seq.filter (fun campaign ->
                campaign.ProgramId = Some program.ProgramId
                || program.CampaignIds.Contains(campaign.CampaignId))
            |> Seq.groupBy (fun campaign -> campaign.CampaignId)
            |> Seq.map (fun (_, xs) -> xs |> Seq.head)
            |> resize

        let summaries = linked |> Seq.map summarizeCampaign |> Seq.sortByDescending sortKey |> resize
        let status = deriveStatus linked
        let summary =
            dict
                [ "total_campaigns", box summaries.Count
                  "completed_campaigns", box (linked |> Seq.filter (fun item -> item.Status = "completed") |> Seq.length)
                  "running_campaigns", box (linked |> Seq.filter (fun item -> item.Status = "running") |> Seq.length)
                  "failed_campaigns", box (linked |> Seq.filter (fun item -> item.Status = "failed") |> Seq.length)
                  "leading_campaign_id", box (if summaries.Count > 0 then summaries[0].CampaignId else "")
                  "campaigns",
                  box (
                      resize (
                          summaries
                          |> Seq.map (fun item ->
                              dict
                                  [ "campaign_id", box item.CampaignId
                                    "name", box item.Name
                                    "status", box item.Status
                                    "total_runs", box item.TotalRuns
                                    "completed_runs", box item.CompletedRuns
                                    "failed_runs", box item.FailedRuns
                                    "best_run_id", box (defaultArg item.BestRunId "")
                                    "leading_model", box (defaultArg item.LeadingModel "")
                                    "sounio_isles_dice", box item.SounioIslesDice
                                    "sounio_auc", box item.SounioAuc
                                    "sounio_aspects_mae", box item.SounioAspectsMae
                                    "ready_plan_count", box item.ReadyPlanCount
                                    "watch_plan_count", box item.WatchPlanCount
                                    "blocked_plan_count", box item.BlockedPlanCount
                                    "top_plan_id", box (defaultArg item.TopPlanId "") ])
                      )
                  )
                  "next_actions", box (nextActions program summaries) ]

        { program with
            Status = status
            CampaignIds = linked |> Seq.map (fun item -> item.CampaignId) |> resize
            Summary = summary
            UpdatedAt = utcNow () }

module PortfolioSemantics =
    open ResearchValues
    open ProgramSemantics

    let private campaignSortKey (summary: PortfolioCampaignSummary) =
        (summary.SounioIslesDice, summary.SounioAuc, -summary.SounioAspectsMae, summary.ReadyPlanCount)

    let private programSortKey (summary: PortfolioProgramSummary) =
        (summary.SounioIslesDice, summary.SounioAuc, -summary.SounioAspectsMae, summary.ReadyPlanCount, summary.CompletedCampaigns)

    let private campaignNextActions (summaries: ResizeArray<PortfolioCampaignSummary>) =
        if summaries.Count = 0 then
            resize [ "Create the first benchmark campaign before attempting portfolio-level comparisons." ]
        else
            let actions = ResizeArray<string>()
            let leader = summaries[0]
            let leaderPlanId = defaultArg leader.TopPlanId "n/a"
            actions.Add(
                $"Use campaign `{leader.Name}` as the current portfolio leader and prioritize plan `{leaderPlanId}` for the next launch."
            )

            let blocked = summaries |> Seq.filter (fun item -> item.BlockedPlanCount > 0) |> Seq.length
            if blocked > 0 then
                actions.Add($"{blocked} campaign(s) still have blocked plans; stabilize their parent evidence before promoting them.")

            let watch = summaries |> Seq.filter (fun item -> item.WatchPlanCount > 0) |> Seq.length
            if watch > 0 then
                actions.Add($"{watch} campaign(s) are in watch status; treat them as control-sensitive until their acceptance notes clear.")

            let withBestRuns = summaries |> Seq.filter (fun item -> item.BestRunId.IsSome) |> Seq.toList
            if not withBestRuns.IsEmpty && withBestRuns |> List.forall (fun item -> item.LeadingModel <> Some sounioModelFamily) then
                actions.Add("No campaign is Sounio-led yet; treat the current portfolio as baseline-constrained and tune the Sounio path next.")

            actions

    let private programNextActions (summaries: ResizeArray<PortfolioProgramSummary>) =
        if summaries.Count = 0 then
            resize [ "Create the first program before attempting program-level portfolio comparisons." ]
        else
            let actions = ResizeArray<string>()
            let leader = summaries[0]
            actions.Add($"Use program `{leader.Name}` as the current scientific lead and prioritize its ready campaign plans first.")

            let blocked =
                summaries
                |> Seq.filter (fun item -> item.BlockedPlanCount > 0 || item.Status = "blocked")
                |> Seq.length

            if blocked > 0 then
                actions.Add($"{blocked} program(s) remain blocked or partially blocked; clear their campaign acceptance issues before expanding scope.")

            let withLeaders = summaries |> Seq.filter (fun item -> item.LeadingCampaignId.IsSome) |> Seq.toList
            if not withLeaders.IsEmpty && withLeaders |> List.forall (fun item -> item.LeadingModel <> Some sounioModelFamily) then
                actions.Add("No program is Sounio-led yet; the next program increment should explicitly challenge the portfolio's control winner.")

            actions

    let buildCampaignPortfolio (campaigns: seq<CampaignRecord>) =
        let summaries = campaigns |> Seq.map summarizeCampaign |> Seq.sortByDescending campaignSortKey |> resize
        { PortfolioId = "campaign-portfolio"
          GeneratedAt = utcNow ()
          TotalCampaigns = summaries.Count
          CompletedCampaigns = summaries |> Seq.filter (fun item -> item.Status = "completed") |> Seq.length
          RunningCampaigns = summaries |> Seq.filter (fun item -> item.Status = "running") |> Seq.length
          FailedCampaigns = summaries |> Seq.filter (fun item -> item.Status = "failed") |> Seq.length
          LeadingCampaignId = if summaries.Count > 0 then Some summaries[0].CampaignId else None
          Campaigns = summaries
          NextActions = campaignNextActions summaries }

    let private summarizeProgram (program: ProgramRecord) =
        let summary = program.Summary
        let campaigns =
            tryGetObj "campaigns" summary
            |> Option.map asDictionaryArray
            |> Option.defaultValue (ResizeArray())

        let leader =
            if campaigns.Count > 0 then campaigns[0] else Dictionary<string, obj>()

        { ProgramId = program.ProgramId
          Name = program.Name
          Status = program.Status
          TotalCampaigns = tryGetObj "total_campaigns" summary |> Option.bind asInt |> Option.defaultValue 0
          CompletedCampaigns = tryGetObj "completed_campaigns" summary |> Option.bind asInt |> Option.defaultValue 0
          RunningCampaigns = tryGetObj "running_campaigns" summary |> Option.bind asInt |> Option.defaultValue 0
          FailedCampaigns = tryGetObj "failed_campaigns" summary |> Option.bind asInt |> Option.defaultValue 0
          LeadingCampaignId = tryGetObj "leading_campaign_id" summary |> Option.bind asString
          LeadingModel = tryGetObj "leading_model" leader |> Option.bind asString
          SounioIslesDice = tryGetObj "sounio_isles_dice" leader |> Option.bind asFloat |> Option.defaultValue 0.0
          SounioAuc = tryGetObj "sounio_auc" leader |> Option.bind asFloat |> Option.defaultValue 0.0
          SounioAspectsMae = tryGetObj "sounio_aspects_mae" leader |> Option.bind asFloat |> Option.defaultValue 999.0
          ReadyPlanCount = campaigns |> Seq.sumBy (fun item -> tryGetObj "ready_plan_count" item |> Option.bind asInt |> Option.defaultValue 0)
          WatchPlanCount = campaigns |> Seq.sumBy (fun item -> tryGetObj "watch_plan_count" item |> Option.bind asInt |> Option.defaultValue 0)
          BlockedPlanCount = campaigns |> Seq.sumBy (fun item -> tryGetObj "blocked_plan_count" item |> Option.bind asInt |> Option.defaultValue 0) }

    let buildProgramPortfolio (programs: seq<ProgramRecord>) =
        let summaries = programs |> Seq.map summarizeProgram |> Seq.sortByDescending programSortKey |> resize
        { PortfolioId = "program-portfolio"
          GeneratedAt = utcNow ()
          TotalPrograms = summaries.Count
          ActivePrograms = summaries |> Seq.filter (fun item -> item.Status = "active") |> Seq.length
          CompletedPrograms = summaries |> Seq.filter (fun item -> item.Status = "completed") |> Seq.length
          BlockedPrograms = summaries |> Seq.filter (fun item -> item.Status = "blocked") |> Seq.length
          LeadingProgramId = if summaries.Count > 0 then Some summaries[0].ProgramId else None
          Programs = summaries
          NextActions = programNextActions summaries }
