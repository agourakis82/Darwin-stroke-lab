module Tests

open System
open System.Collections.Generic
open System.Diagnostics
open System.IO
open System.Text
open Darwin.ResearchOs.Contracts
open Darwin.ResearchOs.Core
open Darwin.ResearchOs.K8s
open Darwin.ResearchOs.Storage
open Darwin.StrokeLab
open Xunit

let private dict (pairs: seq<string * obj>) =
    let target = Dictionary<string, obj>()
    for (key, value) in pairs do
        target[key] <- value
    target

let private metric value = dict [ "value", box value ]

let private benchmarkRun runId datasetVersion leaderModel dice auc mae =
    { Defaults.benchmarkRun runId datasetVersion with
        Metrics =
            dict
                [ "sounio_hypercomplex",
                  box (
                      dict
                          [ "isles_dice", box (metric dice)
                            "auc", box (metric auc)
                            "aspects_mae", box (metric mae) ]
                  ) ]
        Leaderboard =
            dict
                [ "overall_rank",
                  box (
                      ResizeArray(
                          [ dict [ "model_name", box leaderModel ] ]
                      )
                  ) ] }

let private benchmarkJob jobId ownerId runId status =
    { JobId = jobId
      CreatedAt = DateTime.UtcNow.ToString("O")
      UpdatedAt = DateTime.UtcNow.ToString("O")
      OwnerType = "benchmark_run"
      OwnerId = ownerId
      Kind = "benchmark"
      Status = status
      QueueName = "rewrite-parity"
      Executor = "worker"
      RequestPayload = Dictionary()
      ResultPayload = dict [ "benchmark_run_id", box runId ]
      Error = "" }

let private benchmarkEntry label manifest jobId seed =
    { EntryId = Guid.NewGuid().ToString("N")
      Label = label
      Request = { Defaults.benchmarkRequest manifest with Seed = seed }
      JobId = Some jobId
      Status = "queued"
      BenchmarkRunId = None
      Error = "" }

let private campaignRecord campaignId name (entries: seq<CampaignEntry>) =
    { CampaignId = campaignId
      CreatedAt = DateTime.UtcNow.ToString("O")
      UpdatedAt = DateTime.UtcNow.ToString("O")
      Name = name
      Objective = "Port platform core parity to F#."
      Kind = "benchmark"
      ProgramId = None
      Status = "created"
      BenchmarkSpecs = ResizeArray(entries)
      Notes = "generated in F# tests"
      Summary = Dictionary() }

let private programRecord programId name (campaignIds: seq<string>) =
    { ProgramId = programId
      CreatedAt = DateTime.UtcNow.ToString("O")
      UpdatedAt = DateTime.UtcNow.ToString("O")
      Name = name
      Objective = "Compare experiment hypotheses at program level."
      Hypothesis = "The strongest campaign should propagate through the program and portfolio layers."
      Notes = "generated in F# tests"
      Status = "created"
      CampaignIds = ResizeArray(campaignIds)
      Summary = Dictionary() }

let private agentRunRecord runId =
    { RunId = runId
      JobId = None
      CreatedAt = DateTime.UtcNow.ToString("O")
      UpdatedAt = DateTime.UtcNow.ToString("O")
      Objective = "Port agent job orchestration to F#."
      Surface = "research"
      Status = "queued"
      RootAgent = "LabDirectorAgent"
      TraceId = $"{runId}-trace"
      SessionId = $"{runId}-session"
      SafetyPolicy = Defaults.safetyPolicy ()
      FinalOutput = ""
      Error = ""
      DatasetManifestPath = Some "/tmp/manifest-a.json"
      ExternalTestManifestPath = None
      BenchmarkRunId = None
      StudyId = None
      ResearchBriefId = None
      ArtifactPaths = ResizeArray()
      Warnings = ResizeArray() }

let private sounioKernelRequest requireSnio =
    { Label = "Sounio kernel test"
      KernelPath = None
      PersistArtifacts = true
      RequireSnio = requireSnio }

let private findRecommendedBySourceId sourceId (plan: CampaignClusterDeploymentPlan) =
    plan.RecommendedItems
    |> Seq.tryFind (fun item -> item.SourceId = sourceId)

let private dispatchResult status phase cancelled name =
    { ClusterJobName = Some name
      Namespace = "darwin-genomics"
      LocalQueue = "darwin-lab"
      ClusterQueue = Some "darwin-shared"
      Status = status
      ClusterPhase = phase
      Submitted = true
      Cancelled = cancelled
      KueueAdmitted = Some true
      KueueFinished = if status = "completed" then Some true elif status = "cancelled" then Some false else None
      Diagnostics = ResizeArray() }

let private tryGetString key (source: Dictionary<string, obj>) =
    if source.ContainsKey(key) then
        match source[key] with
        | :? string as value when not (String.IsNullOrWhiteSpace value) -> Some value
        | _ -> None
    else
        None

let private tryGetBool key (source: Dictionary<string, obj>) =
    if source.ContainsKey(key) then
        match source[key] with
        | :? bool as value -> Some value
        | _ -> None
    else
        None

let private createMask depth height width =
    Array3D.zeroCreate<bool> depth height width

let private createProbabilityMap depth height width =
    Array3D.zeroCreate<float> depth height width

let private writeNpyFloat32 (path: string) (values: float32[,,]) =
    use stream = File.Open(path, FileMode.Create, FileAccess.Write, FileShare.None)
    let magic = [| 0x93uy; byte 'N'; byte 'U'; byte 'M'; byte 'P'; byte 'Y' |]
    stream.Write(magic, 0, magic.Length)
    stream.WriteByte(1uy)
    stream.WriteByte(0uy)

    let depth = values.GetLength(0)
    let height = values.GetLength(1)
    let width = values.GetLength(2)
    let headerCore = $"{{'descr': '<f4', 'fortran_order': False, 'shape': ({depth}, {height}, {width}), }}"
    let baseLength = 10 + headerCore.Length + 1
    let padding = (16 - (baseLength % 16)) % 16
    let header = headerCore + String(' ', padding) + "\n"
    let headerBytes = Encoding.ASCII.GetBytes(header)
    let headerLengthBytes = BitConverter.GetBytes(uint16 headerBytes.Length)
    stream.Write(headerLengthBytes, 0, headerLengthBytes.Length)
    stream.Write(headerBytes, 0, headerBytes.Length)

    for z in 0 .. depth - 1 do
        for y in 0 .. height - 1 do
            for x in 0 .. width - 1 do
                let bytes = BitConverter.GetBytes(values[z, y, x])
                stream.Write(bytes, 0, bytes.Length)

let private createManifestBackedCase
    (caseRoot: string)
    (caseId: string)
    (split: string)
    (positive: bool)
    (aspectsScore: int option)
    =
    let volume = Array3D.create 4 8 8 0.75f
    let mask = Array3D.zeroCreate<float32> 4 8 8

    if positive then
        for z in 1 .. 2 do
            for y in 2 .. 4 do
                for x in 3 .. 5 do
                    volume[z, y, x] <- 0.12f
                    mask[z, y, x] <- 1.0f

    let volumePath = Path.Combine(caseRoot, $"{caseId}_volume.npy")
    let maskPath = Path.Combine(caseRoot, $"{caseId}_mask.npy")
    writeNpyFloat32 volumePath volume
    writeNpyFloat32 maskPath mask

    dict
        [ "case_id", box caseId
          "split", box split
          "volume_path", box volumePath
          "lesion_mask_path", box maskPath
          "hemisphere", box "left"
          "aspects_score", box (defaultArg aspectsScore 10) ]

