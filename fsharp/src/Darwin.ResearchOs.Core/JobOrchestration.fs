namespace Darwin.ResearchOs.Core

open System
open System.Collections.Generic
open System.Threading
open Darwin.ResearchOs.Contracts

module internal JobValues =
    let dict (pairs: seq<string * obj>) : Dictionary<string, obj> =
        let target = Dictionary<string, obj>()
        for (key, value) in pairs do
            target[key] <- value
        target

    let asString (value: obj) =
        match value with
        | null -> None
        | :? string as text when not (String.IsNullOrWhiteSpace(text)) -> Some text
        | _ -> None

    let asBool (value: obj) =
        match value with
        | null -> None
        | :? bool as value -> Some value
        | :? string as text ->
            match Boolean.TryParse(text) with
            | true, parsed -> Some parsed
            | _ -> None
        | _ -> None

    let asInt (value: obj) =
        match value with
        | null -> None
        | :? int as v -> Some v
        | :? int64 as v -> Some(int v)
        | :? int16 as v -> Some(int v)
        | :? uint32 as v -> Some(int v)
        | :? float as v -> Some(int v)
        | :? decimal as v -> Some(int v)
        | :? string as text ->
            match Int32.TryParse(text) with
            | true, parsed -> Some parsed
            | _ -> None
        | _ -> None

type JobBackendDescriptor =
    { Name: string
      ExecutionMode: string
      SupportsCluster: bool
      Notes: string }

type IBenchmarkJobRunner =
    abstract member Execute: job: JobRecord * request: BenchmarkRequest -> BenchmarkRunRecord

type IAgentRunJobRunner =
    abstract member Execute: job: JobRecord * run: AgentRunRecord -> AgentRunRecord

type ISounioKernelJobRunner =
    abstract member Execute: job: JobRecord * request: SounioKernelExecutionRequest -> SounioKernelExecutionResult

type IK8sDispatchRunner =
    abstract member Submit: job: JobRecord -> K8sDispatchResult
    abstract member Poll: job: JobRecord -> K8sDispatchResult
    abstract member Cancel: job: JobRecord -> K8sDispatchResult

