namespace Darwin.ResearchOs.Api

open System
open System.IO
open System.Collections.Generic
open Darwin.ResearchOs.Contracts
open Darwin.ResearchOs.Core
open Darwin.ResearchOs.K8s
open Darwin.ResearchOs.Storage
open Microsoft.AspNetCore.Builder
open Microsoft.AspNetCore.Http
open Microsoft.AspNetCore.Http.Json
open Microsoft.Extensions.DependencyInjection
open Microsoft.Extensions.Hosting

module Pathing =
    let contractsRoot () =
        let configured: string = Environment.GetEnvironmentVariable("DARWIN_CONTRACTS_ROOT")
        if String.IsNullOrWhiteSpace(configured) then
            Path.Combine(RuntimePathing.repoRoot (), "contracts")
        else
            Path.GetFullPath(configured)

module Program =
    [<EntryPoint>]
    let main args =
        let builder = WebApplication.CreateBuilder(args)
        builder.Services.Configure<JsonOptions>(fun (options: JsonOptions) -> options.SerializerOptions.WriteIndented <- true) |> ignore

        let app = builder.Build()
        let contractsRoot = Pathing.contractsRoot ()
        let registryPath = Path.Combine(contractsRoot, "registry", "comparison_registry.json")
        let openApiPath = Path.Combine(contractsRoot, "openapi", "openapi.json")
        let services = RuntimeServiceSet.create ()

        let dict (pairs: seq<string * obj>) =
            let target = System.Collections.Generic.Dictionary<string, obj>()
            for (key, value) in pairs do
                target[key] <- value
            target

        let runtimeReportDictionary (report: SounioRuntimeProbeReport) =
            dict
                [ "detected", box report.Detected
                  "usable", box report.Usable
                  "source", box report.Source
                  "sounio_root", box (defaultArg report.SounioRoot "")
                  "native_backend_source_root", box (defaultArg report.NativeBackendSourceRoot "")
                  "snio_embed_header_path", box (defaultArg report.SnioEmbedHeaderPath "")
                  "snio_protocol_path", box (defaultArg report.SnioProtocolPath "")
                  "snio_fsharp_protocol_path", box (defaultArg report.SnioFsharpProtocolPath "")
                  "git_root", box (defaultArg report.GitRoot "")
                  "souc_path", box (defaultArg report.SoucPath "")
                  "runtime_library_path", box (defaultArg report.RuntimeLibraryPath "")
                  "stdlib_path", box (defaultArg report.StdlibPath "")
                  "probe_program_path", box (defaultArg report.ProbeProgramPath "")
                  "version", box (defaultArg report.Version "")
                  "remote_url", box (defaultArg report.RemoteUrl "")
                  "official_remote", box (defaultArg report.OfficialRemote false)
                  "worktree_clean", box (defaultArg report.WorktreeClean false)
                  "binary_version_json_available", box report.BinaryVersionJsonAvailable
                  "source_version_json_available", box report.SourceVersionJsonAvailable
                  "native_ffi_detected", box report.NativeFfiDetected
                  "native_ffi_usable", box report.NativeFfiUsable
                  "native_probe_succeeded", box report.NativeProbeSucceeded
                  "native_kernel_execution_available", box report.NativeKernelExecutionAvailable
                  "snio_embedding_detected", box report.SnioEmbeddingDetected
                  "snio_embedding_usable", box report.SnioEmbeddingUsable
                  "kernel_session_protocol_available", box report.KernelSessionProtocolAvailable
                  "native_probe_output", box (ResizeArray(report.NativeProbeOutput))
                  "runtime_tensor_ffi_symbols", box (ResizeArray(report.RuntimeTensorFfiSymbols))
                  "runtime_uncertain_ffi_symbols", box (ResizeArray(report.RuntimeUncertainFfiSymbols))
                  "runtime_ode_ffi_symbols", box (ResizeArray(report.RuntimeOdeFfiSymbols))
                  "runtime_autodiff_ffi_symbols", box (ResizeArray(report.RuntimeAutodiffFfiSymbols))
                  "native_tensor_ffi_symbols", box (ResizeArray(report.NativeTensorFfiSymbols))
                  "native_uncertain_ffi_symbols", box (ResizeArray(report.NativeUncertainFfiSymbols))
                  "native_ode_ffi_symbols", box (ResizeArray(report.NativeOdeFfiSymbols))
                  "native_autodiff_ffi_symbols", box (ResizeArray(report.NativeAutodiffFfiSymbols))
                  "source_only_scientific_ffi_symbols", box (ResizeArray(report.SourceOnlyScientificFfiSymbols))
                  "check_succeeded", box report.CheckSucceeded
                  "run_succeeded", box report.RunSucceeded
                  "probe_output", box (ResizeArray(report.ProbeOutput))
                  "diagnostics", box (ResizeArray(report.Diagnostics)) ]

        let kernelExecutionDictionary (result: SounioKernelExecutionResult) =
            dict
                [ "execution_id", box result.ExecutionId
                  "job_id", box result.JobId
                  "created_at", box result.CreatedAt
                  "label", box result.Label
                  "kernel_path", box result.KernelPath
                  "status", box result.Status
                  "check_succeeded", box result.CheckSucceeded
                  "run_succeeded", box result.RunSucceeded
                  "snio_attempted", box result.SnioAttempted
                  "snio_used", box result.SnioUsed
                  "output", box (ResizeArray(result.Output))
                  "diagnostics", box (ResizeArray(result.Diagnostics))
                  "artifact_ids", box (ResizeArray(result.ArtifactIds))
                  "runtime", box (runtimeReportDictionary result.Runtime) ]

        let localJobBackend = LocalParityJobBackend(services.ResearchStore)
        let parityBenchmarkRunner = ParityBenchmark.createRunner services.ResearchStore services.ObjectStore

        let imageBinding () =
            let apiImage =
                match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_API_IMAGE") with
                | null | "" -> "ghcr.io/agourakis82/darwin-research-os-api:dev"
                | value -> value

            let workerImage =
                match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_WORKER_IMAGE") with
                | null | "" -> "ghcr.io/agourakis82/darwin-research-os-worker:dev"
                | value -> value

            { ApiImage = apiImage
              WorkerImage = workerImage }

        let benchmarkTemplateSummary (request: BenchmarkRequest) : obj =
            Templates.benchmark (imageBinding ()) request
            |> Rendering.renderSummary
            |> box

        let sounioTemplateSummary (request: SounioKernelExecutionRequest) : obj =
            Templates.sounioRuntime (imageBinding ()) request.KernelPath request.RequireSnio
            |> Rendering.renderSounioRuntimeSummary
            |> box

        let normalizeSounioRequest (request: SounioKernelExecutionRequest) =
            { Label =
                if String.IsNullOrWhiteSpace(request.Label) then
                    "Sounio Runtime Execution"
                else
                    request.Label
              KernelPath = request.KernelPath
              PersistArtifacts = request.PersistArtifacts
              RequireSnio = request.RequireSnio }

        let launchStandaloneSounioRuntime (request: SounioKernelExecutionRequest) =
            let normalized = normalizeSounioRequest request
            let rendered =
                Templates.sounioRuntime (imageBinding ()) normalized.KernelPath normalized.RequireSnio
                |> Rendering.renderSounioRuntimeSummary

            match
                Launch.launchStandaloneItem
                    services.ResearchStore
                    "sounio_runtime"
                    (Guid.NewGuid().ToString("N"))
                    "sounio_kernel_request"
                    (Guid.NewGuid().ToString("N"))
                    normalized.Label
                    "standalone-sounio-runtime"
                    None
                    rendered
            with
            | Ok receiptItem ->
                let targetJob =
                    receiptItem.TargetJobId
                    |> Option.bind services.ResearchStore.TryGetJob

                Results.Ok(
                    dict
                        [ "request", box normalized
                          "rendered_job", box rendered
                          "launch_item", box receiptItem
                          "target_job_id", box (defaultArg receiptItem.TargetJobId "")
                          "target_job",
                          (match targetJob with
                           | Some job -> box job
                           | None -> null) ])
            | Error errors ->
                Results.BadRequest(
                    dict
                        [ "request", box normalized
                          "rendered_job", box rendered
                          "errors", box errors ])

        let tryRenderJobSummary (job: JobRecord) =
            match job.Kind with
            | "benchmark" ->
                let request =
                    { DatasetManifestPath = unbox<string> job.RequestPayload["dataset_manifest_path"]
                      ExternalTestManifestPath =
                          match job.RequestPayload.TryGetValue("external_test_manifest_path") with
                          | true, (:? string as value) when not (String.IsNullOrWhiteSpace value) -> Some value
                          | _ -> None
                      TrainSplit = unbox<string> job.RequestPayload["train_split"]
                      TestSplit = unbox<string> job.RequestPayload["test_split"]
                      Seed =
                          match job.RequestPayload["seed"] with
                          | :? int as value -> value
                          | :? int64 as value -> int value
                          | _ -> 13 }
                Some(benchmarkTemplateSummary request)
            | "sounio_runtime" ->
                let request =
                    { Label =
                        match job.RequestPayload.TryGetValue("label") with
                        | true, (:? string as value) when not (String.IsNullOrWhiteSpace value) -> value
                        | _ -> "Sounio Runtime Execution"
                      KernelPath =
                        match job.RequestPayload.TryGetValue("kernel_path") with
                        | true, (:? string as value) when not (String.IsNullOrWhiteSpace value) -> Some value
                        | _ -> None
                      PersistArtifacts =
                        match job.RequestPayload.TryGetValue("persist_artifacts") with
                        | true, (:? bool as value) -> value
                        | _ -> true
                      RequireSnio =
                        match job.RequestPayload.TryGetValue("require_snio") with
                        | true, (:? bool as value) -> value
                        | _ -> false }
                Some(sounioTemplateSummary request)
            | "k8s_dispatch" ->
                match job.RequestPayload.TryGetValue("rendered_job") with
                | true, (:? Dictionary<string, obj> as rendered) -> Some(box rendered)
                | true, (:? System.Collections.Generic.IDictionary<string, obj> as rendered) ->
                    let copy = Dictionary<string, obj>()
                    for KeyValue(key, value) in rendered do
                        copy[key] <- value
                    Some(box copy)
                | _ -> None
            | _ -> None

        let persistJsonArtifact ownerType ownerId name relativePath description payload =
            let stored = services.ObjectStore.WriteJson(relativePath, payload)
            let artifact =
                { ArtifactId = Guid.NewGuid().ToString("N")
                  CreatedAt = DateTime.UtcNow.ToString("O")
                  OwnerType = ownerType
                  OwnerId = ownerId
                  Name = name
                  Kind = "json"
                  Path = stored.LocalPath
                  Uri = stored.Uri
                  Description = description
                  ContentType = "application/json"
                  Bytes =
                    match stored.Bytes with
                    | Some value -> Nullable value
                    | None -> Nullable() }
            services.ResearchStore.SaveArtifact(artifact)
            artifact

        let executeBenchmarkRequest (request: BenchmarkRequest) =
            ParityBenchmark.submitAndRunOnce services.ResearchStore services.ObjectStore services.StateMode request

        let tryCancelJob jobId =
            services.ResearchStore.UpdateJob(
                jobId,
                fun current ->
                    let payload = Dictionary<string, obj>(current.ResultPayload)
                    payload["cancel_requested"] <- box true

                    if current.Status = "queued" then
                        { current with
                            Status = "cancelled"
                            UpdatedAt = DateTime.UtcNow.ToString("O")
                            ResultPayload = payload
                            Error = "Cancellation requested before execution started." }
                    else
                        { current with
                            UpdatedAt = DateTime.UtcNow.ToString("O")
                            ResultPayload = payload }
            )

        let reconcileCampaign (campaign: CampaignRecord) =
            let reconciled =
                CampaignSemantics.reconcile
                    { TryGetJob = services.ResearchStore.TryGetJob
                      TryGetBenchmarkRun = services.ResearchStore.TryGetBenchmarkRun }
                    campaign
            services.ResearchStore.SaveCampaign(reconciled)
            reconciled

        let listCampaignsReconciled () =
            services.ResearchStore.ListCampaigns()
            |> Seq.map reconcileCampaign
            |> ResizeArray

        let tryGetCampaignReconciled campaignId =
            services.ResearchStore.TryGetCampaign(campaignId)
            |> Option.map reconcileCampaign

        let campaignDeploymentPlan (campaign: CampaignRecord) =
            DeploymentPlans.buildCampaignDeploymentPlan (imageBinding ()) campaign

        let createCampaignInline (request: CampaignRequest) =
            let entries = ResizeArray<CampaignEntry>()
            for spec in request.BenchmarkSpecs do
                let job, run = executeBenchmarkRequest spec.Request
                entries.Add(
                    { EntryId = Guid.NewGuid().ToString("N")
                      Label = spec.Label
                      Request = spec.Request
                      JobId = Some job.JobId
                      Status = job.Status
                      BenchmarkRunId = Some run.RunId
                      Error = job.Error }
                )

            let campaign =
                { CampaignId = Guid.NewGuid().ToString("N")
                  CreatedAt = DateTime.UtcNow.ToString("O")
                  UpdatedAt = DateTime.UtcNow.ToString("O")
                  Name = request.Name
                  Objective = request.Objective
                  Kind = "benchmark"
                  ProgramId = request.ProgramId
                  Status = "created"
                  BenchmarkSpecs = entries
                  Notes = request.Notes
                  Summary = dict [] }

            reconcileCampaign campaign

        let createCampaignQueued (request: CampaignRequest) =
            let entries = ResizeArray<CampaignEntry>()
            for spec in request.BenchmarkSpecs do
                let job = localJobBackend.SubmitBenchmark(spec.Request)
                entries.Add(
                    { EntryId = Guid.NewGuid().ToString("N")
                      Label = spec.Label
                      Request = spec.Request
                      JobId = Some job.JobId
                      Status = job.Status
                      BenchmarkRunId = None
                      Error = job.Error }
                )

            let campaign =
                { CampaignId = Guid.NewGuid().ToString("N")
                  CreatedAt = DateTime.UtcNow.ToString("O")
                  UpdatedAt = DateTime.UtcNow.ToString("O")
                  Name = request.Name
                  Objective = request.Objective
                  Kind = "benchmark"
                  ProgramId = request.ProgramId
                  Status = "queued"
                  BenchmarkSpecs = entries
                  Notes = request.Notes
                  Summary = dict [] }

            services.ResearchStore.SaveCampaign(campaign)
            reconcileCampaign campaign

        let createProgram (request: ProgramRequest) =
            let program =
                { ProgramId = Guid.NewGuid().ToString("N")
                  CreatedAt = DateTime.UtcNow.ToString("O")
                  UpdatedAt = DateTime.UtcNow.ToString("O")
                  Name = request.Name
                  Objective = request.Objective
                  Hypothesis = request.Hypothesis
                  Notes = request.Notes
                  Status = "created"
                  CampaignIds = ResizeArray(request.CampaignIds)
                  Summary = dict [] }
            let campaigns =
                request.CampaignIds
                |> Seq.choose tryGetCampaignReconciled
                |> Seq.toList
            let reconciled = ProgramSemantics.reconcile campaigns program
            services.ResearchStore.SaveProgram(reconciled)
            reconciled

        let attachCampaignToProgram programId (request: ProgramCampaignAttachRequest) =
            match services.ResearchStore.TryGetProgram(programId), tryGetCampaignReconciled request.CampaignId with
            | Some program, Some campaign ->
                let campaignIds = ResizeArray(program.CampaignIds)
                if not (campaignIds.Contains(request.CampaignId)) then
                    campaignIds.Add(request.CampaignId)
                let updatedProgram =
                    { program with
                        UpdatedAt = DateTime.UtcNow.ToString("O")
                        CampaignIds = campaignIds }
                let updatedCampaign =
                    { campaign with
                        UpdatedAt = DateTime.UtcNow.ToString("O")
                        ProgramId = Some programId }
                services.ResearchStore.SaveCampaign(updatedCampaign)
                let reconciled =
                    ProgramSemantics.reconcile
                        (campaignIds |> Seq.choose tryGetCampaignReconciled |> Seq.toList)
                        updatedProgram
                services.ResearchStore.SaveProgram(reconciled)
                Some reconciled
            | _ -> None

        let reconcilePrograms () =
            let campaigns = listCampaignsReconciled () |> Seq.toList
            services.ResearchStore.ListPrograms()
            |> Seq.map (ProgramSemantics.reconcile campaigns)
            |> ResizeArray

        let tryGetProgramReconciled programId =
            reconcilePrograms ()
            |> Seq.tryFind (fun program -> program.ProgramId = programId)

        let tryGetProgramDeploymentPlan programId =
            match tryGetProgramReconciled programId with
            | None -> None
            | Some program ->
                let campaigns = listCampaignsReconciled () |> Seq.toList
                Some(DeploymentPlans.buildProgramDeploymentPlan (imageBinding ()) program campaigns)

        let materializeCampaignDeploymentPlan (plan: CampaignClusterDeploymentPlan) =
            let relativePath = Path.Combine("rewrite", "k8s-plans", "campaign", plan.CampaignId, "plan.json")
            persistJsonArtifact
                "campaign"
                plan.CampaignId
                "k8s_plan"
                relativePath
                $"K8s deployment plan for campaign {plan.CampaignId}."
                (Serialization.campaignPlan plan)

        let materializeProgramDeploymentPlan (plan: ProgramClusterDeploymentPlan) =
            let relativePath = Path.Combine("rewrite", "k8s-plans", "program", plan.ProgramId, "plan.json")
            persistJsonArtifact
                "program"
                plan.ProgramId
                "k8s_plan"
                relativePath
                $"K8s deployment plan for program {plan.ProgramId}."
                (Serialization.programPlan plan)

        let persistLaunchReceipt ownerType ownerId launchId (receipt: ClusterLaunchReceipt) =
            let relativePath = Path.Combine("rewrite", "k8s-launches", ownerType, ownerId, $"{launchId}.json")
            persistJsonArtifact
                ownerType
                ownerId
                "k8s_launch_receipt"
                relativePath
                $"K8s launch receipt {launchId} for {ownerType} {ownerId}."
                (Serialization.launchReceipt receipt)

        app.MapGet("/", Func<_>(fun () ->
            {| service = "Darwin.ResearchOs.Api"
               lane = "fsharp"
               status = "wave-2-platform-core"
               contracts_root = contractsRoot
               state_mode = services.StateMode
               object_mode = services.ObjectMode |})) |> ignore

        app.MapGet("/health", Func<_>(fun () -> {| status = "ok" |})) |> ignore
        app.MapGet("/readyz", Func<_>(fun () -> {| status = "ready" |})) |> ignore

        app.MapGet("/rewrite/status", Func<_>(fun () -> RewriteStatus.current())) |> ignore
        app.MapGet("/rewrite/sounio-runtime", Func<_>(fun () -> SounioRuntimeProbe.probe())) |> ignore
        app.MapGet("/rewrite/sounio-runtime/abi", Func<_>(fun () -> SounioRuntimeProbe.abiInventory())) |> ignore

        app.MapPost("/rewrite/sounio-runtime/run", Func<SounioKernelExecutionRequest, IResult>(fun request ->
            let requested =
                { Label =
                    if String.IsNullOrWhiteSpace(request.Label) then "Sounio Runtime Execution" else request.Label
                  KernelPath = request.KernelPath
                  PersistArtifacts = request.PersistArtifacts
                  RequireSnio = request.RequireSnio }
            let execution =
                SounioRuntimeProbe.executeKernel requested
                |> SounioRuntimeProbe.persistKernelExecution services.ResearchStore services.ObjectStore services.StateMode "Darwin.ResearchOs.Api" requested
            Results.Ok(execution))) |> ignore

        app.MapGet("/rewrite/contracts", Func<_>(fun () ->
            {| contracts_root = contractsRoot
               openapi_exists = File.Exists(openApiPath)
               comparison_registry_exists = File.Exists(registryPath)
               golden_fixture_count =
                   let goldenRoot = Path.Combine(contractsRoot, "golden")
                   if Directory.Exists(goldenRoot) then Directory.GetFiles(goldenRoot, "*.json").Length else 0 |})) |> ignore

        app.MapGet("/rewrite/storage", Func<_>(fun () ->
            {| state_mode = services.StateMode
               object_mode = services.ObjectMode
               db_configured = services.DbConfigured
               object_root = services.ObjectRoot
               job_count = services.ResearchStore.ListJobs().Count
               benchmark_run_count = services.ResearchStore.ListBenchmarkRuns().Count
               campaign_count = services.ResearchStore.ListCampaigns().Count
               program_count = services.ResearchStore.ListPrograms().Count
               artifact_count = services.ResearchStore.ListArtifacts().Count
               heartbeat_count = services.ResearchStore.ListWorkerHeartbeats(None).Count |})) |> ignore

        app.MapGet("/rewrite/k8s/queues", Func<_>(fun () ->
            let target =
                { Namespace = "darwin-genomics"
                  LocalQueue = "darwin-lab"
                  ClusterQueue = Some "darwin-shared" }
            let scheduler = KubernetesParityScheduler(target) :> IJobScheduler
            {| scheduler = scheduler.Name
               supports_kueue = scheduler.SupportsKueue
               supports_cancellation = scheduler.SupportsCancellation
               k8s_namespace = target.Namespace
               local_queue = target.LocalQueue
               cluster_queue = target.ClusterQueue
               api_image = (imageBinding()).ApiImage
               worker_image = (imageBinding()).WorkerImage |})) |> ignore

        app.MapPost("/rewrite/k8s/render/benchmark", Func<BenchmarkRequest, IResult>(fun request ->
            Results.Ok(benchmarkTemplateSummary request))) |> ignore

        app.MapPost("/rewrite/k8s/render/sounio-runtime", Func<SounioKernelExecutionRequest, IResult>(fun request ->
            let normalized = normalizeSounioRequest request
            Results.Ok(sounioTemplateSummary normalized))) |> ignore

        app.MapGet("/rewrite/comparison-registry", Func<_>(fun () ->
            if File.Exists(registryPath) then
                Results.Content(File.ReadAllText(registryPath), "application/json")
            else
                Results.NotFound(sprintf "Missing comparison registry at %s" registryPath))) |> ignore

        app.MapGet("/rewrite/jobs", Func<_>(fun () -> services.ResearchStore.ListJobs())) |> ignore

        app.MapPost("/rewrite/jobs/benchmark", Func<BenchmarkRequest, IResult>(fun request ->
            let job = localJobBackend.SubmitBenchmark request
            Results.Ok(job))) |> ignore

        app.MapPost("/rewrite/jobs/sounio-runtime", Func<SounioKernelExecutionRequest, IResult>(fun request ->
            let requested = normalizeSounioRequest request
            let job = localJobBackend.SubmitSounioKernel requested
            Results.Ok(job))) |> ignore

        app.MapPost("/rewrite/jobs/sounio-runtime/launch-k8s", Func<SounioKernelExecutionRequest, IResult>(fun request ->
            launchStandaloneSounioRuntime request)) |> ignore

        app.MapGet("/rewrite/jobs/{jobId}", Func<string, IResult>(fun jobId ->
            match services.ResearchStore.TryGetJob(jobId) with
            | Some job -> Results.Ok(job)
            | None -> Results.NotFound(sprintf "Unknown job %s" jobId))) |> ignore

        app.MapPost("/rewrite/jobs/{jobId}/cancel", Func<string, IResult>(fun jobId ->
            match tryCancelJob jobId with
            | Some job -> Results.Ok(job)
            | None -> Results.NotFound(sprintf "Unknown job %s" jobId))) |> ignore

        app.MapGet("/rewrite/jobs/{jobId}/k8s", Func<string, IResult>(fun jobId ->
            match services.ResearchStore.TryGetJob(jobId) with
            | None -> Results.NotFound(sprintf "Unknown job %s" jobId)
            | Some job ->
                match tryRenderJobSummary job with
                | Some rendered -> Results.Ok(rendered)
                | None -> Results.BadRequest(sprintf "Job %s of kind %s does not have a K8s rendering template yet." jobId job.Kind))) |> ignore

        app.MapGet("/rewrite/benchmark-runs", Func<_>(fun () -> services.ResearchStore.ListBenchmarkRuns())) |> ignore

        app.MapPost("/rewrite/benchmark-runs", Func<BenchmarkRequest, IResult>(fun request ->
            let _, run = executeBenchmarkRequest request
            Results.Ok(run))) |> ignore

        app.MapGet("/rewrite/benchmark-runs/{runId}", Func<string, IResult>(fun runId ->
            match services.ResearchStore.TryGetBenchmarkRun(runId) with
            | Some run -> Results.Ok(run)
            | None -> Results.NotFound(sprintf "Unknown benchmark run %s" runId))) |> ignore

        app.MapGet("/rewrite/artifacts", Func<_>(fun () -> services.ResearchStore.ListArtifacts())) |> ignore

        app.MapGet("/rewrite/artifacts/{ownerType}/{ownerId}", Func<string, string, IResult>(fun ownerType ownerId ->
            let artifacts =
                services.ResearchStore.ListArtifacts()
                |> Seq.filter (fun artifact -> artifact.OwnerType = ownerType && artifact.OwnerId = ownerId)
                |> ResizeArray
            Results.Ok(artifacts))) |> ignore

        app.MapGet("/rewrite/campaigns", Func<_>(fun () -> listCampaignsReconciled ())) |> ignore

        app.MapPost("/rewrite/campaigns", Func<CampaignRequest, IResult>(fun request ->
            let campaign = createCampaignInline request
            Results.Ok(campaign))) |> ignore

        app.MapPost("/rewrite/campaigns/queued", Func<CampaignRequest, IResult>(fun request ->
            let campaign = createCampaignQueued request
            Results.Ok(campaign))) |> ignore

        app.MapGet("/rewrite/campaigns/{campaignId}", Func<string, IResult>(fun campaignId ->
            match tryGetCampaignReconciled campaignId with
            | Some campaign -> Results.Ok(campaign)
            | None -> Results.NotFound(sprintf "Unknown campaign %s" campaignId))) |> ignore

        app.MapGet("/rewrite/campaigns/{campaignId}/k8s-plan", Func<string, IResult>(fun campaignId ->
            match tryGetCampaignReconciled campaignId with
            | Some campaign -> Results.Ok(campaignDeploymentPlan campaign)
            | None -> Results.NotFound(sprintf "Unknown campaign %s" campaignId))) |> ignore

        app.MapPost("/rewrite/campaigns/{campaignId}/k8s-plan/materialize", Func<string, IResult>(fun campaignId ->
            match tryGetCampaignReconciled campaignId with
            | Some campaign ->
                let plan = campaignDeploymentPlan campaign
                let artifact = materializeCampaignDeploymentPlan plan
                Results.Ok(dict [ "plan", box plan; "artifact", box artifact ])
            | None -> Results.NotFound(sprintf "Unknown campaign %s" campaignId))) |> ignore

        app.MapPost("/rewrite/campaigns/{campaignId}/k8s-plan/launch", Func<string, IResult>(fun campaignId ->
            match tryGetCampaignReconciled campaignId with
            | Some campaign ->
                let plan = campaignDeploymentPlan campaign
                let planArtifact = materializeCampaignDeploymentPlan plan
                let receipt = Launch.launchCampaignPlan services.ResearchStore plan Launch.defaultRequest
                let receiptArtifact = persistLaunchReceipt "campaign" campaignId receipt.LaunchId receipt
                Results.Ok(
                    dict
                        [ "plan", box plan
                          "plan_artifact", box planArtifact
                          "launch_receipt", box receipt
                          "launch_artifact", box receiptArtifact ])
            | None -> Results.NotFound(sprintf "Unknown campaign %s" campaignId))) |> ignore

        app.MapGet("/rewrite/programs", Func<_>(fun () -> reconcilePrograms())) |> ignore

        app.MapPost("/rewrite/programs", Func<ProgramRequest, IResult>(fun request ->
            let program = createProgram request
            Results.Ok(program))) |> ignore

        app.MapGet("/rewrite/programs/{programId}", Func<string, IResult>(fun programId ->
            match tryGetProgramReconciled programId with
            | Some program -> Results.Ok(program)
            | None -> Results.NotFound(sprintf "Unknown program %s" programId))) |> ignore

        app.MapGet("/rewrite/programs/{programId}/k8s-plan", Func<string, IResult>(fun programId ->
            match tryGetProgramDeploymentPlan programId with
            | Some plan -> Results.Ok(plan)
            | None -> Results.NotFound(sprintf "Unknown program %s" programId))) |> ignore

        app.MapPost("/rewrite/programs/{programId}/k8s-plan/materialize", Func<string, IResult>(fun programId ->
            match tryGetProgramDeploymentPlan programId with
            | Some plan ->
                let artifact = materializeProgramDeploymentPlan plan
                Results.Ok(dict [ "plan", box plan; "artifact", box artifact ])
            | None -> Results.NotFound(sprintf "Unknown program %s" programId))) |> ignore

        app.MapPost("/rewrite/programs/{programId}/k8s-plan/launch", Func<string, IResult>(fun programId ->
            match tryGetProgramDeploymentPlan programId with
            | Some plan ->
                let planArtifact = materializeProgramDeploymentPlan plan
                let receipt = Launch.launchProgramPlan services.ResearchStore plan Launch.defaultRequest
                let receiptArtifact = persistLaunchReceipt "program" programId receipt.LaunchId receipt
                Results.Ok(
                    dict
                        [ "plan", box plan
                          "plan_artifact", box planArtifact
                          "launch_receipt", box receipt
                          "launch_artifact", box receiptArtifact ])
            | None -> Results.NotFound(sprintf "Unknown program %s" programId))) |> ignore

        app.MapPost("/rewrite/programs/{programId}/campaigns", Func<string, CampaignRequest, IResult>(fun programId request ->
            match services.ResearchStore.TryGetProgram(programId) with
            | None -> Results.NotFound(sprintf "Unknown program %s" programId)
            | Some _ ->
                let queuedCampaign =
                    { request with
                        ProgramId = Some programId }
                    |> createCampaignQueued

                match attachCampaignToProgram programId { CampaignId = queuedCampaign.CampaignId } with
                | Some program -> Results.Ok(dict [ "program", box program; "campaign", box queuedCampaign ])
                | None -> Results.NotFound(sprintf "Unable to attach queued campaign %s to program %s" queuedCampaign.CampaignId programId))) |> ignore

        app.MapPost("/rewrite/programs/{programId}/campaigns/attach", Func<string, ProgramCampaignAttachRequest, IResult>(fun programId request ->
            match attachCampaignToProgram programId request with
            | Some program -> Results.Ok(program)
            | None -> Results.NotFound(sprintf "Unable to attach campaign %s to program %s" request.CampaignId programId))) |> ignore

        app.MapGet("/rewrite/portfolio/campaigns", Func<_>(fun () ->
            PortfolioSemantics.buildCampaignPortfolio (listCampaignsReconciled ()))) |> ignore

        app.MapGet("/rewrite/portfolio/programs", Func<_>(fun () ->
            PortfolioSemantics.buildProgramPortfolio (reconcilePrograms()))) |> ignore

        app.Run()
        0