let private writeManifestFixture (root: string) =
    let caseRoot = Path.Combine(root, "cases")
    Directory.CreateDirectory(caseRoot) |> ignore

    let manifest =
        dict
            [ "dataset_name", box "fsharp-manifest-benchmark"
              "dataset_version", box "fsharp-fixture-v1"
              "split_policy", box "fixed train/test"
              "source", box "fsharp xunit fixture"
              "cases",
              box (
                  ResizeArray(
                      [ createManifestBackedCase caseRoot "case-train-1" "train" false (Some 10)
                        createManifestBackedCase caseRoot "case-train-2" "train" true (Some 8)
                        createManifestBackedCase caseRoot "case-test-1" "test" false (Some 10)
                        createManifestBackedCase caseRoot "case-test-2" "test" true (Some 8) ]
                  )
              ) ]

    let manifestPath = Path.Combine(root, "benchmark_manifest.json")
    File.WriteAllText(manifestPath, System.Text.Json.JsonSerializer.Serialize(manifest))
    manifestPath

[<Fact>]
let ``campaign reconcile selects best run and emits ready plan reports`` () =
    let store = InMemoryResearchStore() :> IResearchStore

    let runA = benchmarkRun "run-a" "fixture-v1" "python_3d_conventional" 0.54 0.72 0.92
    let runB = benchmarkRun "run-b" "fixture-v1" "sounio_hypercomplex" 0.68 0.83 0.58
    store.SaveBenchmarkRun(runA)
    store.SaveBenchmarkRun(runB)

    let jobA = benchmarkJob "job-a" "campaign-a" runA.RunId "completed"
    let jobB = benchmarkJob "job-b" "campaign-a" runB.RunId "completed"
    store.SaveJob(jobA)
    store.SaveJob(jobB)

    let campaign =
        campaignRecord
            "campaign-a"
            "Seed sweep"
            [ benchmarkEntry "seed-13" "/tmp/manifest-a.json" jobA.JobId 13
              benchmarkEntry "seed-14" "/tmp/manifest-b.json" jobB.JobId 14 ]

    let reconciled =
        CampaignSemantics.reconcile
            { TryGetJob = store.TryGetJob
              TryGetBenchmarkRun = store.TryGetBenchmarkRun }
            campaign

    Assert.Equal("completed", reconciled.Status)
    Assert.Equal(2, reconciled.BenchmarkSpecs.Count)
    Assert.Equal("run-b", tryGetString "benchmark_run_id" (unbox<Dictionary<string, obj>> reconciled.Summary["best_run"]) |> Option.defaultValue "")
    Assert.NotEmpty(unbox<ResizeArray<string>> reconciled.Summary["next_experiment_recommendations"])

    let planReports = unbox<ResizeArray<Dictionary<string, obj>>> reconciled.Summary["experiment_plan_reports"]
    Assert.NotEmpty(planReports)
    Assert.Equal("ready", tryGetString "acceptance_status" planReports[0] |> Option.defaultValue "")

[<Fact>]
let ``stroke evaluation captures overlap and lesion counts`` () =
    let truth = createMask 8 8 8
    let probability = createProbabilityMap 8 8 8

    for z in 1 .. 2 do
        for y in 1 .. 2 do
            for x in 1 .. 2 do
                truth[z, y, x] <- true
                probability[z, y, x] <- 1.0

    for z in 5 .. 6 do
        for y in 5 .. 6 do
            for x in 5 .. 6 do
                truth[z, y, x] <- true

    for z in 4 .. 5 do
        for y in 4 .. 5 do
            for x in 4 .. 5 do
                probability[z, y, x] <- 1.0

    let summary = StrokeEvaluation.summarizeSegmentation 0.5 0.001 truth probability

    Assert.True(summary.Dice > 0.0 && summary.Dice < 1.0)
    Assert.True(summary.LesionwiseF1 > 0.0 && summary.LesionwiseF1 < 1.0)
    Assert.Equal(0, summary.AbsoluteLesionCountDifference)
    Assert.True(summary.AbsoluteVolumeDifferenceMl >= 0.0)

[<Fact>]
let ``stroke evaluation returns perfect score when both masks are empty`` () =
    let truth = createMask 4 4 4
    let probability = createProbabilityMap 4 4 4

    let summary = StrokeEvaluation.summarizeSegmentation 0.5 1.0 truth probability

    Assert.Equal(1.0, summary.Dice, 6)
    Assert.Equal(1.0, summary.LesionwiseF1, 6)
    Assert.Equal(0, summary.AbsoluteLesionCountDifference)
    Assert.Equal(0.0, summary.AbsoluteVolumeDifferenceMl, 6)

[<Fact>]
let ``stroke evaluation aligns truth shape before scoring`` () =
    let truth = createMask 2 8 8
    let probability = createProbabilityMap 4 4 4

    for z in 0 .. 1 do
        for y in 2 .. 5 do
            for x in 2 .. 5 do
                truth[z, y, x] <- true

    for z in 0 .. 3 do
        for y in 1 .. 2 do
            for x in 1 .. 2 do
                probability[z, y, x] <- 1.0

    let summary = StrokeEvaluation.summarizeSegmentation 0.5 1.0 truth probability

    Assert.Equal(1.0, summary.Dice, 6)
    Assert.Equal(1.0, summary.LesionwiseF1, 6)
    Assert.Equal(0, summary.AbsoluteLesionCountDifference)
    Assert.Equal(0.0, summary.AbsoluteVolumeDifferenceMl, 6)

[<Fact>]
let ``stroke leaderboard reproduces Python-style mean rank ordering`` () =
    let leaderboard =
        StrokeLeaderboard.buildLeaderboard
            [ { ModelName = "sounio_hypercomplex"
                IslesDice = Some 0.71
                IslesLesionwiseF1 = Some 0.59
                IslesAbsoluteVolumeDifferenceMl = Some 12.0
                IslesAbsoluteLesionCountDifference = Some 0.0
                Auc = Some 0.86
                AspectsMae = Some 0.51 }
              { ModelName = "python_3d_conventional"
                IslesDice = Some 0.68
                IslesLesionwiseF1 = Some 0.52
                IslesAbsoluteVolumeDifferenceMl = Some 18.0
                IslesAbsoluteLesionCountDifference = Some 1.0
                Auc = Some 0.82
                AspectsMae = Some 0.62 }
              { ModelName = "cpp_equivalent"
                IslesDice = Some 0.66
                IslesLesionwiseF1 = Some 0.5
                IslesAbsoluteVolumeDifferenceMl = Some 20.0
                IslesAbsoluteLesionCountDifference = Some 1.0
                Auc = Some 0.8
                AspectsMae = Some 0.66 } ]

    Assert.Equal("sounio_hypercomplex", leaderboard.OverallRank[0].ModelName)
    Assert.Equal(1, leaderboard.OverallRank[0].Rank)
    Assert.Equal(1, leaderboard.MetricRankings["isles_dice"]["sounio_hypercomplex"])

