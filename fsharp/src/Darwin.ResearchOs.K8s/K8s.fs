namespace Darwin.ResearchOs.K8s

open System
open System.Collections.Generic
open System.Diagnostics
open System.IO
open Darwin.ResearchOs.Contracts
open Darwin.ResearchOs.Core
open System.Text
open System.Text.Json

type QueueTarget =
    { Namespace: string
      LocalQueue: string
      ClusterQueue: string option }

type ContainerImageBinding =
    { ApiImage: string
      WorkerImage: string }

type BenchmarkJobTemplate =
    { JobKind: string
      EntryPoint: string
      Args: string list
      QueueTarget: QueueTarget
      SharedDatasetPath: string
      ArtifactPrefix: string
      ImageBinding: ContainerImageBinding
      DatasetManifestPath: string
      ExternalTestManifestPath: string option
      TrainSplit: string
      TestSplit: string
      Seed: int }

type SounioRuntimeJobTemplate =
    { JobKind: string
      EntryPoint: string
      Args: string list
      QueueTarget: QueueTarget
      SharedDatasetPath: string
      ArtifactPrefix: string
      ImageBinding: ContainerImageBinding
      KernelPath: string option
      RequireSnio: bool }

type KubernetesParityScheduler(target: QueueTarget) =
    interface IJobScheduler with
        member _.Name = $"k8s:{target.Namespace}/{target.LocalQueue}"
        member _.SupportsKueue = true
        member _.SupportsCancellation = true

module internal Values =
    let utcNow () = DateTime.UtcNow.ToString("O")

    let isLegacyPythonWorkerImage (workerImage: string) =
        not (String.IsNullOrWhiteSpace workerImage)
        && workerImage.Contains("sounio-stroke-lab-worker", StringComparison.OrdinalIgnoreCase)

    let defaultEntryPointForImage (workerImage: string) =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_ENTRYPOINT") with
        | null
        | "" ->
            if isLegacyPythonWorkerImage workerImage then
                "sounio-stroke-lab"
            else
                "/app/darwin-research-os-worker"
        | value -> value

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
        | :? string as text when not (String.IsNullOrWhiteSpace text) -> Some text
        | :? string -> None
        | _ ->
            let text = string value
            if String.IsNullOrWhiteSpace text then None else Some text

    let asBool (value: obj) =
        match value with
        | null -> None
        | :? bool as flag -> Some flag
        | :? string as text ->
            match Boolean.TryParse(text) with
            | true, parsed -> Some parsed
            | _ -> None
        | _ -> None

    let asInt (value: obj) =
        match value with
        | null -> None
        | :? int as number -> Some number
        | :? int64 as number -> Some(int number)
        | :? string as text ->
            match Int32.TryParse(text) with
            | true, parsed -> Some parsed
            | _ -> None
        | _ -> None

    let asDictionary (value: obj) =
        match value with
        | null -> None
        | :? Dictionary<string, obj> as dictionary -> Some dictionary
        | :? IDictionary<string, obj> as source ->
            let copy = Dictionary<string, obj>()
            for KeyValue(key, item) in source do
                copy[key] <- item
            Some copy
        | _ -> None

    let asDictionaryArray (value: obj) =
        match value with
        | null -> ResizeArray()
        | :? ResizeArray<Dictionary<string, obj>> as items -> items
        | :? seq<Dictionary<string, obj>> as items -> ResizeArray(items)
        | :? ResizeArray<obj> as items ->
            ResizeArray(items |> Seq.choose asDictionary)
        | :? seq<obj> as items ->
            ResizeArray(items |> Seq.choose asDictionary)
        | _ -> ResizeArray()

    let asStringArray (value: obj) =
        match value with
        | null -> ResizeArray()
        | :? ResizeArray<string> as items -> items
        | :? seq<string> as items -> ResizeArray(items)
        | :? ResizeArray<obj> as items ->
            ResizeArray(items |> Seq.choose asString)
        | :? seq<obj> as items ->
            ResizeArray(items |> Seq.choose asString)
        | _ -> ResizeArray()

    let benchmarkRequestFromDictionary (source: IDictionary<string, obj>) =
        let requestDictionary =
            match tryGetObj "request" source |> Option.bind asDictionary with
            | Some nested -> nested :> IDictionary<string, obj>
            | None -> source

        { DatasetManifestPath =
            requestDictionary
            |> tryGetObj "dataset_manifest_path"
            |> Option.bind asString
            |> Option.defaultValue "/datasets/manifest.json"
          ExternalTestManifestPath =
            requestDictionary
            |> tryGetObj "external_test_manifest_path"
            |> Option.bind asString
          TrainSplit =
            requestDictionary
            |> tryGetObj "train_split"
            |> Option.bind asString
            |> Option.defaultValue "train"
          TestSplit =
            requestDictionary
            |> tryGetObj "test_split"
            |> Option.bind asString
            |> Option.defaultValue "test"
          Seed =
            requestDictionary
            |> tryGetObj "seed"
            |> Option.bind asInt
            |> Option.defaultValue 13 }

module Templates =
    let benchmark imageBinding (request: BenchmarkRequest) =
        let args = ResizeArray<string>([ "run-benchmark"; "--manifest"; request.DatasetManifestPath ])
        match request.ExternalTestManifestPath with
        | Some path when not (String.IsNullOrWhiteSpace path) ->
            args.Add("--external-test-manifest")
            args.Add(path)
        | _ -> ()
        args.Add("--train-split")
        args.Add(request.TrainSplit)
        args.Add("--test-split")
        args.Add(request.TestSplit)
        args.Add("--seed")
        args.Add(string request.Seed)

        { JobKind = "benchmark"
          EntryPoint = Values.defaultEntryPointForImage imageBinding.WorkerImage
          Args = List.ofSeq args
          QueueTarget =
              { Namespace = "darwin-genomics"
                LocalQueue = "darwin-lab"
                ClusterQueue = Some "darwin-shared" }
          SharedDatasetPath = "/datasets"
          ArtifactPrefix = "benchmark/"
          ImageBinding = imageBinding
          DatasetManifestPath = request.DatasetManifestPath
          ExternalTestManifestPath = request.ExternalTestManifestPath
          TrainSplit = request.TrainSplit
          TestSplit = request.TestSplit
          Seed = request.Seed }

    let sounioRuntime imageBinding kernelPath requireSnio =
        let args = ResizeArray<string>([ "run-sounio-runtime" ])
        match kernelPath with
        | Some path when not (String.IsNullOrWhiteSpace(path)) ->
            args.Add("--kernel-path")
            args.Add(path)
        | _ -> ()
        if requireSnio then
            args.Add("--require-snio")

        { JobKind = "sounio_runtime"
          EntryPoint = Values.defaultEntryPointForImage imageBinding.WorkerImage
          Args = List.ofSeq args
          QueueTarget =
              { Namespace = "darwin-genomics"
                LocalQueue = "darwin-lab"
                ClusterQueue = Some "darwin-shared" }
          SharedDatasetPath = "/datasets"
          ArtifactPrefix = "sounio-runtime/"
          ImageBinding = imageBinding
          KernelPath = kernelPath
          RequireSnio = requireSnio }

