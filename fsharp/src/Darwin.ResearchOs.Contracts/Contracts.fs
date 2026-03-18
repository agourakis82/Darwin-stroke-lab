namespace Darwin.ResearchOs.Contracts

open System
open System.Collections.Generic

[<CLIMutable>]
type SafetyPolicy =
    { PrivacyMode: string
      AutonomyMode: string
      RemoteToolPolicy: string
      WriteScope: ResizeArray<string>
      PublicToolAllowlist: ResizeArray<string>
      LocalToolAllowlist: ResizeArray<string> }

[<CLIMutable>]
type BenchmarkRequest =
    { DatasetManifestPath: string
      ExternalTestManifestPath: string option
      TrainSplit: string
      TestSplit: string
      Seed: int }

[<CLIMutable>]
type JobRecord =
    { JobId: string
      CreatedAt: string
      UpdatedAt: string
      OwnerType: string
      OwnerId: string
      Kind: string
      Status: string
      QueueName: string
      Executor: string
      RequestPayload: Dictionary<string, obj>
      ResultPayload: Dictionary<string, obj>
      Error: string }

[<CLIMutable>]
type WorkerHeartbeat =
    { WorkerId: string
      WorkerKind: string
      Backend: string
      Executor: string
      Hostname: string
      CreatedAt: string
      UpdatedAt: string
      Notes: string }

[<CLIMutable>]
type ArtifactRef =
    { ArtifactId: string
      CreatedAt: string
      OwnerType: string
      OwnerId: string
      Name: string
      Kind: string
      Path: string
      Uri: string option
      Description: string
      ContentType: string
      Bytes: Nullable<int> }

[<CLIMutable>]
type BenchmarkRunRecord =
    { RunId: string
      JobId: string option
      CreatedAt: string
      DatasetVersion: string
      PipelineVersion: string
      LanguageStack: string
      ModelFamily: string
      ComputeBudget: Dictionary<string, obj>
      Metrics: Dictionary<string, obj>
      Comparisons: Dictionary<string, obj>
      StratifiedMetrics: Dictionary<string, obj>
      Leaderboard: Dictionary<string, obj>
      FairnessChecks: ResizeArray<string> }

[<CLIMutable>]
type CampaignBenchmarkSpec =
    { Label: string
      Request: BenchmarkRequest }

[<CLIMutable>]
type CampaignEntry =
    { EntryId: string
      Label: string
      Request: BenchmarkRequest
      JobId: string option
      Status: string
      BenchmarkRunId: string option
      Error: string }

[<CLIMutable>]
type CampaignRequest =
    { Name: string
      Objective: string
      BenchmarkSpecs: ResizeArray<CampaignBenchmarkSpec>
      Notes: string
      ProgramId: string option }

[<CLIMutable>]
type CampaignFollowUpProposal =
    { ProposalId: string
      Title: string
      Rationale: string
      BenchmarkSpecs: ResizeArray<CampaignBenchmarkSpec> }

[<CLIMutable>]
type ExperimentSuccessCriterion =
    { Metric: string
      Comparator: string
      Target: string
      Rationale: string }

[<CLIMutable>]
type CampaignExperimentPlan =
    { PlanId: string
      Title: string
      Hypothesis: string
      TargetCohort: string
      Rationale: string
      RequiredBaselines: ResizeArray<string>
      SuccessCriteria: ResizeArray<ExperimentSuccessCriterion>
      BenchmarkSpecs: ResizeArray<CampaignBenchmarkSpec>
      RecommendedAgentRequest: Dictionary<string, obj> }

[<CLIMutable>]
type CampaignExperimentPlanReport =
    { PlanId: string
      Title: string
      AcceptanceStatus: string
      LaunchReady: bool
      TargetCohort: string
      CurrentBestRunId: string option
      CurrentLeadingModel: string option
      BaselineControl: string
      AcceptanceCriteria: ResizeArray<ExperimentSuccessCriterion>
      AcceptanceNotes: ResizeArray<string> }

[<CLIMutable>]
type CampaignRecord =
    { CampaignId: string
      CreatedAt: string
      UpdatedAt: string
      Name: string
      Objective: string
      Kind: string
      ProgramId: string option
      Status: string
      BenchmarkSpecs: ResizeArray<CampaignEntry>
      Notes: string
      Summary: Dictionary<string, obj> }