[<Fact>]
let ``program reconcile and portfolios preserve the leading campaign`` () =
    let store = InMemoryResearchStore() :> IResearchStore

    let runA = benchmarkRun "run-1" "fixture-v1" "python_3d_conventional" 0.59 0.75 0.84
    let runB = benchmarkRun "run-2" "fixture-v1" "sounio_hypercomplex" 0.71 0.86 0.51
    let runC = benchmarkRun "run-3" "fixture-v2" "python_3d_conventional" 0.48 0.69 0.97

    [ runA; runB; runC ] |> List.iter store.SaveBenchmarkRun

    [ benchmarkJob "job-1" "campaign-1" runA.RunId "completed"
      benchmarkJob "job-2" "campaign-1" runB.RunId "completed"
      benchmarkJob "job-3" "campaign-2" runC.RunId "completed" ]
    |> List.iter store.SaveJob

    let campaign1 =
        campaignRecord
            "campaign-1"
            "Winner campaign"
            [ benchmarkEntry "winner-seed-13" "/tmp/manifest-a.json" "job-1" 13
              benchmarkEntry "winner-seed-14" "/tmp/manifest-b.json" "job-2" 14 ]

    let campaign2 =
        campaignRecord
            "campaign-2"
            "Control campaign"
            [ benchmarkEntry "control-seed-13" "/tmp/manifest-a.json" "job-3" 13 ]

    let deps : CampaignSemantics.Dependencies =
        { TryGetJob = store.TryGetJob
          TryGetBenchmarkRun = store.TryGetBenchmarkRun }

    let reconciled1 = CampaignSemantics.reconcile deps campaign1
    let reconciled2 = CampaignSemantics.reconcile deps campaign2

    let program =
        programRecord
            "program-1"
            "Sensitivity program"
            [ reconciled1.CampaignId; reconciled2.CampaignId ]

    let reconciledProgram = ProgramSemantics.reconcile [ reconciled1; reconciled2 ] program
    let campaignPortfolio = PortfolioSemantics.buildCampaignPortfolio [ reconciled1; reconciled2 ]
    let programPortfolio = PortfolioSemantics.buildProgramPortfolio [ reconciledProgram ]

    Assert.Equal("completed", reconciledProgram.Status)
    Assert.Equal(Some "campaign-1", reconciledProgram.Summary["leading_campaign_id"] |> unbox<string> |> Some)
    Assert.Equal(Some "campaign-1", campaignPortfolio.LeadingCampaignId)
    Assert.Equal(Some "program-1", programPortfolio.LeadingProgramId)
    Assert.Equal(Some "sounio_hypercomplex", campaignPortfolio.Campaigns[0].LeadingModel)
    Assert.Equal(Some "sounio_hypercomplex", programPortfolio.Programs[0].LeadingModel)

[<Fact>]
let ``submit and run once drains older benchmark queue items before returning target run`` () =
    let store = InMemoryResearchStore() :> IResearchStore
    let objectRoot = Path.Combine(Path.GetTempPath(), $"darwin-parity-{Guid.NewGuid():N}")
    Directory.CreateDirectory(objectRoot) |> ignore

    try
        let staleJob =
            LocalParityJobBackend(store).SubmitBenchmark(
                { DatasetManifestPath = "/workspace/stale.json"
                  ExternalTestManifestPath = None
                  TrainSplit = "train"
                  TestSplit = "test"
                  Seed = 9 }
            )

        let objectStore = FilesystemObjectStore(objectRoot) :> IObjectStore
        let completedJob, run =
            ParityBenchmark.submitAndRunOnce
                store
                objectStore
                "local"
                { DatasetManifestPath = "/workspace/target.json"
                  ExternalTestManifestPath = None
                  TrainSplit = "train"
                  TestSplit = "test"
                  Seed = 13 }

        Assert.Equal("completed", completedJob.Status)
        Assert.Equal("target", run.DatasetVersion)
        Assert.Equal(Some "completed", store.TryGetJob(staleJob.JobId) |> Option.map (fun item -> item.Status))
    finally
        if Directory.Exists(objectRoot) then
            Directory.Delete(objectRoot, true)

[<Fact>]
let ``in-memory research store round-trips core records`` () =
    let store = InMemoryResearchStore() :> IResearchStore

    let artifact =
        { ArtifactId = "artifact-1"
          CreatedAt = DateTime.UtcNow.ToString("O")
          OwnerType = "campaign"
          OwnerId = "campaign-1"
          Name = "campaign_report"
          Kind = "markdown"
          Path = "/tmp/campaign_report.md"
          Uri = Some "minio://darwin/campaign_report.md"
          Description = "campaign artifact"
          ContentType = "text/markdown"
          Bytes = Nullable 128 }

    let program = programRecord "program-store" "Store parity" []
    let campaign = campaignRecord "campaign-store" "Store candidate" []
    let job = benchmarkJob "job-store" "campaign-store" "run-store" "queued"
    let run = benchmarkRun "run-store" "fixture-store" "sounio_hypercomplex" 0.61 0.8 0.6

    store.SaveArtifact(artifact)
    store.SaveProgram(program)
    store.SaveCampaign(campaign)
    store.SaveJob(job)
    store.SaveBenchmarkRun(run)

    Assert.Equal(1, store.ListArtifacts().Count)
    Assert.Equal(Some artifact.ArtifactId, store.ListArtifacts() |> Seq.tryHead |> Option.map (fun item -> item.ArtifactId))
    Assert.Equal(Some program.ProgramId, store.TryGetProgram(program.ProgramId) |> Option.map (fun item -> item.ProgramId))
    Assert.Equal(Some campaign.CampaignId, store.TryGetCampaign(campaign.CampaignId) |> Option.map (fun item -> item.CampaignId))
    Assert.Equal(Some job.JobId, store.TryGetJob(job.JobId) |> Option.map (fun item -> item.JobId))
    Assert.Equal(Some run.RunId, store.TryGetBenchmarkRun(run.RunId) |> Option.map (fun item -> item.RunId))

[<Fact>]
let ``benchmark worker executes queued jobs through the local parity backend`` () =
    let store = InMemoryResearchStore() :> IResearchStore
    let backend = LocalParityJobBackend(store)
    let objectRoot = Path.Combine(Path.GetTempPath(), $"darwin-fsharp-core-{Guid.NewGuid():N}")
    Directory.CreateDirectory(objectRoot) |> ignore
    let objectStore = FilesystemObjectStore(objectRoot) :> IObjectStore

    let job = backend.SubmitBenchmark(Defaults.benchmarkRequest "/tmp/worker-manifest.json")
    try
        let worker = BenchmarkWorker(store, ParityBenchmark.createRunner store objectStore, "local")
        let completed = worker.RunOnce()

        Assert.True(backend.EnsureWorkerAvailable("benchmark"))
        Assert.True(completed.IsSome)
        Assert.Equal(Some "completed", store.TryGetJob(job.JobId) |> Option.map (fun item -> item.Status))
        Assert.Equal(Some job.OwnerId, store.TryGetBenchmarkRun(job.OwnerId) |> Option.map (fun item -> item.RunId))
        Assert.Equal(1, store.ListArtifacts().Count)
    finally
        if Directory.Exists(objectRoot) then
            Directory.Delete(objectRoot, true)