type LocalParityJobBackend(store: IResearchStore, ?workerTtl: TimeSpan) =
    let ttl = defaultArg workerTtl (TimeSpan.FromSeconds(15.0))

    member _.Descriptor =
        { Name = "local"
          ExecutionMode = "queued-worker"
          SupportsCluster = false
          Notes = "Parity local backend for the F# rewrite lane." }

    member _.SubmitBenchmark(request: BenchmarkRequest) =
        let runId = Guid.NewGuid().ToString("N")
        let now = DateTime.UtcNow.ToString("O")
        let job =
            { JobId = Guid.NewGuid().ToString("N")
              CreatedAt = now
              UpdatedAt = now
              OwnerType = "benchmark_run"
              OwnerId = runId
              Kind = "benchmark"
              Status = "queued"
              QueueName = "local-benchmark"
              Executor = "fsharp-benchmark-worker"
              RequestPayload =
                  JobValues.dict
                      [ "dataset_manifest_path", box request.DatasetManifestPath
                        "external_test_manifest_path", box (defaultArg request.ExternalTestManifestPath "")
                        "train_split", box request.TrainSplit
                        "test_split", box request.TestSplit
                        "seed", box request.Seed ]
              ResultPayload =
                  JobValues.dict
                      [ "backend", box "local"
                        "artifact_prefix", box $"{runId}/"
                        "cancel_requested", box false ]
              Error = "" }
        store.SaveJob(job)
        job

    member _.SubmitAgentRun(run: AgentRunRecord) =
        store.SaveAgentRun(run)
        let now = DateTime.UtcNow.ToString("O")
        let job =
            { JobId = Guid.NewGuid().ToString("N")
              CreatedAt = now
              UpdatedAt = now
              OwnerType = "agent_run"
              OwnerId = run.RunId
              Kind = "agent_run"
              Status = "queued"
              QueueName = "local-agent"
              Executor = "fsharp-agent-worker"
              RequestPayload = JobValues.dict [ "run_id", box run.RunId; "surface", box run.Surface ]
              ResultPayload =
                  JobValues.dict
                      [ "backend", box "local"
                        "artifact_prefix", box $"{run.RunId}/"
                        "cancel_requested", box false
                        "agent_run_id", box run.RunId ]
              Error = "" }
        store.SaveJob(job)
        job

    member _.SubmitSounioKernel(request: SounioKernelExecutionRequest) =
        let executionId = Guid.NewGuid().ToString("N")
        let now = DateTime.UtcNow.ToString("O")
        let label =
            if String.IsNullOrWhiteSpace(request.Label) then
                "Sounio Kernel Execution"
            else
                request.Label
        let job =
            { JobId = Guid.NewGuid().ToString("N")
              CreatedAt = now
              UpdatedAt = now
              OwnerType = "sounio_kernel_run"
              OwnerId = executionId
              Kind = "sounio_runtime"
              Status = "queued"
              QueueName = "local-sounio-runtime"
              Executor = "fsharp-sounio-worker"
              RequestPayload =
                  JobValues.dict
                      [ "label", box label
                        "kernel_path", box (defaultArg request.KernelPath "")
                        "persist_artifacts", box request.PersistArtifacts
                        "require_snio", box request.RequireSnio ]
              ResultPayload =
                  JobValues.dict
                      [ "backend", box "local"
                        "artifact_prefix", box $"{executionId}/"
                        "cancel_requested", box false
                        "snio_attempted", box false
                        "snio_used", box false
                        "execution_id", box executionId ]
              Error = "" }
        store.SaveJob(job)
        job

    member _.GetJob(jobId: string) =
        store.TryGetJob(jobId)

    member _.CancelJob(jobId: string) =
        let update current =
            let payload = Dictionary<string, obj>(current.ResultPayload)
            payload["cancel_requested"] <- box true
            if current.Status = "queued" then
                { current with
                    Status = "cancelled"
                    UpdatedAt = DateTime.UtcNow.ToString("O")
                    ResultPayload = payload
                    Error = "Cancellation requested before local execution started." }
            elif current.Status = "running" then
                { current with
                    UpdatedAt = DateTime.UtcNow.ToString("O")
                    ResultPayload = payload
                    Error = "Cancellation requested after local execution started; this slice does not preempt active runs." }
            else
                current

        store.UpdateJob(jobId, update)

    member _.WaitForJob(jobId: string, timeout: TimeSpan, pollInterval: TimeSpan) =
        let deadline = DateTime.UtcNow + timeout
        let rec loop () =
            match store.TryGetJob(jobId) with
            | Some job when job.Status = "completed" || job.Status = "failed" || job.Status = "cancelled" -> Some job
            | _ when DateTime.UtcNow >= deadline -> None
            | _ ->
                Thread.Sleep(pollInterval)
                loop ()
        loop ()

    member _.EnsureWorkerAvailable(workerKind: string) =
        let cutoff = DateTime.UtcNow - ttl
        store.ListWorkerHeartbeats(Some workerKind)
        |> Seq.exists (fun heartbeat ->
            match DateTime.TryParse(heartbeat.UpdatedAt) with
            | true, updated -> updated.ToUniversalTime() >= cutoff
            | _ -> false)