[<CLIMutable>]
type ProgramRequest =
    { Name: string
      Objective: string
      Hypothesis: string
      Notes: string
      CampaignIds: ResizeArray<string> }

[<CLIMutable>]
type ProgramCampaignAttachRequest =
    { CampaignId: string }

[<CLIMutable>]
type ProgramRecord =
    { ProgramId: string
      CreatedAt: string
      UpdatedAt: string
      Name: string
      Objective: string
      Hypothesis: string
      Notes: string
      Status: string
      CampaignIds: ResizeArray<string>
      Summary: Dictionary<string, obj> }

[<CLIMutable>]
type PortfolioCampaignSummary =
    { CampaignId: string
      Name: string
      Status: string
      TotalRuns: int
      CompletedRuns: int
      FailedRuns: int
      BestRunId: string option
      LeadingModel: string option
      SounioIslesDice: float
      SounioAuc: float
      SounioAspectsMae: float
      ReadyPlanCount: int
      WatchPlanCount: int
      BlockedPlanCount: int
      TopPlanId: string option }

[<CLIMutable>]
type PortfolioReport =
    { PortfolioId: string
      GeneratedAt: string
      TotalCampaigns: int
      CompletedCampaigns: int
      RunningCampaigns: int
      FailedCampaigns: int
      LeadingCampaignId: string option
      Campaigns: ResizeArray<PortfolioCampaignSummary>
      NextActions: ResizeArray<string> }

[<CLIMutable>]
type PortfolioProgramSummary =
    { ProgramId: string
      Name: string
      Status: string
      TotalCampaigns: int
      CompletedCampaigns: int
      RunningCampaigns: int
      FailedCampaigns: int
      LeadingCampaignId: string option
      LeadingModel: string option
      SounioIslesDice: float
      SounioAuc: float
      SounioAspectsMae: float
      ReadyPlanCount: int
      WatchPlanCount: int
      BlockedPlanCount: int }

[<CLIMutable>]
type ProgramPortfolioReport =
    { PortfolioId: string
      GeneratedAt: string
      TotalPrograms: int
      ActivePrograms: int
      CompletedPrograms: int
      BlockedPrograms: int
      LeadingProgramId: string option
      Programs: ResizeArray<PortfolioProgramSummary>
      NextActions: ResizeArray<string> }

[<CLIMutable>]
type RenderedK8sJobSummary =
    { Kind: string
      JobKind: string
      EntryPoint: string
      Args: ResizeArray<string>
      K8sNamespace: string
      LocalQueue: string
      ClusterQueue: string option
      SharedDatasetPath: string
      ArtifactPrefix: string
      ApiImage: string
      WorkerImage: string
      DatasetManifestPath: string option
      ExternalTestManifestPath: string option
      TrainSplit: string option
      TestSplit: string option
      Seed: int option
      KernelPath: string option
      RequireSnio: bool option }

[<CLIMutable>]
type ClusterDeploymentPlanItem =
    { ItemId: string
      Label: string
      SourceKind: string
      SourceId: string
      CurrentStatus: string
      LaunchReady: bool
      AcceptanceStatus: string option
      CampaignId: string
      ProgramId: string option
      RenderedJob: RenderedK8sJobSummary
      Notes: ResizeArray<string> }

[<CLIMutable>]
type CampaignClusterDeploymentPlan =
    { DeploymentPlanId: string
      GeneratedAt: string
      CampaignId: string
      CampaignName: string
      CampaignStatus: string
      ProgramId: string option
      Objective: string
      QueueNamespace: string
      LocalQueue: string
      ClusterQueue: string option
      TotalItems: int
      LaunchReadyItems: int
      BlockedItems: int
      ActiveItems: ResizeArray<ClusterDeploymentPlanItem>
      RecommendedItems: ResizeArray<ClusterDeploymentPlanItem>
      NextActions: ResizeArray<string> }

[<CLIMutable>]
type ProgramClusterDeploymentPlan =
    { DeploymentPlanId: string
      GeneratedAt: string
      ProgramId: string
      ProgramName: string
      ProgramStatus: string
      Objective: string
      Hypothesis: string
      LeadingCampaignId: string option
      QueueNamespace: string
      LocalQueue: string
      ClusterQueue: string option
      CampaignCount: int
      LaunchReadyItems: int
      BlockedItems: int
      CampaignPlans: ResizeArray<CampaignClusterDeploymentPlan>
      NextActions: ResizeArray<string> }

