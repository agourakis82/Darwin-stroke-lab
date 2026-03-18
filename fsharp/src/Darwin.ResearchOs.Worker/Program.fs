namespace Darwin.ResearchOs.Worker

open System
open Darwin.ResearchOs.Core
open Darwin.ResearchOs.Storage
open Microsoft.Extensions.DependencyInjection
open Microsoft.Extensions.Hosting

module Program =

    let private runBenchmarkJob jobId =
        let services = RuntimeServiceSet.create ()
        let worker = BenchmarkWorker(services.ResearchStore, ParityBenchmark.createRunner services.ResearchStore services.ObjectStore, services.StateMode)

        match worker.RunJob(jobId) with
        | Some job when job.Status = "completed" -> 0
        | Some job ->
            eprintfn "Benchmark job %s finished with status=%s error=%s" job.JobId job.Status job.Error
            1
        | None ->
            eprintfn "Benchmark job %s was not found or is not a benchmark job." jobId
            1

    let private runSounioRuntimeJob jobId =
        let services = RuntimeServiceSet.create ()
        let runner =
            { new ISounioKernelJobRunner with
                member _.Execute(job, request) = SounioRuntimeProbe.executeKernelForJob job request }
        let worker = SounioKernelWorker(services.ResearchStore, services.ObjectStore, runner, services.StateMode)

        match worker.RunJob(jobId) with
        | Some job when job.Status = "completed" -> 0
        | Some job ->
            eprintfn "Sounio runtime job %s finished with status=%s error=%s" job.JobId job.Status job.Error
            1
        | None ->
            eprintfn "Sounio runtime job %s was not found or is not a sounio_runtime job." jobId
            1

    [<EntryPoint>]
    let main args =
        match Array.toList args with
        | [ "run-benchmark-job"; "--job-id"; jobId ] -> runBenchmarkJob jobId
        | [ "run-sounio-runtime-job"; "--job-id"; jobId ] -> runSounioRuntimeJob jobId
        | _ ->
            let builder = Host.CreateApplicationBuilder(args)
            builder.Services.AddHostedService<Worker>() |> ignore
            builder.Build().Run()
            0