module Rendering =
    let renderSummary (template: BenchmarkJobTemplate) =
        { Kind = "Job"
          JobKind = template.JobKind
          EntryPoint = template.EntryPoint
          Args = ResizeArray(template.Args)
          K8sNamespace = template.QueueTarget.Namespace
          LocalQueue = template.QueueTarget.LocalQueue
          ClusterQueue = template.QueueTarget.ClusterQueue
          SharedDatasetPath = template.SharedDatasetPath
          ArtifactPrefix = template.ArtifactPrefix
          ApiImage = template.ImageBinding.ApiImage
          WorkerImage = template.ImageBinding.WorkerImage
          DatasetManifestPath = Some template.DatasetManifestPath
          ExternalTestManifestPath = template.ExternalTestManifestPath
          TrainSplit = Some template.TrainSplit
          TestSplit = Some template.TestSplit
          Seed = Some template.Seed
          KernelPath = None
          RequireSnio = None }

    let renderSounioRuntimeSummary (template: SounioRuntimeJobTemplate) =
        { Kind = "Job"
          JobKind = template.JobKind
          EntryPoint = template.EntryPoint
          Args = ResizeArray(template.Args)
          K8sNamespace = template.QueueTarget.Namespace
          LocalQueue = template.QueueTarget.LocalQueue
          ClusterQueue = template.QueueTarget.ClusterQueue
          SharedDatasetPath = template.SharedDatasetPath
          ArtifactPrefix = template.ArtifactPrefix
          ApiImage = template.ImageBinding.ApiImage
          WorkerImage = template.ImageBinding.WorkerImage
          DatasetManifestPath = None
          ExternalTestManifestPath = None
          TrainSplit = None
          TestSplit = None
          Seed = None
          KernelPath = template.KernelPath
          RequireSnio = Some template.RequireSnio }