[<CLIMutable>]
type ClusterLaunchRequest =
    { IncludeActiveItems: bool
      IncludeRecommendedItems: bool
      OnlyLaunchReady: bool }

[<CLIMutable>]
type ClusterLaunchReceiptItem =
    { ItemId: string
      Label: string
      SourceKind: string
      SourceId: string
      CampaignId: string
      ProgramId: string option
      DispatchJobId: string
      TargetJobId: string option
      DispatchStatus: string
      QueueName: string
      RenderedJob: RenderedK8sJobSummary }

[<CLIMutable>]
type ClusterLaunchReceipt =
    { LaunchId: string
      CreatedAt: string
      ScopeKind: string
      ScopeId: string
      ItemCount: int
      SkippedCount: int
      Items: ResizeArray<ClusterLaunchReceiptItem>
      Notes: ResizeArray<string> }

[<CLIMutable>]
type K8sDispatchResult =
    { ClusterJobName: string option
      Namespace: string
      LocalQueue: string
      ClusterQueue: string option
      Status: string
      ClusterPhase: string
      Submitted: bool
      Cancelled: bool
      KueueAdmitted: bool option
      KueueFinished: bool option
      Diagnostics: ResizeArray<string> }

[<CLIMutable>]
type AgentRunRequest =
    { Objective: string
      Surface: string
      DatasetManifestPath: string option
      ExternalTestManifestPath: string option
      TrainSplit: string
      TestSplit: string
      Seed: int
      StudyId: string option
      StudyFilePaths: ResizeArray<string>
      IncludeBaselineComparison: bool
      ModelFamily: string
      ResearchQuestion: string option
      Notes: string }

[<CLIMutable>]
type AgentRunRecord =
    { RunId: string
      JobId: string option
      CreatedAt: string
      UpdatedAt: string
      Objective: string
      Surface: string
      Status: string
      RootAgent: string
      TraceId: string
      SessionId: string
      SafetyPolicy: SafetyPolicy
      FinalOutput: string
      Error: string
      DatasetManifestPath: string option
      ExternalTestManifestPath: string option
      BenchmarkRunId: string option
      StudyId: string option
      ResearchBriefId: string option
      ArtifactPaths: ResizeArray<string>
      Warnings: ResizeArray<string> }

[<CLIMutable>]
type SounioRuntimeProbeReport =
    { Detected: bool
      Usable: bool
      Source: string
      SounioRoot: string option
      NativeBackendSourceRoot: string option
      SnioEmbedHeaderPath: string option
      SnioProtocolPath: string option
      SnioFsharpProtocolPath: string option
      GitRoot: string option
      SoucPath: string option
      RuntimeLibraryPath: string option
      StdlibPath: string option
      ProbeProgramPath: string option
      Version: string option
      RemoteUrl: string option
      OfficialRemote: bool option
      WorktreeClean: bool option
      BinaryVersionJsonAvailable: bool
      SourceVersionJsonAvailable: bool
      NativeFfiDetected: bool
      NativeFfiUsable: bool
      NativeProbeSucceeded: bool
      NativeKernelExecutionAvailable: bool
      SnioEmbeddingDetected: bool
      SnioEmbeddingUsable: bool
      KernelSessionProtocolAvailable: bool
      SnioServerProbeAttempted: bool
      SnioServerProbeSucceeded: bool
      SnioBuiltInServeSupported: bool option
      SnioServeEntryPath: string option
      SnioInfoValues: ResizeArray<int64>
      SnioCapabilities: int64 option
      SnioHealthOk: bool option
      SnioStatsValue: int64 option
      NativeProbeOutput: ResizeArray<string>
      RuntimeTensorFfiSymbols: ResizeArray<string>
      RuntimeUncertainFfiSymbols: ResizeArray<string>
      RuntimeOdeFfiSymbols: ResizeArray<string>
      RuntimeAutodiffFfiSymbols: ResizeArray<string>
      NativeTensorFfiSymbols: ResizeArray<string>
      NativeUncertainFfiSymbols: ResizeArray<string>
      NativeOdeFfiSymbols: ResizeArray<string>
      NativeAutodiffFfiSymbols: ResizeArray<string>
      SourceOnlyScientificFfiSymbols: ResizeArray<string>
      CheckSucceeded: bool
      RunSucceeded: bool
      ProbeOutput: ResizeArray<string>
      Diagnostics: ResizeArray<string> }