type BenchmarkWorker(store: IResearchStore, runner: IBenchmarkJobRunner, backendName: string, ?hostname: string) =
    let workerSuffix = Guid.NewGuid().ToString("N").Substring(0, 12)
    let workerId = $"benchmark-worker-{workerSuffix}"
    let host = defaultArg hostname Environment.MachineName

    member private _.Heartbeat() =
        let now = DateTime.UtcNow.ToString("O")
        store.SaveWorkerHeartbeat(
            { WorkerId = workerId
              WorkerKind = "benchmark"
              Backend = backendName
              Executor = "fsharp-benchmark-worker"
              Hostname = host
              CreatedAt = now
              UpdatedAt = now
              Notes = "F# parity benchmark worker heartbeat." }
        )

    member private _.RequestOf(job: JobRecord) =
        { DatasetManifestPath = unbox<string> job.RequestPayload["dataset_manifest_path"]
          ExternalTestManifestPath =
              match job.RequestPayload.TryGetValue("external_test_manifest_path") with
              | true, (:? string as value) when not (String.IsNullOrWhiteSpace value) -> Some value
              | _ -> None
          TrainSplit = unbox<string> job.RequestPayload["train_split"]
          TestSplit = unbox<string> job.RequestPayload["test_split"]
          Seed = job.RequestPayload["seed"] |> JobValues.asInt |> Option.defaultValue 13 }

    member private this.ExecuteJob(job: JobRecord) =
        let request = this.RequestOf(job)

        try
            let run = runner.Execute(job, request)
            store.SaveBenchmarkRun(run)
            let payload = Dictionary<string, obj>(job.ResultPayload)
            payload["benchmark_run_id"] <- box run.RunId
            payload["dataset_version"] <- box run.DatasetVersion
            payload["backend"] <- box backendName
            payload["cancel_requested"] <-
                if payload.ContainsKey("cancel_requested") then payload["cancel_requested"] else box false
            store.UpdateJob(
                job.JobId,
                fun current ->
                    { current with
                        Status = "completed"
                        UpdatedAt = DateTime.UtcNow.ToString("O")
                        ResultPayload = payload }
            )
            |> ignore
        with ex ->
            let payload = Dictionary<string, obj>(job.ResultPayload)
            payload["backend"] <- box backendName
            store.UpdateJob(
                job.JobId,
                fun current ->
                    { current with
                        Status = "failed"
                        UpdatedAt = DateTime.UtcNow.ToString("O")
                        ResultPayload = payload
                        Error = ex.Message }
            )
            |> ignore

        store.TryGetJob(job.JobId)

    member this.RunJob(jobId: string) =
        this.Heartbeat()
        match store.TryGetJob(jobId) with
        | None -> None
        | Some job when job.Kind <> "benchmark" && job.Kind <> "benchmark_remote" -> None
        | Some job when job.Status = "completed" || job.Status = "failed" || job.Status = "cancelled" -> Some job
        | Some job ->
            let runningJob =
                if job.Status = "queued" then
                    store.UpdateJob(
                        jobId,
                        fun current ->
                            { current with
                                Status = "running"
                                UpdatedAt = DateTime.UtcNow.ToString("O") }
                    )
                    |> Option.defaultValue job
                else
                    job
            this.ExecuteJob(runningJob)

    member this.RunOnce() =
        this.Heartbeat()
        match store.ClaimNextJob(Some "benchmark") with
        | None -> None
        | Some job -> this.ExecuteJob(job)

type AgentRunWorker(store: IResearchStore, runner: IAgentRunJobRunner, backendName: string, ?hostname: string) =
    let workerSuffix = Guid.NewGuid().ToString("N").Substring(0, 12)
    let workerId = $"agent-worker-{workerSuffix}"
    let host = defaultArg hostname Environment.MachineName

    member private _.Heartbeat() =
        let now = DateTime.UtcNow.ToString("O")
        store.SaveWorkerHeartbeat(
            { WorkerId = workerId
              WorkerKind = "agent_run"
              Backend = backendName
              Executor = "fsharp-agent-worker"
              Hostname = host
              CreatedAt = now
              UpdatedAt = now
              Notes = "F# parity agent worker heartbeat." }
        )

    member this.RunOnce() =
        this.Heartbeat()
        match store.ClaimNextJob(Some "agent_run") with
        | None -> None
        | Some job ->
            match store.TryGetAgentRun(job.OwnerId) with
            | None ->
                store.UpdateJob(
                    job.JobId,
                    fun current ->
                        { current with
                            Status = "failed"
                            UpdatedAt = DateTime.UtcNow.ToString("O")
                            Error = $"Missing agent run {job.OwnerId}" }
                )
                |> ignore
                store.TryGetJob(job.JobId)
            | Some run ->
                try
                    let completed = runner.Execute(job, run)
                    store.SaveAgentRun(completed)
                    let payload = Dictionary<string, obj>(job.ResultPayload)
                    payload["agent_run_id"] <- box completed.RunId
                    payload["backend"] <- box backendName
                    payload["cancel_requested"] <-
                        if payload.ContainsKey("cancel_requested") then payload["cancel_requested"] else box false
                    store.UpdateJob(
                        job.JobId,
                        fun current ->
                            { current with
                                Status = completed.Status
                                UpdatedAt = DateTime.UtcNow.ToString("O")
                                ResultPayload = payload
                                Error = completed.Error }
                    )
                    |> ignore
                with ex ->
                    store.UpdateJob(
                        job.JobId,
                        fun current ->
                            { current with
                                Status = "failed"
                                UpdatedAt = DateTime.UtcNow.ToString("O")
                                Error = ex.Message }
                    )
                    |> ignore

                store.TryGetJob(job.JobId)

