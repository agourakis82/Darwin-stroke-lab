namespace Darwin.ResearchOs.Core

open Darwin.ResearchOs.Contracts

[<CLIMutable>]
type StoredObjectDescriptor =
    { LocalPath: string
      Uri: string option
      Bytes: int option
      ContentType: string option }

type RewriteWave =
    | ContractsParity
    | PlatformCore
    | ControlPlane
    | StrokeLab

type RewriteLaneState =
    { CurrentWave: RewriteWave
      ContractsFrozen: bool
      FsharpApiSkeletonReady: bool
      StanLaneReady: bool
      PythonCompatibilityRequired: bool
      Notes: string list }

type IContractSnapshotProvider =
    abstract member OpenApiPath: string
    abstract member GoldenFixtureRoot: string
    abstract member ComparisonRegistryPath: string

type IStateStore =
    abstract member Mode: string
    abstract member ConnectionDescriptor: string

type IObjectStore =
    abstract member Mode: string
    abstract member RootDescriptor: string
    abstract member WriteText: relativePath: string * content: string -> StoredObjectDescriptor
    abstract member AppendText: relativePath: string * content: string -> StoredObjectDescriptor
    abstract member WriteJson: relativePath: string * payload: System.Collections.Generic.Dictionary<string, obj> -> StoredObjectDescriptor

type IResearchStore =
    abstract member SaveJob: JobRecord -> unit
    abstract member TryGetJob: string -> JobRecord option
    abstract member ListJobs: unit -> ResizeArray<JobRecord>
    abstract member UpdateJob: string * (JobRecord -> JobRecord) -> JobRecord option
    abstract member ClaimNextJob: string option -> JobRecord option
    abstract member RequeueInflightJobs: string option -> unit
    abstract member SaveBenchmarkRun: BenchmarkRunRecord -> unit
    abstract member TryGetBenchmarkRun: string -> BenchmarkRunRecord option
    abstract member ListBenchmarkRuns: unit -> ResizeArray<BenchmarkRunRecord>
    abstract member SaveAgentRun: AgentRunRecord -> unit
    abstract member TryGetAgentRun: string -> AgentRunRecord option
    abstract member ListAgentRuns: unit -> ResizeArray<AgentRunRecord>
    abstract member SaveCampaign: CampaignRecord -> unit
    abstract member TryGetCampaign: string -> CampaignRecord option
    abstract member ListCampaigns: unit -> ResizeArray<CampaignRecord>
    abstract member SaveProgram: ProgramRecord -> unit
    abstract member TryGetProgram: string -> ProgramRecord option
    abstract member ListPrograms: unit -> ResizeArray<ProgramRecord>
    abstract member SaveArtifact: ArtifactRef -> unit
    abstract member ListArtifacts: unit -> ResizeArray<ArtifactRef>
    abstract member SaveWorkerHeartbeat: WorkerHeartbeat -> unit
    abstract member ListWorkerHeartbeats: string option -> ResizeArray<WorkerHeartbeat>

type IJobScheduler =
    abstract member Name: string
    abstract member SupportsKueue: bool
    abstract member SupportsCancellation: bool

type IStrokeKernelAdapter =
    abstract member KernelName: string
    abstract member UsesOfficialSounioRuntime: bool
    abstract member BaselineProbabilisticLane: string

module RewriteStatus =
    let current () =
        { CurrentWave = PlatformCore
          ContractsFrozen = true
          FsharpApiSkeletonReady = true
          StanLaneReady = true
          PythonCompatibilityRequired = true
          Notes =
              [ "Python remains the source of truth until F# reaches parity."
                "Sounio remains the scientific kernel."
                "Stan is the probabilistic baseline lane, not the control-plane owner."
                "Platform-core parity for campaigns, programs, and portfolio is now in progress." ] }

module KernelSelection =
    let describe (adapter: IStrokeKernelAdapter) =
        $"Kernel={adapter.KernelName}; OfficialRuntime={adapter.UsesOfficialSounioRuntime}; ProbabilisticBaseline={adapter.BaselineProbabilisticLane}"

module ContractHints =
    let parityTargets () =
        [ "JobRecord"
          "ArtifactRef"
          "BenchmarkRun"
          "CampaignRecord"
          "ProgramRecord"
          "PortfolioReport"
          "ProgramPortfolioReport"
          "AgentRun" ]