[<CLIMutable>]
type SounioKernelExecutionRequest =
    { Label: string
      KernelPath: string option
      PersistArtifacts: bool
      RequireSnio: bool }

[<CLIMutable>]
type SounioKernelExecutionResult =
    { ExecutionId: string
      JobId: string
      CreatedAt: string
      Label: string
      KernelPath: string
      Status: string
      CheckSucceeded: bool
      RunSucceeded: bool
      SnioAttempted: bool
      SnioUsed: bool
      Output: ResizeArray<string>
      Diagnostics: ResizeArray<string>
      ArtifactIds: ResizeArray<string>
      Runtime: SounioRuntimeProbeReport }

[<CLIMutable>]
type SounioRuntimeAbiReport =
    { Detected: bool
      LibraryPath: string option
      SourceRoot: string option
      SnioEmbedHeaderPath: string option
      SnioProtocolPath: string option
      SnioFsharpProtocolPath: string option
      ExportCount: int
      SourceExportCount: int
      BinaryVersionJsonAvailable: bool
      SourceVersionJsonAvailable: bool
      ExportedSymbols: ResizeArray<string>
      DispatchSymbols: ResizeArray<string>
      HandlerSymbols: ResizeArray<string>
      IntrinsicSymbols: ResizeArray<string>
      KnowledgeSymbols: ResizeArray<string>
      BootstrapSymbols: ResizeArray<string>
      CvSymbols: ResizeArray<string>
      MathSymbols: ResizeArray<string>
      MemorySymbols: ResizeArray<string>
      RuntimeTensorSymbols: ResizeArray<string>
      RuntimeUncertainSymbols: ResizeArray<string>
      RuntimeOdeSymbols: ResizeArray<string>
      RuntimeAutodiffSymbols: ResizeArray<string>
      TensorSymbols: ResizeArray<string>
      UncertainSymbols: ResizeArray<string>
      OdeSymbols: ResizeArray<string>
      AutodiffSymbols: ResizeArray<string>
      SourceOnlyScientificSymbols: ResizeArray<string>
      KernelExecutionSymbols: ResizeArray<string>
      NativeKernelExecutionAvailable: bool
      KernelSessionProtocolAvailable: bool
      Notes: ResizeArray<string> }

module Defaults =
    let safetyPolicy () =
        { PrivacyMode = "local_deidentified"
          AutonomyMode = "autonomous_lab"
          RemoteToolPolicy = "public_only_deidentified"
          WriteScope = ResizeArray()
          PublicToolAllowlist = ResizeArray()
          LocalToolAllowlist = ResizeArray() }

    let benchmarkRequest manifestPath =
        { DatasetManifestPath = manifestPath
          ExternalTestManifestPath = None
          TrainSplit = "train"
          TestSplit = "test"
          Seed = 13 }

    let benchmarkRun runId datasetVersion =
        { RunId = runId
          JobId = None
          CreatedAt = DateTime.UtcNow.ToString("O")
          DatasetVersion = datasetVersion
          PipelineVersion = "rewrite-parity-v1"
          LanguageStack = "sounio+fsharp"
          ModelFamily = "sounio_hypercomplex"
          ComputeBudget = Dictionary()
          Metrics = Dictionary()
          Comparisons = Dictionary()
          StratifiedMetrics = Dictionary()
          Leaderboard = Dictionary()
          FairnessChecks = ResizeArray() }

    let agentRequest manifestPath =
        { Objective = "Freeze an agent-run contract for the rewrite lane."
          Surface = "research"
          DatasetManifestPath = Some manifestPath
          ExternalTestManifestPath = None
          TrainSplit = "train"
          TestSplit = "test"
          Seed = 13
          StudyId = None
          StudyFilePaths = ResizeArray()
          IncludeBaselineComparison = true
          ModelFamily = "sounio_hypercomplex"
          ResearchQuestion = Some "What evidence must stay stable during the rewrite?"
          Notes = "Generated from the F# contracts lane." }

    let campaignRequest name manifestPath =
        { Name = name
          Objective = "Establish parity for campaign orchestration in the F# rewrite lane."
          BenchmarkSpecs =
              ResizeArray(
                  [ { Label = "seed-13"
                      Request = benchmarkRequest manifestPath } ]
              )
          Notes = "Generated from the F# contracts lane."
          ProgramId = None }