module DeploymentPlans =
    open Values

    let private queueOf rendered =
        rendered.K8sNamespace, rendered.LocalQueue, rendered.ClusterQueue

    let private activeItemFromEntry imageBinding (campaign: CampaignRecord) (entry: CampaignEntry) =
        let rendered =
            Templates.benchmark imageBinding entry.Request
            |> Rendering.renderSummary

        let notes = ResizeArray<string>()
        entry.JobId |> Option.iter (fun jobId -> notes.Add($"job_id={jobId}"))
        entry.BenchmarkRunId |> Option.iter (fun runId -> notes.Add($"benchmark_run_id={runId}"))
        if not (String.IsNullOrWhiteSpace entry.Error) then
            notes.Add($"job_error={entry.Error}")

        { ItemId = entry.EntryId
          Label = entry.Label
          SourceKind = "campaign_entry"
          SourceId = entry.EntryId
          CurrentStatus = entry.Status
          LaunchReady = entry.Status = "queued" || entry.Status = "created"
          AcceptanceStatus = None
          CampaignId = campaign.CampaignId
          ProgramId = campaign.ProgramId
          RenderedJob = rendered
          Notes = notes }

    let private recommendedItemsFromCampaign imageBinding (campaign: CampaignRecord) =
        let planReports =
            campaign.Summary
            |> tryGetObj "experiment_plan_reports"
            |> Option.map asDictionaryArray
            |> Option.defaultValue (ResizeArray())
            |> Seq.choose (fun report ->
                tryGetObj "plan_id" report
                |> Option.bind asString
                |> Option.map (fun planId -> planId, report))
            |> dict

        campaign.Summary
        |> tryGetObj "experiment_plans"
        |> Option.map asDictionaryArray
        |> Option.defaultValue (ResizeArray())
        |> Seq.collect (fun plan ->
            let planId =
                tryGetObj "plan_id" plan
                |> Option.bind asString
                |> Option.defaultValue (Guid.NewGuid().ToString("N"))

            let title =
                tryGetObj "title" plan
                |> Option.bind asString
                |> Option.defaultValue planId

            let rationale = tryGetObj "rationale" plan |> Option.bind asString
            let targetCohort = tryGetObj "target_cohort" plan |> Option.bind asString

            let report =
                match planReports.TryGetValue(planId) with
                | true, found -> Some found
                | _ -> None

            let acceptanceStatus =
                report
                |> Option.bind (fun item -> tryGetObj "acceptance_status" item |> Option.bind asString)

            let launchReady =
                report
                |> Option.bind (fun item -> tryGetObj "launch_ready" item |> Option.bind asBool)
                |> Option.defaultValue false

            let acceptanceNotes =
                report
                |> Option.bind (fun item -> tryGetObj "acceptance_notes" item)
                |> Option.map asStringArray
                |> Option.defaultValue (ResizeArray())

            plan
            |> tryGetObj "benchmark_specs"
            |> Option.map asDictionaryArray
            |> Option.defaultValue (ResizeArray())
            |> Seq.mapi (fun index spec ->
                let request = benchmarkRequestFromDictionary spec
                let specLabel =
                    tryGetObj "label" spec
                    |> Option.bind asString
                    |> Option.defaultValue $"{title}-spec-{index + 1}"

                let rendered =
                    Templates.benchmark imageBinding request
                    |> Rendering.renderSummary

                let notes = ResizeArray<string>()
                rationale |> Option.iter (fun value -> notes.Add($"rationale={value}"))
                targetCohort |> Option.iter (fun value -> notes.Add($"target_cohort={value}"))
                for note in acceptanceNotes do
                    notes.Add(note)

                { ItemId = $"{planId}-spec-{index + 1}"
                  Label = specLabel
                  SourceKind = "experiment_plan"
                  SourceId = planId
                  CurrentStatus = defaultArg acceptanceStatus "planned"
                  LaunchReady = launchReady
                  AcceptanceStatus = acceptanceStatus
                  CampaignId = campaign.CampaignId
                  ProgramId = campaign.ProgramId
                  RenderedJob = rendered
                  Notes = notes }))
        |> ResizeArray

    let buildCampaignDeploymentPlan imageBinding (campaign: CampaignRecord) =
        let activeItems =
            campaign.BenchmarkSpecs
            |> Seq.map (activeItemFromEntry imageBinding campaign)
            |> ResizeArray

        let recommendedItems = recommendedItemsFromCampaign imageBinding campaign

        let nextActions =
            campaign.Summary
            |> tryGetObj "next_experiment_recommendations"
            |> Option.map asStringArray
            |> Option.defaultValue (ResizeArray())

        let queueNamespace, localQueue, clusterQueue =
            match Seq.tryHead activeItems with
            | Some item -> queueOf item.RenderedJob
            | None ->
                let fallback =
                    Templates.benchmark imageBinding (Defaults.benchmarkRequest "/datasets/manifest.json")
                    |> Rendering.renderSummary
                queueOf fallback

        { DeploymentPlanId = $"campaign-{campaign.CampaignId}-k8s"
          GeneratedAt = utcNow ()
          CampaignId = campaign.CampaignId
          CampaignName = campaign.Name
          CampaignStatus = campaign.Status
          ProgramId = campaign.ProgramId
          Objective = campaign.Objective
          QueueNamespace = queueNamespace
          LocalQueue = localQueue
          ClusterQueue = clusterQueue
          TotalItems = activeItems.Count + recommendedItems.Count
          LaunchReadyItems =
            (activeItems |> Seq.filter (fun item -> item.LaunchReady) |> Seq.length)
            + (recommendedItems |> Seq.filter (fun item -> item.LaunchReady) |> Seq.length)
          BlockedItems =
            recommendedItems
            |> Seq.filter (fun item -> item.AcceptanceStatus = Some "blocked")
            |> Seq.length
          ActiveItems = activeItems
          RecommendedItems = recommendedItems
          NextActions = nextActions }

    let buildProgramDeploymentPlan imageBinding (program: ProgramRecord) (campaigns: seq<CampaignRecord>) =
        let linkedCampaignIds = Set.ofSeq program.CampaignIds

        let orderedCampaignIds =
            program.Summary
            |> tryGetObj "campaigns"
            |> Option.map asDictionaryArray
            |> Option.defaultValue (ResizeArray())
            |> Seq.choose (fun item -> tryGetObj "campaign_id" item |> Option.bind asString)
            |> Seq.toList

        let orderMap =
            orderedCampaignIds
            |> Seq.mapi (fun index campaignId -> campaignId, index)
            |> dict

        let orderedCampaigns =
            campaigns
            |> Seq.filter (fun campaign ->
                campaign.ProgramId = Some program.ProgramId
                || linkedCampaignIds.Contains(campaign.CampaignId))
            |> Seq.groupBy (fun campaign -> campaign.CampaignId)
            |> Seq.map (fun (_, xs) -> xs |> Seq.head)
            |> Seq.sortBy (fun campaign ->
                match orderMap.TryGetValue(campaign.CampaignId) with
                | true, index -> index
                | _ -> Int32.MaxValue)
            |> ResizeArray

        let campaignPlans =
            orderedCampaigns
            |> Seq.map (buildCampaignDeploymentPlan imageBinding)
            |> ResizeArray

        let queueNamespace, localQueue, clusterQueue =
            match Seq.tryHead campaignPlans with
            | Some plan -> plan.QueueNamespace, plan.LocalQueue, plan.ClusterQueue
            | None ->
                let fallback =
                    Templates.benchmark imageBinding (Defaults.benchmarkRequest "/datasets/manifest.json")
                    |> Rendering.renderSummary
                queueOf fallback

        let nextActions =
            program.Summary
            |> tryGetObj "next_actions"
            |> Option.map asStringArray
            |> Option.defaultValue (ResizeArray())

        let leadingCampaignId =
            program.Summary
            |> tryGetObj "leading_campaign_id"
            |> Option.bind asString

        { DeploymentPlanId = $"program-{program.ProgramId}-k8s"
          GeneratedAt = utcNow ()
          ProgramId = program.ProgramId
          ProgramName = program.Name
          ProgramStatus = program.Status
          Objective = program.Objective
          Hypothesis = program.Hypothesis
          LeadingCampaignId = leadingCampaignId
          QueueNamespace = queueNamespace
          LocalQueue = localQueue
          ClusterQueue = clusterQueue
          CampaignCount = campaignPlans.Count
          LaunchReadyItems = campaignPlans |> Seq.sumBy (fun plan -> plan.LaunchReadyItems)
          BlockedItems = campaignPlans |> Seq.sumBy (fun plan -> plan.BlockedItems)
          CampaignPlans = campaignPlans
          NextActions = nextActions }