[<Fact>]
let ``submitAndRunOnce executes parity benchmark and persists artifact`` () =
    let store = InMemoryResearchStore() :> IResearchStore
    let objectRoot = Path.Combine(Path.GetTempPath(), $"darwin-fsharp-inline-{Guid.NewGuid():N}")
    Directory.CreateDirectory(objectRoot) |> ignore
    let objectStore = FilesystemObjectStore(objectRoot) :> IObjectStore

    try
        let job, run =
            ParityBenchmark.submitAndRunOnce
                store
                objectStore
                "local"
                { Defaults.benchmarkRequest "/tmp/inline-manifest.json" with Seed = 14 }

        Assert.Equal("completed", job.Status)
        Assert.Equal(job.OwnerId, run.RunId)

        let overallRank = run.Leaderboard["overall_rank"] |> unbox<ResizeArray<Dictionary<string, obj>>>
        let meanRank = run.Leaderboard["mean_rank"] |> unbox<Dictionary<string, obj>>
        let sounioMetrics = run.Metrics["sounio_hypercomplex"] |> unbox<Dictionary<string, obj>>

        Assert.Equal(Some "sounio_hypercomplex", overallRank |> Seq.tryHead |> Option.bind (fun x -> tryGetString "model_name" x))
        Assert.True(sounioMetrics.ContainsKey("isles_lesionwise_f1"))
        Assert.True(sounioMetrics.ContainsKey("isles_absolute_volume_difference_ml"))
        Assert.True(sounioMetrics.ContainsKey("isles_absolute_lesion_count_difference"))
        Assert.True(sounioMetrics.ContainsKey("predicted_lesion_volume_ml"))
        Assert.True(meanRank.ContainsKey("sounio_hypercomplex"))
        let artifacts = store.ListArtifacts()
        Assert.Equal(1, artifacts.Count)
        Assert.True(File.Exists(artifacts[0].Path))
    finally
        if Directory.Exists(objectRoot) then
            Directory.Delete(objectRoot, true)

[<Fact>]
let ``submitAndRunOnce prefers manifest-backed harness when benchmark cases are available`` () =
    let store = InMemoryResearchStore() :> IResearchStore
    let root = Path.Combine(Path.GetTempPath(), $"darwin-fsharp-manifest-{Guid.NewGuid():N}")
    Directory.CreateDirectory(root) |> ignore
    let manifestPath = writeManifestFixture root
    let objectStore = FilesystemObjectStore(root) :> IObjectStore

    try
        let _, run =
            ParityBenchmark.submitAndRunOnce
                store
                objectStore
                "local"
                { Defaults.benchmarkRequest manifestPath with Seed = 14 }

        Assert.Equal(Some "manifest", tryGetString "benchmark_source" run.ComputeBudget)
        Assert.Equal(Some "darwin.strokelab.manifest_harness.v1", tryGetString "evaluation_engine" run.ComputeBudget)
        Assert.Equal(Some "sounio_hypercomplex", run.Leaderboard["overall_rank"] |> unbox<ResizeArray<Dictionary<string, obj>>> |> Seq.tryHead |> Option.bind (fun x -> tryGetString "model_name" x))
        Assert.True(run.StratifiedMetrics.ContainsKey("lesion_positive"))
        Assert.True(run.FairnessChecks |> Seq.exists (fun item -> item.Contains("Loaded manifest-backed benchmark harness")))
    finally
        if Directory.Exists(root) then
            Directory.Delete(root, true)

[<Fact>]
let ``agent worker executes queued agent runs and queued cancellation stays terminal`` () =
    let store = InMemoryResearchStore() :> IResearchStore
    let backend = LocalParityJobBackend(store)

    let cancelled = backend.SubmitBenchmark(Defaults.benchmarkRequest "/tmp/cancel-manifest.json")
    let cancelledJob = backend.CancelJob(cancelled.JobId)
    Assert.Equal(Some "cancelled", cancelledJob |> Option.map (fun item -> item.Status))

    let run = agentRunRecord "agent-run-1"
    let agentJob = backend.SubmitAgentRun(run)

    let runner =
        { new IAgentRunJobRunner with
            member _.Execute(job, current) =
                { current with
                    JobId = Some job.JobId
                    UpdatedAt = DateTime.UtcNow.ToString("O")
                    Status = "completed"
                    FinalOutput = "agent parity completed" } }

    let worker = AgentRunWorker(store, runner, "local")
    let completed = worker.RunOnce()

    Assert.True(completed.IsSome)
    Assert.Equal(Some "completed", store.TryGetJob(agentJob.JobId) |> Option.map (fun item -> item.Status))
    Assert.Equal(Some "completed", store.TryGetAgentRun(run.RunId) |> Option.map (fun item -> item.Status))

[<Fact>]
let ``filesystem object store writes text and json artifacts`` () =
    let root = Path.Combine(Path.GetTempPath(), $"darwin-object-store-{Guid.NewGuid():N}")
    Directory.CreateDirectory(root) |> ignore

    let store = FilesystemObjectStore(root) :> IObjectStore
    let textObject = store.WriteText("reports/summary.txt", "rewrite lane")
    let jsonObject = store.WriteJson("reports/summary.json", dict [ "status", box "ok"; "count", box 2 ])
    let appended = store.AppendText("reports/log.txt", "first\n")

    Assert.True(File.Exists(textObject.LocalPath))
    Assert.True(File.Exists(jsonObject.LocalPath))
    Assert.True(File.Exists(appended.LocalPath))
    Assert.Equal(None, textObject.Uri)
    Assert.Contains("\"status\"", File.ReadAllText(jsonObject.LocalPath))

    Directory.Delete(root, true)

[<Fact>]
let ``require snio blocks cli fallback when no usable snio path succeeds`` () =
    let result = SounioRuntimeProbe.executeKernel (sounioKernelRequest true)

    if result.SnioUsed then
        Assert.Equal("completed", result.Status)
    else
        Assert.Equal("failed", result.Status)
        Assert.Contains(result.Diagnostics, fun message -> message.Contains("RequireSnio=true", StringComparison.Ordinal))

[<Fact>]
let ``sounio kernel worker executes queued runtime jobs and persists snio metadata`` () =
    let store = InMemoryResearchStore() :> IResearchStore
    let backend = LocalParityJobBackend(store)
    let objectRoot = Path.Combine(Path.GetTempPath(), $"darwin-sounio-worker-{Guid.NewGuid():N}")
    Directory.CreateDirectory(objectRoot) |> ignore
    let objectStore = FilesystemObjectStore(objectRoot) :> IObjectStore
    let request = sounioKernelRequest false
    let job = backend.SubmitSounioKernel(request)

    let runner =
        { new ISounioKernelJobRunner with
            member _.Execute(job, request) =
                { ExecutionId = job.OwnerId
                  JobId = job.JobId
                  CreatedAt = job.CreatedAt
                  Label = request.Label
                  KernelPath = defaultArg request.KernelPath "/tmp/kernel.sio"
                  Status = "completed"
                  CheckSucceeded = true
                  RunSucceeded = true
                  SnioAttempted = true
                  SnioUsed = true
                  Output = ResizeArray([ "ok" ])
                  Diagnostics = ResizeArray([ "snio path exercised" ])
                  ArtifactIds = ResizeArray()
                  Runtime = SounioRuntimeProbe.probe () } }

    try
        let worker = SounioKernelWorker(store, objectStore, runner, "local")
        let completed = worker.RunOnce()

        Assert.True(completed.IsSome)
        Assert.Equal(Some "completed", store.TryGetJob(job.JobId) |> Option.map (fun item -> item.Status))
        let persistedJob = store.TryGetJob(job.JobId) |> Option.get
        Assert.Equal(Some true, persistedJob.ResultPayload.TryGetValue("snio_attempted") |> function | true, (:? bool as value) -> Some value | _ -> None)
        Assert.Equal(Some true, persistedJob.ResultPayload.TryGetValue("snio_used") |> function | true, (:? bool as value) -> Some value | _ -> None)
        Assert.True(store.ListArtifacts().Count >= 3)
    finally
        if Directory.Exists(objectRoot) then
            Directory.Delete(objectRoot, true)