type SounioKernelWorker(store: IResearchStore, objectStore: IObjectStore, runner: ISounioKernelJobRunner, backendName: string, ?hostname: string) =
    let workerSuffix = Guid.NewGuid().ToString("N").Substring(0, 12)
    let workerId = $"sounio-worker-{workerSuffix}"
    let host = defaultArg hostname Environment.MachineName

    member private _.Heartbeat() =
        let now = DateTime.UtcNow.ToString("O")
        store.SaveWorkerHeartbeat(
            { WorkerId = workerId
              WorkerKind = "sounio_runtime"
              Backend = backendName
              Executor = "fsharp-sounio-worker"
              Hostname = host
              CreatedAt = now
              UpdatedAt = now
              Notes = "F# Sounio runtime worker heartbeat." }
        )

    member private _.RequestOf(job: JobRecord) =
        let label =
            match job.RequestPayload.TryGetValue("label") with
            | true, value -> JobValues.asString value |> Option.defaultValue "Sounio Kernel Execution"
            | _ -> "Sounio Kernel Execution"

        { Label = label
          KernelPath =
              match job.RequestPayload.TryGetValue("kernel_path") with
              | true, value -> JobValues.asString value
              | _ -> None
          PersistArtifacts =
              match job.RequestPayload.TryGetValue("persist_artifacts") with
              | true, value -> JobValues.asBool value |> Option.defaultValue true
              | _ -> true
          RequireSnio =
              match job.RequestPayload.TryGetValue("require_snio") with
              | true, value -> JobValues.asBool value |> Option.defaultValue false
              | _ -> false }

    member private this.ExecuteJob(job: JobRecord) =
        let request = this.RequestOf(job)

        try
            let completed = runner.Execute(job, request)
            let persisted =
                SounioRuntimeProbe.persistKernelExecution store objectStore backendName "fsharp-sounio-worker" request completed
            let payload =
                match store.TryGetJob(job.JobId) with
                | Some current -> Dictionary<string, obj>(current.ResultPayload)
                | None -> Dictionary<string, obj>()
            payload["execution_id"] <- box persisted.ExecutionId
            payload["backend"] <- box backendName
            payload["snio_attempted"] <- box persisted.SnioAttempted
            payload["snio_used"] <- box persisted.SnioUsed
            store.UpdateJob(
                job.JobId,
                fun current ->
                    { current with
                        Status = persisted.Status
                        UpdatedAt = DateTime.UtcNow.ToString("O")
                        ResultPayload = payload
                        Error = if persisted.Status = "completed" then "" elif persisted.Diagnostics.Count > 0 then persisted.Diagnostics[0] else current.Error }
            )
            |> ignore
        with ex ->
            store.UpdateJob(
                job.JobId,
                fun current ->
                    { current with
                        Status = "failed"
                        UpdatedAt = DateTime.UtcNow.ToString("O")
                        Error = ex.Message }
            )
            |> ignore

        store.TryGetJob(job.JobId)

    member this.RunJob(jobId: string) =
        this.Heartbeat()
        match store.TryGetJob(jobId) with
        | None -> None
        | Some job when job.Kind <> "sounio_runtime" && job.Kind <> "sounio_runtime_remote" -> None
        | Some job when job.Status = "completed" || job.Status = "failed" || job.Status = "cancelled" -> Some job
        | Some job ->
            let runningJob =
                if job.Status = "queued" then
                    store.UpdateJob(
                        jobId,
                        fun current ->
                            { current with
                                Status = "running"
                                UpdatedAt = DateTime.UtcNow.ToString("O") }
                    )
                    |> Option.defaultValue job
                else
                    job
            this.ExecuteJob(runningJob)

    member this.RunOnce() =
        this.Heartbeat()
        match store.ClaimNextJob(Some "sounio_runtime") with
        | None -> None
        | Some job -> this.ExecuteJob(job)