module Serialization =
    let private dict (pairs: seq<string * obj>) =
        let target = Dictionary<string, obj>()
        for (key, value) in pairs do
            target[key] <- value
        target

    let renderedJob (job: RenderedK8sJobSummary) =
        dict
            [ "kind", box job.Kind
              "job_kind", box job.JobKind
              "entrypoint", box job.EntryPoint
              "args", box job.Args
              "k8s_namespace", box job.K8sNamespace
              "local_queue", box job.LocalQueue
              "cluster_queue", box (defaultArg job.ClusterQueue "")
              "shared_dataset_path", box job.SharedDatasetPath
              "artifact_prefix", box job.ArtifactPrefix
              "api_image", box job.ApiImage
              "worker_image", box job.WorkerImage
              "dataset_manifest_path", box (defaultArg job.DatasetManifestPath "")
              "external_test_manifest_path", box (defaultArg job.ExternalTestManifestPath "")
              "train_split", box (defaultArg job.TrainSplit "")
              "test_split", box (defaultArg job.TestSplit "")
              "seed", box (defaultArg job.Seed -1)
              "kernel_path", box (defaultArg job.KernelPath "")
              "require_snio", box (defaultArg job.RequireSnio false) ]

    let deploymentItem (item: ClusterDeploymentPlanItem) =
        dict
            [ "item_id", box item.ItemId
              "label", box item.Label
              "source_kind", box item.SourceKind
              "source_id", box item.SourceId
              "current_status", box item.CurrentStatus
              "launch_ready", box item.LaunchReady
              "acceptance_status", box (defaultArg item.AcceptanceStatus "")
              "campaign_id", box item.CampaignId
              "program_id", box (defaultArg item.ProgramId "")
              "notes", box item.Notes
              "rendered_job", box (renderedJob item.RenderedJob) ]

    let campaignPlan (plan: CampaignClusterDeploymentPlan) =
        dict
            [ "deployment_plan_id", box plan.DeploymentPlanId
              "generated_at", box plan.GeneratedAt
              "campaign_id", box plan.CampaignId
              "campaign_name", box plan.CampaignName
              "campaign_status", box plan.CampaignStatus
              "program_id", box (defaultArg plan.ProgramId "")
              "objective", box plan.Objective
              "queue_namespace", box plan.QueueNamespace
              "local_queue", box plan.LocalQueue
              "cluster_queue", box (defaultArg plan.ClusterQueue "")
              "total_items", box plan.TotalItems
              "launch_ready_items", box plan.LaunchReadyItems
              "blocked_items", box plan.BlockedItems
              "active_items", box (ResizeArray(plan.ActiveItems |> Seq.map deploymentItem))
              "recommended_items", box (ResizeArray(plan.RecommendedItems |> Seq.map deploymentItem))
              "next_actions", box plan.NextActions ]

    let programPlan (plan: ProgramClusterDeploymentPlan) =
        dict
            [ "deployment_plan_id", box plan.DeploymentPlanId
              "generated_at", box plan.GeneratedAt
              "program_id", box plan.ProgramId
              "program_name", box plan.ProgramName
              "program_status", box plan.ProgramStatus
              "objective", box plan.Objective
              "hypothesis", box plan.Hypothesis
              "leading_campaign_id", box (defaultArg plan.LeadingCampaignId "")
              "queue_namespace", box plan.QueueNamespace
              "local_queue", box plan.LocalQueue
              "cluster_queue", box (defaultArg plan.ClusterQueue "")
              "campaign_count", box plan.CampaignCount
              "launch_ready_items", box plan.LaunchReadyItems
              "blocked_items", box plan.BlockedItems
              "campaign_plans", box (ResizeArray(plan.CampaignPlans |> Seq.map campaignPlan))
              "next_actions", box plan.NextActions ]

    let launchReceiptItem (item: ClusterLaunchReceiptItem) =
        dict
            [ "item_id", box item.ItemId
              "label", box item.Label
              "source_kind", box item.SourceKind
              "source_id", box item.SourceId
              "campaign_id", box item.CampaignId
              "program_id", box (defaultArg item.ProgramId "")
              "dispatch_job_id", box item.DispatchJobId
              "target_job_id", box (defaultArg item.TargetJobId "")
              "dispatch_status", box item.DispatchStatus
              "queue_name", box item.QueueName
              "rendered_job", box (renderedJob item.RenderedJob) ]

    let launchReceipt (receipt: ClusterLaunchReceipt) =
        dict
            [ "launch_id", box receipt.LaunchId
              "created_at", box receipt.CreatedAt
              "scope_kind", box receipt.ScopeKind
              "scope_id", box receipt.ScopeId
              "item_count", box receipt.ItemCount
              "skipped_count", box receipt.SkippedCount
              "items", box (ResizeArray(receipt.Items |> Seq.map launchReceiptItem))
              "notes", box receipt.Notes ]