[<Fact>]
let ``k8s sounio runtime template carries require_snio and kernel path`` () =
    let template =
        Templates.sounioRuntime
            { ApiImage = "ghcr.io/example/api:latest"
              WorkerImage = "ghcr.io/example/worker:latest" }
            (Some "/workspace/kernel.sio")
            true
    let rendered = Rendering.renderSounioRuntimeSummary template

    Assert.Equal("sounio_runtime", rendered.JobKind)
    Assert.Equal(Some "/workspace/kernel.sio", rendered.KernelPath)
    Assert.Equal(Some true, rendered.RequireSnio)
    Assert.Contains("--require-snio", rendered.Args)

[<Fact>]
let ``k8s benchmark template carries manifest and queue defaults`` () =
    let template =
        Templates.benchmark
            { ApiImage = "ghcr.io/example/api:latest"
              WorkerImage = "ghcr.io/example/worker:latest" }
            { DatasetManifestPath = "/workspace/manifest.json"
              ExternalTestManifestPath = Some "/workspace/external.json"
              TrainSplit = "train-a"
              TestSplit = "test-b"
              Seed = 29 }
    let rendered = Rendering.renderSummary template

    Assert.Equal("benchmark", rendered.JobKind)
    Assert.Equal("darwin-genomics", rendered.K8sNamespace)
    Assert.Equal("darwin-lab", rendered.LocalQueue)
    Assert.Equal(Some "darwin-shared", rendered.ClusterQueue)
    Assert.Contains("/workspace/manifest.json", rendered.Args)
    Assert.Contains("/workspace/external.json", rendered.Args)
    Assert.Contains("train-a", rendered.Args)
    Assert.Contains("test-b", rendered.Args)
    Assert.Contains("29", rendered.Args)

[<Fact>]
let ``campaign deployment plan renders active and recommended cluster jobs`` () =
    let store = InMemoryResearchStore() :> IResearchStore

    let runA = benchmarkRun "run-deploy-a" "fixture-v1" "python_3d_conventional" 0.58 0.73 0.81
    let runB = benchmarkRun "run-deploy-b" "fixture-v1" "sounio_hypercomplex" 0.69 0.84 0.55
    let jobA = benchmarkJob "job-deploy-a" "campaign-deploy" runA.RunId "completed"
    let jobB = benchmarkJob "job-deploy-b" "campaign-deploy" runB.RunId "completed"
    store.SaveBenchmarkRun(runA)
    store.SaveBenchmarkRun(runB)
    store.SaveJob(jobA)
    store.SaveJob(jobB)

    let campaign =
        campaignRecord
            "campaign-deploy"
            "Deployment-ready campaign"
            [ benchmarkEntry "seed-13" "/workspace/manifest-a.json" jobA.JobId 13
              benchmarkEntry "seed-14" "/workspace/manifest-b.json" jobB.JobId 14 ]

    let reconciled =
        CampaignSemantics.reconcile
            { TryGetJob = store.TryGetJob
              TryGetBenchmarkRun = store.TryGetBenchmarkRun }
            campaign

    let plan =
        DeploymentPlans.buildCampaignDeploymentPlan
            { ApiImage = "ghcr.io/example/api:latest"
              WorkerImage = "ghcr.io/example/worker:latest" }
            reconciled

    Assert.Equal(reconciled.CampaignId, plan.CampaignId)
    Assert.Equal(2, plan.ActiveItems.Count)
    Assert.NotEmpty(plan.RecommendedItems)
    Assert.Equal("darwin-genomics", plan.QueueNamespace)
    Assert.True(plan.TotalItems >= 3)

    let winnerSweep = findRecommendedBySourceId "plan-winner-reproducibility-sweep" plan
    Assert.True(winnerSweep.IsSome)
    Assert.Equal(Some "ready", winnerSweep.Value.AcceptanceStatus)
    Assert.True(winnerSweep.Value.LaunchReady)
    Assert.Equal(Some "/workspace/manifest-b.json", winnerSweep.Value.RenderedJob.DatasetManifestPath)

[<Fact>]
let ``program deployment plan aggregates campaign deployment plans`` () =
    let store = InMemoryResearchStore() :> IResearchStore

    let runA = benchmarkRun "run-program-a" "fixture-v1" "sounio_hypercomplex" 0.71 0.86 0.51
    let runB = benchmarkRun "run-program-b" "fixture-v1" "python_3d_conventional" 0.56 0.74 0.83
    store.SaveBenchmarkRun(runA)
    store.SaveBenchmarkRun(runB)
    store.SaveJob(benchmarkJob "job-program-a" "campaign-program-a" runA.RunId "completed")
    store.SaveJob(benchmarkJob "job-program-b" "campaign-program-b" runB.RunId "completed")

    let deps : CampaignSemantics.Dependencies =
        { TryGetJob = store.TryGetJob
          TryGetBenchmarkRun = store.TryGetBenchmarkRun }

    let campaignA =
        campaignRecord
            "campaign-program-a"
            "Program winner"
            [ benchmarkEntry "winner-13" "/workspace/manifest-a.json" "job-program-a" 13 ]
        |> CampaignSemantics.reconcile deps

    let campaignB =
        campaignRecord
            "campaign-program-b"
            "Program control"
            [ benchmarkEntry "control-13" "/workspace/manifest-c.json" "job-program-b" 13 ]
        |> CampaignSemantics.reconcile deps

    let program =
        programRecord
            "program-deploy"
            "Program deployment plan"
            [ campaignA.CampaignId; campaignB.CampaignId ]
        |> ProgramSemantics.reconcile [ campaignA; campaignB ]

    let deploymentPlan =
        DeploymentPlans.buildProgramDeploymentPlan
            { ApiImage = "ghcr.io/example/api:latest"
              WorkerImage = "ghcr.io/example/worker:latest" }
            program
            [ campaignA; campaignB ]

    Assert.Equal(program.ProgramId, deploymentPlan.ProgramId)
    Assert.Equal(2, deploymentPlan.CampaignPlans.Count)
    Assert.Equal(Some "campaign-program-a", deploymentPlan.LeadingCampaignId)
    Assert.True(deploymentPlan.LaunchReadyItems >= 2)
    Assert.Equal("darwin-lab", deploymentPlan.LocalQueue)

[<Fact>]
let ``campaign launch stages only launch-ready items into k8s dispatch queue`` () =
    let store = InMemoryResearchStore() :> IResearchStore

    let runA = benchmarkRun "run-launch-a" "fixture-v1" "python_3d_conventional" 0.58 0.73 0.81
    let runB = benchmarkRun "run-launch-b" "fixture-v1" "sounio_hypercomplex" 0.69 0.84 0.55
    store.SaveBenchmarkRun(runA)
    store.SaveBenchmarkRun(runB)
    store.SaveJob(benchmarkJob "job-launch-a" "campaign-launch" runA.RunId "completed")
    store.SaveJob(benchmarkJob "job-launch-b" "campaign-launch" runB.RunId "completed")

    let deps : CampaignSemantics.Dependencies =
        { TryGetJob = store.TryGetJob
          TryGetBenchmarkRun = store.TryGetBenchmarkRun }

    let plan =
        campaignRecord
            "campaign-launch"
            "Launchable campaign"
            [ benchmarkEntry "seed-13" "/datasets/manifest-a.json" "job-launch-a" 13
              benchmarkEntry "seed-14" "/datasets/manifest-b.json" "job-launch-b" 14 ]
        |> CampaignSemantics.reconcile deps
        |> DeploymentPlans.buildCampaignDeploymentPlan
            { ApiImage = "ghcr.io/example/api:latest"
              WorkerImage = "ghcr.io/example/worker:latest" }

    let receipt = Launch.launchCampaignPlan store plan Launch.defaultRequest
    let dispatchJobs =
        store.ListJobs()
        |> Seq.filter (fun item -> item.Kind = "k8s_dispatch")
        |> Seq.toList

    Assert.Equal(4, receipt.ItemCount)
    Assert.Equal(4, dispatchJobs.Length)
    Assert.All(
        dispatchJobs,
        fun job ->
            Assert.Equal("queued", job.Status)
            Assert.StartsWith("k8s:darwin-genomics/darwin-lab", job.QueueName)
    )