type K8sDispatchWorker(store: IResearchStore, runner: IK8sDispatchRunner, backendName: string, ?hostname: string) =
    let workerSuffix = Guid.NewGuid().ToString("N").Substring(0, 12)
    let workerId = $"k8s-dispatch-worker-{workerSuffix}"
    let host = defaultArg hostname Environment.MachineName

    member private _.Heartbeat() =
        let now = DateTime.UtcNow.ToString("O")
        store.SaveWorkerHeartbeat(
            { WorkerId = workerId
              WorkerKind = "k8s_dispatch"
              Backend = backendName
              Executor = "fsharp-k8s-dispatcher"
              Hostname = host
              CreatedAt = now
              UpdatedAt = now
              Notes = "F# K8s dispatch worker heartbeat." }
        )

    member private _.Persist(job: JobRecord, result: K8sDispatchResult) =
        let payload = Dictionary<string, obj>(job.ResultPayload)
        payload["backend"] <- box backendName
        payload["cluster_phase"] <- box result.ClusterPhase
        payload["submitted"] <- box result.Submitted
        payload["cancelled"] <- box result.Cancelled
        payload["k8s_namespace"] <- box result.Namespace
        payload["local_queue"] <- box result.LocalQueue
        payload["cluster_queue"] <- box (defaultArg result.ClusterQueue "")
        payload["cluster_job_name"] <- box (defaultArg result.ClusterJobName "")
        payload["kueue_admitted"] <- box (defaultArg result.KueueAdmitted false)
        payload["kueue_finished"] <- box (defaultArg result.KueueFinished false)

        store.UpdateJob(
            job.JobId,
            fun current ->
                { current with
                    Status = result.Status
                    UpdatedAt = DateTime.UtcNow.ToString("O")
                    ResultPayload = payload
                    Error = if result.Status = "failed" && result.Diagnostics.Count > 0 then result.Diagnostics[0] else if result.Status = "cancelled" then "" else current.Error }
        )
        |> ignore

        store.TryGetJob(job.JobId)

    member this.RunOnce() =
        this.Heartbeat()

        let runningJob =
            store.ListJobs()
            |> Seq.filter (fun job -> job.Kind = "k8s_dispatch" && job.Status = "running")
            |> Seq.sortBy (fun job -> job.UpdatedAt)
            |> Seq.tryHead

        match runningJob with
        | Some job ->
            let cancelRequested =
                match job.ResultPayload.TryGetValue("cancel_requested") with
                | true, value -> JobValues.asBool value |> Option.defaultValue false
                | _ -> false

            let result =
                if cancelRequested then
                    runner.Cancel(job)
                else
                    runner.Poll(job)

            this.Persist(job, result)
        | None ->
            match store.ClaimNextJob(Some "k8s_dispatch") with
            | None -> None
            | Some job ->
                try
                    let result = runner.Submit(job)
                    this.Persist(job, result)
                with ex ->
                    store.UpdateJob(
                        job.JobId,
                        fun current ->
                            { current with
                                Status = "failed"
                                UpdatedAt = DateTime.UtcNow.ToString("O")
                                Error = ex.Message }
                    )
                    |> ignore

                    store.TryGetJob(job.JobId)
