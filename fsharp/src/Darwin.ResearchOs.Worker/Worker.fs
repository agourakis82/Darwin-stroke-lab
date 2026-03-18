namespace Darwin.ResearchOs.Worker

open System
open System.Threading
open System.Threading.Tasks
open Darwin.ResearchOs.Core
open Darwin.ResearchOs.K8s
open Darwin.ResearchOs.Storage
open Microsoft.Extensions.Hosting
open Microsoft.Extensions.Logging

type Worker(logger: ILogger<Worker>) =
    inherit BackgroundService()

    let services = RuntimeServiceSet.create ()
    let benchmarkWorker = BenchmarkWorker(services.ResearchStore, ParityBenchmark.createRunner services.ResearchStore services.ObjectStore, services.StateMode)
    let sounioKernelRunner =
        { new ISounioKernelJobRunner with
            member _.Execute(job, request) = SounioRuntimeProbe.executeKernelForJob job request }
    let sounioKernelWorker = SounioKernelWorker(services.ResearchStore, services.ObjectStore, sounioKernelRunner, services.StateMode)
    let k8sDispatchRunner = Dispatch.KubectlK8sDispatchRunner() :> IK8sDispatchRunner
    let k8sDispatchWorker = K8sDispatchWorker(services.ResearchStore, k8sDispatchRunner, "k8s")

    let pollDelayMs =
        match Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_WORKER_POLL_MS") with
        | null | "" -> 2000
        | value ->
            match Int32.TryParse(value) with
            | true, parsed when parsed > 0 -> parsed
            | _ -> 2000

    override _.ExecuteAsync(ct: CancellationToken) =
        task {
            let state = RewriteStatus.current ()
            services.ResearchStore.RequeueInflightJobs(Some "benchmark")
            services.ResearchStore.RequeueInflightJobs(Some "sounio_runtime")
            services.ResearchStore.RequeueInflightJobs(Some "k8s_dispatch")
            logger.LogInformation(
                "Darwin worker booted; stateMode={stateMode}; objectMode={objectMode}; dbConfigured={dbConfigured}; wave={wave}",
                services.StateMode,
                services.ObjectMode,
                services.DbConfigured,
                state.CurrentWave
            )
            while not ct.IsCancellationRequested do
                match benchmarkWorker.RunOnce() with
                | Some job ->
                    logger.LogInformation(
                        "Processed benchmark job {jobId}; status={status}; ownerId={ownerId}",
                        job.JobId,
                        job.Status,
                        job.OwnerId
                    )
                | None ->
                    logger.LogDebug(
                        "No queued benchmark jobs at {time}; wave={wave}; contractsFrozen={contractsFrozen}; pythonCompatibilityRequired={compat}",
                        DateTimeOffset.Now,
                        state.CurrentWave,
                        state.ContractsFrozen,
                        state.PythonCompatibilityRequired
                    )

                match sounioKernelWorker.RunOnce() with
                | Some job ->
                    logger.LogInformation(
                        "Processed sounio runtime job {jobId}; status={status}; ownerId={ownerId}",
                        job.JobId,
                        job.Status,
                        job.OwnerId
                    )
                | None ->
                    logger.LogDebug("No queued sounio runtime jobs at {time}", DateTimeOffset.Now)

                match k8sDispatchWorker.RunOnce() with
                | Some job ->
                    logger.LogInformation(
                        "Processed k8s dispatch job {jobId}; status={status}; ownerId={ownerId}; queueName={queueName}",
                        job.JobId,
                        job.Status,
                        job.OwnerId,
                        job.QueueName
                    )
                | None ->
                    logger.LogDebug("No active k8s dispatch work at {time}", DateTimeOffset.Now)

                do! Task.Delay(pollDelayMs, ct)
        }