[<Fact>]
let ``program launch aggregates child campaign plans into k8s dispatch queue`` () =
    let store = InMemoryResearchStore() :> IResearchStore

    let runA = benchmarkRun "run-program-launch-a" "fixture-v1" "sounio_hypercomplex" 0.71 0.86 0.51
    let runB = benchmarkRun "run-program-launch-b" "fixture-v1" "python_3d_conventional" 0.56 0.74 0.83
    store.SaveBenchmarkRun(runA)
    store.SaveBenchmarkRun(runB)
    store.SaveJob(benchmarkJob "job-program-launch-a" "campaign-program-launch-a" runA.RunId "completed")
    store.SaveJob(benchmarkJob "job-program-launch-b" "campaign-program-launch-b" runB.RunId "completed")

    let deps : CampaignSemantics.Dependencies =
        { TryGetJob = store.TryGetJob
          TryGetBenchmarkRun = store.TryGetBenchmarkRun }

    let campaignA =
        campaignRecord
            "campaign-program-launch-a"
            "Program launch winner"
            [ benchmarkEntry "winner-13" "/datasets/manifest-a.json" "job-program-launch-a" 13 ]
        |> CampaignSemantics.reconcile deps

    let campaignB =
        campaignRecord
            "campaign-program-launch-b"
            "Program launch control"
            [ benchmarkEntry "control-13" "/datasets/manifest-c.json" "job-program-launch-b" 13 ]
        |> CampaignSemantics.reconcile deps

    let program =
        programRecord
            "program-launch"
            "Program launch plan"
            [ campaignA.CampaignId; campaignB.CampaignId ]
        |> ProgramSemantics.reconcile [ campaignA; campaignB ]

    let programPlan =
        DeploymentPlans.buildProgramDeploymentPlan
            { ApiImage = "ghcr.io/example/api:latest"
              WorkerImage = "ghcr.io/example/worker:latest" }
            program
            [ campaignA; campaignB ]

    let receipt = Launch.launchProgramPlan store programPlan Launch.defaultRequest

    Assert.True(receipt.ItemCount >= 2)
    Assert.Equal("program", receipt.ScopeKind)
    Assert.True(
        store.ListJobs()
        |> Seq.filter (fun item -> item.Kind = "k8s_dispatch")
        |> Seq.length
        >= receipt.ItemCount
    )

[<Fact>]
let ``campaign launch skips items whose manifests are outside shared dataset path`` () =
    let store = InMemoryResearchStore() :> IResearchStore

    let runA = benchmarkRun "run-launch-invalid-a" "fixture-v1" "sounio_hypercomplex" 0.71 0.86 0.51
    store.SaveBenchmarkRun(runA)
    store.SaveJob(benchmarkJob "job-launch-invalid-a" "campaign-launch-invalid" runA.RunId "completed")

    let deps : CampaignSemantics.Dependencies =
        { TryGetJob = store.TryGetJob
          TryGetBenchmarkRun = store.TryGetBenchmarkRun }

    let plan =
        campaignRecord
            "campaign-launch-invalid"
            "Invalid cluster path campaign"
            [ benchmarkEntry "seed-13" "/workspace/manifest-a.json" "job-launch-invalid-a" 13 ]
        |> CampaignSemantics.reconcile deps
        |> DeploymentPlans.buildCampaignDeploymentPlan
            { ApiImage = "ghcr.io/example/api:latest"
              WorkerImage = "ghcr.io/example/worker:latest" }

    let receipt = Launch.launchCampaignPlan store plan Launch.defaultRequest

    Assert.Equal(0, receipt.ItemCount)
    Assert.True(receipt.SkippedCount >= 1)
    Assert.Contains(receipt.Notes, fun note -> note.Contains("dataset_manifest_path /workspace/manifest-a.json is not cluster-accessible"))
    Assert.Empty(
        store.ListJobs()
        |> Seq.filter (fun item -> item.Kind = "k8s_dispatch")
        |> Seq.toList
    )

[<Fact>]
let ``standalone sounio runtime launch stages remote target and dispatch jobs`` () =
    let store = InMemoryResearchStore() :> IResearchStore
    let rendered =
        Templates.sounioRuntime
            { ApiImage = "ghcr.io/example/api:latest"
              WorkerImage = "ghcr.io/example/worker:latest" }
            (Some "/app/sounio/kernels/runtime_probe.sio")
            false
        |> Rendering.renderSounioRuntimeSummary

    let receipt =
        Launch.launchStandaloneItem
            store
            "sounio_runtime"
            "scope-sounio"
            "sounio_kernel_request"
            "request-sounio"
            "Cluster Sounio"
            "standalone-sounio"
            None
            rendered

    match receipt with
    | Error errors ->
        failwithf "Expected launch receipt, got validation errors: %A" errors
    | Ok item ->
        Assert.True(item.TargetJobId.IsSome)
        let dispatchJob = store.TryGetJob(item.DispatchJobId)
        let targetJob = item.TargetJobId |> Option.bind store.TryGetJob

        Assert.True(dispatchJob.IsSome)
        Assert.True(targetJob.IsSome)
        Assert.Equal("k8s_dispatch", dispatchJob.Value.Kind)
        Assert.Equal("sounio_runtime_remote", targetJob.Value.Kind)
        Assert.Equal(Some "/app/sounio/kernels/runtime_probe.sio", tryGetString "kernel_path" targetJob.Value.RequestPayload)
        Assert.Equal(Some false, tryGetBool "require_snio" targetJob.Value.RequestPayload)