module Launch =
    open Values

    let private dict (pairs: seq<string * obj>) =
        let target = Dictionary<string, obj>()
        for (key, value) in pairs do
            target[key] <- value
        target

    let defaultRequest =
        { IncludeActiveItems = true
          IncludeRecommendedItems = true
          OnlyLaunchReady = true }

    let private queueNameOf (item: ClusterDeploymentPlanItem) =
        $"k8s:{item.RenderedJob.K8sNamespace}/{item.RenderedJob.LocalQueue}"

    let private submitTargetJob (store: IResearchStore) (item: ClusterDeploymentPlanItem) =
        let now = utcNow ()

        match item.RenderedJob.JobKind with
        | "benchmark" ->
            match item.RenderedJob.DatasetManifestPath, item.RenderedJob.TrainSplit, item.RenderedJob.TestSplit, item.RenderedJob.Seed with
            | Some manifestPath, Some trainSplit, Some testSplit, Some seed ->
                let runId = Guid.NewGuid().ToString("N")
                let jobId = Guid.NewGuid().ToString("N")
                let job =
                    { JobId = jobId
                      CreatedAt = now
                      UpdatedAt = now
                      OwnerType = "benchmark_run"
                      OwnerId = runId
                      Kind = "benchmark_remote"
                      Status = "queued"
                      QueueName = "k8s-benchmark"
                      Executor = "fsharp-cluster-benchmark-worker"
                      RequestPayload =
                        dict
                            [ "dataset_manifest_path", box manifestPath
                              "external_test_manifest_path", box (defaultArg item.RenderedJob.ExternalTestManifestPath "")
                              "train_split", box trainSplit
                              "test_split", box testSplit
                              "seed", box seed ]
                      ResultPayload =
                        dict
                            [ "backend", box "k8s"
                              "artifact_prefix", box $"{runId}/"
                              "cancel_requested", box false ]
                      Error = "" }
                store.SaveJob(job)
                Some job.JobId
            | _ -> None
        | "sounio_runtime" ->
            let executionId = Guid.NewGuid().ToString("N")
            let jobId = Guid.NewGuid().ToString("N")
            let job =
                { JobId = jobId
                  CreatedAt = now
                  UpdatedAt = now
                  OwnerType = "sounio_kernel_run"
                  OwnerId = executionId
                  Kind = "sounio_runtime_remote"
                  Status = "queued"
                  QueueName = "k8s-sounio-runtime"
                  Executor = "fsharp-cluster-sounio-worker"
                  RequestPayload =
                    dict
                        [ "label", box item.Label
                          "kernel_path", box (defaultArg item.RenderedJob.KernelPath "")
                          "persist_artifacts", box true
                          "require_snio", box (defaultArg item.RenderedJob.RequireSnio false) ]
                  ResultPayload =
                    dict
                        [ "backend", box "k8s"
                          "artifact_prefix", box $"{executionId}/"
                          "cancel_requested", box false
                          "snio_attempted", box false
                          "snio_used", box false
                          "execution_id", box executionId ]
                  Error = "" }
            store.SaveJob(job)
            Some job.JobId
        | _ -> None

    let private dispatchJob (store: IResearchStore) scopeKind scopeId (item: ClusterDeploymentPlanItem) =
        let now = utcNow ()
        let jobId = Guid.NewGuid().ToString("N")
        let queueName = queueNameOf item
        let targetJobId = submitTargetJob store item
        let requestPayload =
            dict
                [ "scope_kind", box scopeKind
                  "scope_id", box scopeId
                  "source_kind", box item.SourceKind
                  "source_id", box item.SourceId
                  "campaign_id", box item.CampaignId
                  "program_id", box (defaultArg item.ProgramId "")
                  "label", box item.Label
                  "launch_ready", box item.LaunchReady
                  "target_job_id", box (defaultArg targetJobId "")
                  "target_job_kind", box item.RenderedJob.JobKind
                  "rendered_job", box (Serialization.renderedJob item.RenderedJob) ]

        let resultPayload =
            dict
                [ "backend", box "k8s-render-only"
                  "dispatch_status", box "rendered"
                  "k8s_namespace", box item.RenderedJob.K8sNamespace
                  "local_queue", box item.RenderedJob.LocalQueue
                  "cluster_queue", box (defaultArg item.RenderedJob.ClusterQueue "")
                  "job_kind", box item.RenderedJob.JobKind ]

        let job =
            { JobId = jobId
              CreatedAt = now
              UpdatedAt = now
              OwnerType = "cluster_deployment_item"
              OwnerId = item.ItemId
              Kind = "k8s_dispatch"
              Status = "queued"
              QueueName = queueName
              Executor = "fsharp-k8s-dispatcher"
              RequestPayload = requestPayload
              ResultPayload = resultPayload
              Error = "" }

        store.SaveJob(job)
        { ItemId = item.ItemId
          Label = item.Label
          SourceKind = item.SourceKind
          SourceId = item.SourceId
          CampaignId = item.CampaignId
          ProgramId = item.ProgramId
          DispatchJobId = jobId
          TargetJobId = targetJobId
          DispatchStatus = "queued"
          QueueName = queueName
          RenderedJob = item.RenderedJob }

    let private shouldLaunch request (item: ClusterDeploymentPlanItem) =
        (not request.OnlyLaunchReady || item.LaunchReady)

    let private isSharedPath (sharedRoot: string) (rawPath: string) =
        let normalizedRoot = sharedRoot.TrimEnd('/')
        let normalizedPath = rawPath.Trim()
        normalizedPath = normalizedRoot || normalizedPath.StartsWith($"{normalizedRoot}/", StringComparison.Ordinal)

    let private validationErrors (item: ClusterDeploymentPlanItem) =
        let errors = ResizeArray<string>()
        let sharedRoot = item.RenderedJob.SharedDatasetPath

        match item.RenderedJob.DatasetManifestPath with
        | Some path when not (String.IsNullOrWhiteSpace path) && not (isSharedPath sharedRoot path) ->
            errors.Add(
                $"dataset_manifest_path {path} is not cluster-accessible; expected it under {sharedRoot}."
            )
        | _ -> ()

        match item.RenderedJob.ExternalTestManifestPath with
        | Some path when not (String.IsNullOrWhiteSpace path) && not (isSharedPath sharedRoot path) ->
            errors.Add(
                $"external_test_manifest_path {path} is not cluster-accessible; expected it under {sharedRoot}."
            )
        | _ -> ()

        match item.RenderedJob.KernelPath with
        | Some path when not (String.IsNullOrWhiteSpace path) && not (isSharedPath sharedRoot path) && not (path.StartsWith("/app/", StringComparison.Ordinal)) ->
            errors.Add(
                $"kernel_path {path} is not cluster-accessible; expected it under {sharedRoot} or bundled under /app/."
            )
        | _ -> ()

        errors

    let launchStandaloneItem
        (store: IResearchStore)
        scopeKind
        scopeId
        sourceKind
        sourceId
        label
        campaignId
        programId
        (renderedJob: RenderedK8sJobSummary)
        =
        let item =
            { ItemId = Guid.NewGuid().ToString("N")
              Label = label
              SourceKind = sourceKind
              SourceId = sourceId
              CurrentStatus = "ready"
              LaunchReady = true
              AcceptanceStatus = None
              CampaignId = campaignId
              ProgramId = programId
              RenderedJob = renderedJob
              Notes = ResizeArray() }

        let errors = validationErrors item
        if errors.Count = 0 then
            Ok(dispatchJob store scopeKind scopeId item)
        else
            Error(ResizeArray(errors))

    let launchCampaignPlan (store: IResearchStore) (plan: CampaignClusterDeploymentPlan) (request: ClusterLaunchRequest) =
        let launchId = Guid.NewGuid().ToString("N")
        let selected = ResizeArray<ClusterDeploymentPlanItem>()

        if request.IncludeActiveItems then
            for item in plan.ActiveItems do
                if shouldLaunch request item then
                    selected.Add(item)

        if request.IncludeRecommendedItems then
            for item in plan.RecommendedItems do
                if shouldLaunch request item then
                    selected.Add(item)

        let receipts = ResizeArray<ClusterLaunchReceiptItem>()
        let notes =
            ResizeArray(
                [ $"campaign={plan.CampaignName}"
                  $"queue={plan.QueueNamespace}/{plan.LocalQueue}"
                  "dispatch_mode=queued-kubectl" ]
            )
        let mutable invalidCount = 0

        for item in selected do
            let errors = validationErrors item
            if errors.Count = 0 then
                receipts.Add(dispatchJob store "campaign" plan.CampaignId item)
            else
                invalidCount <- invalidCount + 1
                for error in errors do
                    notes.Add($"skipped:{item.ItemId}:{error}")

        { LaunchId = launchId
          CreatedAt = utcNow ()
          ScopeKind = "campaign"
          ScopeId = plan.CampaignId
          ItemCount = receipts.Count
          SkippedCount = (plan.TotalItems - selected.Count) + invalidCount
          Items = receipts
          Notes = notes }

    let launchProgramPlan (store: IResearchStore) (plan: ProgramClusterDeploymentPlan) (request: ClusterLaunchRequest) =
        let launchId = Guid.NewGuid().ToString("N")
        let selected = ResizeArray<ClusterDeploymentPlanItem>()

        for campaignPlan in plan.CampaignPlans do
            if request.IncludeActiveItems then
                for item in campaignPlan.ActiveItems do
                    if shouldLaunch request item then
                        selected.Add(item)

            if request.IncludeRecommendedItems then
                for item in campaignPlan.RecommendedItems do
                    if shouldLaunch request item then
                        selected.Add(item)

        let totalItems =
            plan.CampaignPlans
            |> Seq.sumBy (fun campaignPlan -> campaignPlan.TotalItems)

        let receipts = ResizeArray<ClusterLaunchReceiptItem>()
        let notes =
            ResizeArray(
                [ $"program={plan.ProgramName}"
                  $"queue={plan.QueueNamespace}/{plan.LocalQueue}"
                  "dispatch_mode=queued-kubectl" ]
            )
        let mutable invalidCount = 0

        for item in selected do
            let errors = validationErrors item
            if errors.Count = 0 then
                receipts.Add(dispatchJob store "program" plan.ProgramId item)
            else
                invalidCount <- invalidCount + 1
                for error in errors do
                    notes.Add($"skipped:{item.ItemId}:{error}")

        { LaunchId = launchId
          CreatedAt = utcNow ()
          ScopeKind = "program"
          ScopeId = plan.ProgramId
          ItemCount = receipts.Count
          SkippedCount = (totalItems - selected.Count) + invalidCount
          Items = receipts
          Notes = notes }