[<Fact>]
let ``k8s dispatch worker submits queued jobs and records cluster metadata`` () =
    let store = InMemoryResearchStore() :> IResearchStore

    let runA = benchmarkRun "run-dispatch-a" "fixture-v1" "sounio_hypercomplex" 0.71 0.86 0.51
    let runB = benchmarkRun "run-dispatch-b" "fixture-v1" "python_3d_conventional" 0.56 0.74 0.83
    store.SaveBenchmarkRun(runA)
    store.SaveBenchmarkRun(runB)
    store.SaveJob(benchmarkJob "job-dispatch-a" "campaign-dispatch" runA.RunId "completed")
    store.SaveJob(benchmarkJob "job-dispatch-b" "campaign-dispatch" runB.RunId "completed")

    let deps : CampaignSemantics.Dependencies =
        { TryGetJob = store.TryGetJob
          TryGetBenchmarkRun = store.TryGetBenchmarkRun }

    let plan =
        campaignRecord
            "campaign-dispatch"
            "Dispatch worker campaign"
            [ benchmarkEntry "seed-13" "/datasets/manifest-a.json" "job-dispatch-a" 13
              benchmarkEntry "seed-14" "/datasets/manifest-b.json" "job-dispatch-b" 14 ]
        |> CampaignSemantics.reconcile deps
        |> DeploymentPlans.buildCampaignDeploymentPlan
            { ApiImage = "ghcr.io/example/api:latest"
              WorkerImage = "ghcr.io/example/worker:latest" }

    let receipt = Launch.launchCampaignPlan store plan Launch.defaultRequest
    let mutable submitCalls = 0

    let runner =
        { new IK8sDispatchRunner with
            member _.Submit(job) =
                submitCalls <- submitCalls + 1
                dispatchResult "running" "submitted" false $"cluster-{job.JobId}"
            member _.Poll(_) = dispatchResult "completed" "finished" false "cluster-polled"
            member _.Cancel(_) = dispatchResult "cancelled" "cancelled" true "cluster-cancelled" }

    let worker = K8sDispatchWorker(store, runner, "k8s")
    let processed = worker.RunOnce()

    Assert.True(processed.IsSome)
    Assert.Equal(1, submitCalls)
    let updated = processed.Value
    Assert.Equal("running", updated.Status)
    Assert.Equal(Some "submitted", tryGetString "cluster_phase" updated.ResultPayload)
    Assert.Equal(Some ("cluster-" + updated.JobId), tryGetString "cluster_job_name" updated.ResultPayload)

[<Fact>]
let ``k8s dispatch worker polls and cancels running jobs`` () =
    let store = InMemoryResearchStore() :> IResearchStore
    let now = DateTime.UtcNow.ToString("O")
    let job =
        { JobId = "k8s-running-job"
          CreatedAt = now
          UpdatedAt = now
          OwnerType = "cluster_deployment_item"
          OwnerId = "item-1"
          Kind = "k8s_dispatch"
          Status = "running"
          QueueName = "k8s:darwin-genomics/darwin-lab"
          Executor = "fsharp-k8s-dispatcher"
          RequestPayload =
            dict
                [ "rendered_job",
                  box (
                      dict
                          [ "kind", box "Job"
                            "job_kind", box "benchmark"
                            "entrypoint", box "sounio-stroke-lab"
                            "args", box (ResizeArray([ "run-benchmark"; "--manifest"; "/workspace/manifest.json" ]))
                            "k8s_namespace", box "darwin-genomics"
                            "local_queue", box "darwin-lab"
                            "cluster_queue", box "darwin-shared"
                            "shared_dataset_path", box "/datasets"
                            "artifact_prefix", box "benchmark/"
                            "api_image", box "ghcr.io/example/api:latest"
                            "worker_image", box "ghcr.io/example/worker:latest"
                            "dataset_manifest_path", box "/workspace/manifest.json"
                            "external_test_manifest_path", box ""
                            "train_split", box "train"
                            "test_split", box "test"
                            "seed", box 13 ] ) ]
          ResultPayload =
            dict
                [ "cluster_job_name", box "darwin-benchmark-123"
                  "cancel_requested", box false ]
          Error = "" }
    store.SaveJob(job)

    let mutable pollCalls = 0
    let mutable cancelCalls = 0
    let runner =
        { new IK8sDispatchRunner with
            member _.Submit(_) = dispatchResult "running" "submitted" false "cluster-submit"
            member _.Poll(_) =
                pollCalls <- pollCalls + 1
                dispatchResult "completed" "finished" false "darwin-benchmark-123"
            member _.Cancel(_) =
                cancelCalls <- cancelCalls + 1
                dispatchResult "cancelled" "cancelled" true "darwin-benchmark-123" }

    let worker = K8sDispatchWorker(store, runner, "k8s")
    let completed = worker.RunOnce()
    Assert.True(completed.IsSome)
    Assert.Equal(1, pollCalls)
    Assert.Equal("completed", completed.Value.Status)

    store.UpdateJob(
        job.JobId,
        fun current ->
            let payload = Dictionary<string, obj>(current.ResultPayload)
            payload["cancel_requested"] <- box true
            { current with Status = "running"; ResultPayload = payload }
    ) |> ignore

    let cancelled = worker.RunOnce()
    Assert.True(cancelled.IsSome)
    Assert.Equal(1, cancelCalls)
    Assert.Equal("cancelled", cancelled.Value.Status)

[<Fact>]
let ``kubectl dispatch runner can submit poll and cancel with scripted kubectl`` () =
    let tempRoot = Path.Combine(Path.GetTempPath(), $"darwin-kubectl-{Guid.NewGuid():N}")
    Directory.CreateDirectory(tempRoot) |> ignore
    let scriptPath = Path.Combine(tempRoot, "kubectl")

    File.WriteAllText(
        scriptPath,
        """#!/bin/sh
set -eu
cmd="$1"
shift
if [ "$cmd" = "apply" ]; then
  cat >/dev/null
  printf '{"spec":{"suspend":false},"status":{"active":1}}'
  exit 0
fi
if [ "$cmd" = "get" ] && [ "$1" = "job" ]; then
  job_name="$2"
  printf '{"metadata":{"name":"%s"},"status":{"conditions":[{"type":"Complete","status":"True"}]}}' "$job_name"
  exit 0
fi
if [ "$cmd" = "delete" ] && [ "$1" = "job" ]; then
  job_name="$2"
  printf 'job.batch "%s" deleted' "$job_name"
  exit 0
fi
echo "unexpected args: $cmd $*" >&2
exit 1
"""
    )

    let chmod = ProcessStartInfo("/bin/chmod", $"+x {scriptPath}")
    chmod.UseShellExecute <- false
    use proc = Process.Start(chmod)
    proc.WaitForExit()

    let previousKubectl = Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_KUBECTL_PATH")
    Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_KUBECTL_PATH", scriptPath)

    try
        let job =
            { JobId = "kubectl-job"
              CreatedAt = DateTime.UtcNow.ToString("O")
              UpdatedAt = DateTime.UtcNow.ToString("O")
              OwnerType = "cluster_deployment_item"
              OwnerId = "item-kubectl"
              Kind = "k8s_dispatch"
              Status = "queued"
              QueueName = "k8s:darwin-genomics/darwin-lab"
              Executor = "fsharp-k8s-dispatcher"
              RequestPayload =
                dict
                    [ "rendered_job",
                      box (
                          dict
                              [ "kind", box "Job"
                                "job_kind", box "benchmark"
                                "entrypoint", box "sounio-stroke-lab"
                                "args", box (ResizeArray([ "run-benchmark"; "--manifest"; "/workspace/manifest.json" ]))
                                "k8s_namespace", box "darwin-genomics"
                                "local_queue", box "darwin-lab"
                                "cluster_queue", box "darwin-shared"
                                "shared_dataset_path", box "/datasets"
                                "artifact_prefix", box "benchmark/"
                                "api_image", box "ghcr.io/example/api:latest"
                                "worker_image", box "ghcr.io/example/worker:latest"
                                "dataset_manifest_path", box "/workspace/manifest.json"
                                "external_test_manifest_path", box ""
                                "train_split", box "train"
                                "test_split", box "test"
                                "seed", box 13 ] ) ]
              ResultPayload = dict []
              Error = "" }

        let runner = Dispatch.KubectlK8sDispatchRunner() :> IK8sDispatchRunner
        let submitted = runner.Submit(job)
        let withClusterName =
            { job with
                Status = "running"
                ResultPayload = dict [ "cluster_job_name", box (submitted.ClusterJobName |> Option.defaultValue "") ] }
        let polled = runner.Poll(withClusterName)
        let cancelled = runner.Cancel(withClusterName)

        Assert.Equal("completed", submitted.Status)
        Assert.Equal("finished", submitted.ClusterPhase)
        Assert.Equal("completed", polled.Status)
        Assert.Equal("finished", polled.ClusterPhase)
        Assert.Equal("cancelled", cancelled.Status)
    finally
        Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_KUBECTL_PATH", previousKubectl)
        if Directory.Exists(tempRoot) then
            Directory.Delete(tempRoot, true)

[<Fact>]
let ``kubectl dispatch manifest forwards sounio bundle env and pvc into cluster job`` () =
    let tempRoot = Path.Combine(Path.GetTempPath(), $"darwin-kubectl-env-{Guid.NewGuid():N}")
    Directory.CreateDirectory(tempRoot) |> ignore
    let scriptPath = Path.Combine(tempRoot, "kubectl")
    let manifestPath = Path.Combine(tempRoot, "manifest.json")

    File.WriteAllText(
        scriptPath,
        sprintf
            """#!/bin/sh
set -eu
cmd="$1"
shift
if [ "$cmd" = "apply" ]; then
  cat >"%s"
  printf '{"spec":{"suspend":false},"status":{"active":1}}'
  exit 0
fi
if [ "$cmd" = "get" ] && [ "$1" = "job" ]; then
  job_name="$2"
  printf '{"metadata":{"name":"%%s"},"status":{"conditions":[{"type":"Complete","status":"True"}]}}' "$job_name"
  exit 0
fi
echo "unexpected args: $cmd $*" >&2
exit 1
"""
            manifestPath
    )

    let chmod = ProcessStartInfo("/bin/chmod", $"+x {scriptPath}")
    chmod.UseShellExecute <- false
    use proc = Process.Start(chmod)
    proc.WaitForExit()

    let previousKubectl = Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_KUBECTL_PATH")
    let previousPvc = Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_DATASET_PVC")
    let previousSounioRoot = Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_SOUNIO_ROOT")
    let previousStdlib = Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_SOUNIO_STDLIB_PATH")

    Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_KUBECTL_PATH", scriptPath)
    Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_DATASET_PVC", "sounio-stroke-datasets")
    Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_SOUNIO_ROOT", "/opt/sounio")
    Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_SOUNIO_STDLIB_PATH", "/opt/sounio/stdlib")

    try
        let job =
            { JobId = "kubectl-sounio-job"
              CreatedAt = DateTime.UtcNow.ToString("O")
              UpdatedAt = DateTime.UtcNow.ToString("O")
              OwnerType = "cluster_deployment_item"
              OwnerId = "item-sounio"
              Kind = "k8s_dispatch"
              Status = "queued"
              QueueName = "k8s:darwin-genomics/darwin-lab"
              Executor = "fsharp-k8s-dispatcher"
              RequestPayload =
                dict
                    [ "target_job_id", box "remote-sounio-job"
                      "rendered_job",
                      box (
                          dict
                              [ "kind", box "Job"
                                "job_kind", box "sounio_runtime"
                                "entrypoint", box "/app/darwin-research-os-worker"
                                "args", box (ResizeArray([ "run-sounio-runtime"; "--kernel-path"; "/app/sounio/kernels/runtime_probe.sio" ]))
                                "k8s_namespace", box "darwin-genomics"
                                "local_queue", box "darwin-lab"
                                "cluster_queue", box "darwin-shared"
                                "shared_dataset_path", box "/datasets"
                                "artifact_prefix", box "sounio-runtime/"
                                "api_image", box "ghcr.io/example/api:latest"
                                "worker_image", box "ghcr.io/example/worker:latest"
                                "kernel_path", box "/app/sounio/kernels/runtime_probe.sio"
                                "require_snio", box false ] ) ]
              ResultPayload = dict []
              Error = "" }

        let runner = Dispatch.KubectlK8sDispatchRunner() :> IK8sDispatchRunner
        let submitted = runner.Submit(job)

        Assert.Equal("completed", submitted.Status)

        let manifest = File.ReadAllText(manifestPath)
        Assert.Contains("\"command\": [", manifest)
        Assert.Contains("/app/darwin-research-os-worker", manifest)
        Assert.Contains("run-sounio-runtime-job", manifest)
        Assert.Contains("remote-sounio-job", manifest)
        Assert.Contains("SOUNIO_ROOT", manifest)
        Assert.Contains("/opt/sounio", manifest)
        Assert.Contains("SOUNIO_STDLIB_PATH", manifest)
        Assert.Contains("SOUNIO_RUNTIME_LIB_PATH", manifest)
        Assert.Contains("/opt/sounio/runtime/target/release/libsounio_runtime.so", manifest)
        Assert.Contains("sounio-stroke-datasets", manifest)
        Assert.Contains("\"name\": \"sounio-runtime\"", manifest)
    finally
        Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_KUBECTL_PATH", previousKubectl)
        Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_DATASET_PVC", previousPvc)
        Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_SOUNIO_ROOT", previousSounioRoot)
        Environment.SetEnvironmentVariable("DARWIN_RESEARCH_OS_K8S_SOUNIO_STDLIB_PATH", previousStdlib)
        if Directory.Exists(tempRoot) then
            Directory.Delete(tempRoot, true)

[<Fact>]
let ``sounio runtime probe reports consistent native ffi state`` () =
    let report = SounioRuntimeProbe.probe ()

    match report.NativeBackendSourceRoot with
    | Some _ ->
        Assert.True(
            report.NativeTensorFfiSymbols.Count > 0
            || report.NativeUncertainFfiSymbols.Count > 0
            || report.NativeOdeFfiSymbols.Count > 0
            || report.NativeAutodiffFfiSymbols.Count > 0
        )
        Assert.True(report.SourceOnlyScientificFfiSymbols.Count >= 0)
    | None -> ()

    if report.NativeFfiUsable then
        Assert.True(report.NativeFfiDetected)
        Assert.True(report.NativeProbeSucceeded)
        Assert.NotEmpty(report.NativeProbeOutput)
        Assert.False(report.NativeKernelExecutionAvailable)

[<Fact>]
let ``sounio runtime abi inventory reports exported symbol groups consistently`` () =
    let abi = SounioRuntimeProbe.abiInventory ()

    match abi.SourceRoot with
    | Some _ ->
        Assert.True(abi.SourceExportCount >= 1)
        Assert.True(
            abi.TensorSymbols.Count > 0
            || abi.UncertainSymbols.Count > 0
            || abi.OdeSymbols.Count > 0
            || abi.AutodiffSymbols.Count > 0
        )
        Assert.True(abi.SourceOnlyScientificSymbols.Count >= 0)
    | None -> ()

    if abi.Detected then
        Assert.NotEmpty(abi.ExportedSymbols)
        Assert.True(abi.KnowledgeSymbols.Count >= 1)
        Assert.True(abi.BootstrapSymbols.Count >= 0)
        Assert.True(abi.CvSymbols.Count >= 0)
        if abi.SnioEmbedHeaderPath.IsSome || abi.SnioProtocolPath.IsSome || abi.SnioFsharpProtocolPath.IsSome then
            Assert.True(abi.KernelSessionProtocolAvailable)
        Assert.False(abi.NativeKernelExecutionAvailable)