module Dispatch =
    open Values

    type private KubectlResult =
        { ExitCode: int
          Stdout: string
          Stderr: string }

    let private dict (pairs: seq<string * obj>) =
        let target = Dictionary<string, obj>()
        for (key, value) in pairs do
            target[key] <- value
        target

    let private kubectlPath () =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_KUBECTL_PATH") with
        | null | "" -> "kubectl"
        | value -> value

    let private datasetPvc () =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_DATASET_PVC") with
        | null | "" -> None
        | value -> Some value

    let private clusterDbUrl () =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_DB_URL") with
        | null | "" -> None
        | value -> Some value

    let private clusterObjectStore () =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_OBJECT_STORE") with
        | null | "" -> None
        | value -> Some value

    let private clusterObjectRoot () =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_OBJECT_ROOT") with
        | null | "" -> None
        | value -> Some value

    let private clusterSounioRoot () =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_SOUNIO_ROOT") with
        | null | "" -> None
        | value -> Some value

    let private clusterSounioStdlibPath () =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_SOUNIO_STDLIB_PATH") with
        | null | "" -> None
        | value -> Some value

    let private clusterSounioRuntimeLibPath () =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_SOUNIO_RUNTIME_LIB_PATH") with
        | null
        | "" ->
            match clusterSounioRoot () with
            | Some root -> Some(Path.Combine(root, "runtime", "target", "release", "libsounio_runtime.so"))
            | None -> None
        | value -> Some value

    let private runKubectl args stdinText =
        let psi = ProcessStartInfo()
        psi.FileName <- kubectlPath ()
        psi.Arguments <- args
        psi.RedirectStandardInput <- true
        psi.RedirectStandardOutput <- true
        psi.RedirectStandardError <- true
        psi.UseShellExecute <- false
        psi.CreateNoWindow <- true

        use proc = new Process()
        proc.StartInfo <- psi

        if not (proc.Start()) then
            failwith "Failed to start kubectl process."

        if not (String.IsNullOrWhiteSpace stdinText) then
            proc.StandardInput.Write(stdinText)
        proc.StandardInput.Close()

        let stdout = proc.StandardOutput.ReadToEnd()
        let stderr = proc.StandardError.ReadToEnd()
        proc.WaitForExit()

        { ExitCode = proc.ExitCode
          Stdout = stdout
          Stderr = stderr }

    let private renderedJobFromJob (job: JobRecord) =
        let renderedDictionary =
            match job.RequestPayload.TryGetValue("rendered_job") with
            | true, value ->
                match asDictionary value with
                | Some dictionary -> dictionary :> IDictionary<string, obj>
                | None -> failwith $"Job {job.JobId} is missing a usable rendered_job payload."
            | _ -> failwith $"Job {job.JobId} is missing rendered_job metadata."

        { Kind = renderedDictionary |> tryGetObj "kind" |> Option.bind asString |> Option.defaultValue "Job"
          JobKind = renderedDictionary |> tryGetObj "job_kind" |> Option.bind asString |> Option.defaultValue "benchmark"
          EntryPoint = renderedDictionary |> tryGetObj "entrypoint" |> Option.bind asString |> Option.defaultValue (defaultEntryPointForImage (renderedDictionary |> tryGetObj "worker_image" |> Option.bind asString |> Option.defaultValue ""))
          Args = renderedDictionary |> tryGetObj "args" |> Option.map asStringArray |> Option.defaultValue (ResizeArray())
          K8sNamespace = renderedDictionary |> tryGetObj "k8s_namespace" |> Option.bind asString |> Option.defaultValue "default"
          LocalQueue = renderedDictionary |> tryGetObj "local_queue" |> Option.bind asString |> Option.defaultValue "default"
          ClusterQueue = renderedDictionary |> tryGetObj "cluster_queue" |> Option.bind asString
          SharedDatasetPath = renderedDictionary |> tryGetObj "shared_dataset_path" |> Option.bind asString |> Option.defaultValue "/datasets"
          ArtifactPrefix = renderedDictionary |> tryGetObj "artifact_prefix" |> Option.bind asString |> Option.defaultValue "benchmark/"
          ApiImage = renderedDictionary |> tryGetObj "api_image" |> Option.bind asString |> Option.defaultValue ""
          WorkerImage = renderedDictionary |> tryGetObj "worker_image" |> Option.bind asString |> Option.defaultValue ""
          DatasetManifestPath = renderedDictionary |> tryGetObj "dataset_manifest_path" |> Option.bind asString
          ExternalTestManifestPath = renderedDictionary |> tryGetObj "external_test_manifest_path" |> Option.bind asString
          TrainSplit = renderedDictionary |> tryGetObj "train_split" |> Option.bind asString
          TestSplit = renderedDictionary |> tryGetObj "test_split" |> Option.bind asString
          Seed = renderedDictionary |> tryGetObj "seed" |> Option.bind asInt
          KernelPath = renderedDictionary |> tryGetObj "kernel_path" |> Option.bind asString
          RequireSnio = renderedDictionary |> tryGetObj "require_snio" |> Option.bind asBool }

    let private clusterJobName (job: JobRecord) (rendered: RenderedK8sJobSummary) =
        let suffix =
            if job.JobId.Length > 12 then job.JobId.Substring(0, 12) else job.JobId
        let kindStem =
            rendered.JobKind.Replace("_", "-").ToLowerInvariant()
        $"darwin-{kindStem}-{suffix}"

    let private containerName (rendered: RenderedK8sJobSummary) =
        rendered.JobKind.Replace("_", "-").ToLowerInvariant()

    let private containerArgs (job: JobRecord) (rendered: RenderedK8sJobSummary) =
        let targetJobId =
            match job.RequestPayload.TryGetValue("target_job_id") with
            | true, value -> asString value
            | _ -> None

        match targetJobId, rendered.JobKind, isLegacyPythonWorkerImage rendered.WorkerImage with
        | Some jobId, "benchmark", false -> ResizeArray([ "run-benchmark-job"; "--job-id"; jobId ])
        | Some jobId, "sounio_runtime", false -> ResizeArray([ "run-sounio-runtime-job"; "--job-id"; jobId ])
        | _ -> ResizeArray(rendered.Args)

    let private manifestJson (job: JobRecord) (jobName: string) (rendered: RenderedK8sJobSummary) =
        let labels =
            dict
                [ "app.kubernetes.io/name", box "darwin-research-os"
                  "app.kubernetes.io/component", box rendered.JobKind
                  "kueue.x-k8s.io/queue-name", box rendered.LocalQueue ]

        let envVars = ResizeArray<Dictionary<string, obj>>()
        match clusterDbUrl () with
        | Some value -> envVars.Add(dict [ "name", box "DARWIN_RESEARCH_OS_DB_URL"; "value", box value ])
        | None -> ()
        match clusterObjectStore () with
        | Some value -> envVars.Add(dict [ "name", box "DARWIN_RESEARCH_OS_OBJECT_STORE"; "value", box value ])
        | None -> ()
        match clusterObjectRoot () with
        | Some value -> envVars.Add(dict [ "name", box "DARWIN_RESEARCH_OS_OBJECT_ROOT"; "value", box value ])
        | None -> ()
        match clusterSounioRoot () with
        | Some value -> envVars.Add(dict [ "name", box "SOUNIO_ROOT"; "value", box value ])
        | None -> ()
        match clusterSounioStdlibPath () with
        | Some value -> envVars.Add(dict [ "name", box "SOUNIO_STDLIB_PATH"; "value", box value ])
        | None -> ()
        match clusterSounioRuntimeLibPath () with
        | Some value -> envVars.Add(dict [ "name", box "SOUNIO_RUNTIME_LIB_PATH"; "value", box value ])
        | None -> ()

        let container =
            dict
                [ "name", box (containerName rendered)
                  "image", box rendered.WorkerImage
                  "command", box (ResizeArray([ rendered.EntryPoint ]))
                  "args", box (containerArgs job rendered)
                  "env", box envVars ]

        let podSpec =
            dict
                [ "restartPolicy", box "Never"
                  "containers", box (ResizeArray([ container ])) ]

        match datasetPvc () with
        | Some pvcName ->
            let mounts =
                ResizeArray(
                    [ dict
                        [ "name", box "datasets"
                          "mountPath", box rendered.SharedDatasetPath ] ]
                )
            let mountedContainer = Dictionary<string, obj>(container)
            mountedContainer["volumeMounts"] <- box mounts
            podSpec["containers"] <- box (ResizeArray([ mountedContainer ]))
            podSpec["volumes"] <-
                box (
                    ResizeArray(
                        [ dict
                            [ "name", box "datasets"
                              "persistentVolumeClaim", box (dict [ "claimName", box pvcName ]) ] ]
                    )
                )
        | None -> ()

        dict
            [ "apiVersion", box "batch/v1"
              "kind", box "Job"
              "metadata",
              box (
                  dict
                      [ "name", box jobName
                        "namespace", box rendered.K8sNamespace
                        "labels", box labels ]
              )
              "spec",
              box (
                  dict
                      [ "backoffLimit", box 0
                        "template",
                        box (
                            dict
                                [ "metadata", box (dict [ "labels", box labels ])
                                  "spec", box podSpec ]
                        ) ]
              ) ]
        |> fun payload -> JsonSerializer.Serialize(payload, JsonSerializerOptions(WriteIndented = true))

    let private parseStatus namespaceName localQueue clusterQueue jobName stdout stderr =
        let diagnostics = ResizeArray<string>()
        if not (String.IsNullOrWhiteSpace stdout) then diagnostics.Add(stdout.Trim())
        if not (String.IsNullOrWhiteSpace stderr) then diagnostics.Add(stderr.Trim())

        let mutable status = "running"
        let mutable clusterPhase = "submitted"
        let mutable admitted = None
        let mutable finished = None

        if not (String.IsNullOrWhiteSpace stdout) then
            use document = JsonDocument.Parse(stdout)
            let root = document.RootElement

            let suspended =
                match root.TryGetProperty("spec") with
                | true, spec ->
                    match spec.TryGetProperty("suspend") with
                    | true, value -> Some (value.GetBoolean())
                    | _ -> None
                | _ -> None

            match root.TryGetProperty("status") with
            | true, statusElement ->
                let mutable foundTerminal = false
                match statusElement.TryGetProperty("conditions") with
                | true, conditions when conditions.ValueKind = JsonValueKind.Array ->
                    for condition in conditions.EnumerateArray() do
                        let conditionType =
                            match condition.TryGetProperty("type") with
                            | true, value -> value.GetString()
                            | _ -> null
                        let conditionStatus =
                            match condition.TryGetProperty("status") with
                            | true, value -> value.GetString()
                            | _ -> null

                        if conditionType = "Complete" && conditionStatus = "True" then
                            status <- "completed"
                            clusterPhase <- "finished"
                            finished <- Some true
                            admitted <- Some true
                            foundTerminal <- true
                        elif conditionType = "Failed" && conditionStatus = "True" then
                            status <- "failed"
                            clusterPhase <- "failed"
                            finished <- Some false
                            admitted <- admitted |> Option.orElse (Some true)
                            foundTerminal <- true
                | _ -> ()

                if not foundTerminal then
                    match suspended with
                    | Some true ->
                        status <- "running"
                        clusterPhase <- "queued"
                    | _ ->
                        status <- "running"
                        clusterPhase <- "running"
            | _ -> ()

        { ClusterJobName = Some jobName
          Namespace = namespaceName
          LocalQueue = localQueue
          ClusterQueue = clusterQueue
          Status = status
          ClusterPhase = clusterPhase
          Submitted = true
          Cancelled = false
          KueueAdmitted = admitted
          KueueFinished = finished
          Diagnostics = diagnostics }

    let private prependDiagnostics diagnostics (result: K8sDispatchResult) =
        let merged = ResizeArray<string>()
        for message in diagnostics do
            if not (String.IsNullOrWhiteSpace message) then
                merged.Add(message)
        for message in result.Diagnostics do
            if not (String.IsNullOrWhiteSpace message) then
                merged.Add(message)
        { result with Diagnostics = merged }

    let private enrichFailureDiagnostics namespaceName jobName (result: K8sDispatchResult) =
        if result.Status <> "failed" then
            result
        else
            let logsResult = runKubectl $"logs job/{jobName} -n {namespaceName} --all-containers=true --tail=200" ""
            prependDiagnostics (ResizeArray([ logsResult.Stdout.Trim(); logsResult.Stderr.Trim() ])) result

    type KubectlK8sDispatchRunner() =
        interface IK8sDispatchRunner with
            member _.Submit(job) =
                let rendered = renderedJobFromJob job
                let jobName = clusterJobName job rendered
                let manifest = manifestJson job jobName rendered
                let applyResult = runKubectl $"apply --validate=false -f -" manifest
                if applyResult.ExitCode <> 0 then
                    { ClusterJobName = Some jobName
                      Namespace = rendered.K8sNamespace
                      LocalQueue = rendered.LocalQueue
                      ClusterQueue = rendered.ClusterQueue
                      Status = "failed"
                      ClusterPhase = "submit_failed"
                      Submitted = false
                      Cancelled = false
                      KueueAdmitted = None
                      KueueFinished = None
                      Diagnostics = ResizeArray([ applyResult.Stderr.Trim(); applyResult.Stdout.Trim() ]) }
                else
                    let getResult = runKubectl $"get job {jobName} -n {rendered.K8sNamespace} -o json" ""
                    if getResult.ExitCode <> 0 then
                        { ClusterJobName = Some jobName
                          Namespace = rendered.K8sNamespace
                          LocalQueue = rendered.LocalQueue
                          ClusterQueue = rendered.ClusterQueue
                          Status = "failed"
                          ClusterPhase = "submit_failed"
                          Submitted = true
                          Cancelled = false
                          KueueAdmitted = None
                          KueueFinished = None
                          Diagnostics =
                            ResizeArray(
                                [ applyResult.Stdout.Trim()
                                  applyResult.Stderr.Trim()
                                  getResult.Stdout.Trim()
                                  getResult.Stderr.Trim() ]
                            ) }
                    else
                        parseStatus rendered.K8sNamespace rendered.LocalQueue rendered.ClusterQueue jobName getResult.Stdout getResult.Stderr
                        |> prependDiagnostics (ResizeArray([ applyResult.Stdout.Trim(); applyResult.Stderr.Trim() ]))
                        |> enrichFailureDiagnostics rendered.K8sNamespace jobName

            member _.Poll(job) =
                let rendered = renderedJobFromJob job
                let jobName =
                    match job.ResultPayload.TryGetValue("cluster_job_name") with
                    | true, value -> asString value |> Option.defaultValue (clusterJobName job rendered)
                    | _ -> clusterJobName job rendered
                let result = runKubectl $"get job {jobName} -n {rendered.K8sNamespace} -o json" ""
                if result.ExitCode <> 0 then
                    { ClusterJobName = Some jobName
                      Namespace = rendered.K8sNamespace
                      LocalQueue = rendered.LocalQueue
                      ClusterQueue = rendered.ClusterQueue
                      Status = "failed"
                      ClusterPhase = "missing"
                      Submitted = true
                      Cancelled = false
                      KueueAdmitted = None
                      KueueFinished = None
                      Diagnostics = ResizeArray([ result.Stderr.Trim(); result.Stdout.Trim() ]) }
                else
                    parseStatus rendered.K8sNamespace rendered.LocalQueue rendered.ClusterQueue jobName result.Stdout result.Stderr
                    |> enrichFailureDiagnostics rendered.K8sNamespace jobName

            member _.Cancel(job) =
                let rendered = renderedJobFromJob job
                let jobName =
                    match job.ResultPayload.TryGetValue("cluster_job_name") with
                    | true, value -> asString value |> Option.defaultValue (clusterJobName job rendered)
                    | _ -> clusterJobName job rendered
                let result = runKubectl $"delete job {jobName} -n {rendered.K8sNamespace} --ignore-not-found=true --wait=false" ""
                let diagnostics = ResizeArray<string>()
                if not (String.IsNullOrWhiteSpace result.Stdout) then diagnostics.Add(result.Stdout.Trim())
                if not (String.IsNullOrWhiteSpace result.Stderr) then diagnostics.Add(result.Stderr.Trim())
                { ClusterJobName = Some jobName
                  Namespace = rendered.K8sNamespace
                  LocalQueue = rendered.LocalQueue
                  ClusterQueue = rendered.ClusterQueue
                  Status = "cancelled"
                  ClusterPhase = "cancelled"
                  Submitted = true
                  Cancelled = true
                  KueueAdmitted = None
                  KueueFinished = Some false
                  Diagnostics = diagnostics }
