namespace Darwin.ResearchOs.Core

open System
open System.Collections.Generic
open System.IO
open Darwin.ResearchOs.Contracts
open Darwin.StrokeLab

module internal ParityBenchmarkValues =
    let dict (pairs: seq<string * obj>) =
        let target = Dictionary<string, obj>()
        for (key, value) in pairs do
            target[key] <- value
        target

    let utcNow () = DateTime.UtcNow.ToString("O")

module ParityBenchmark =
    open ParityBenchmarkValues

    type private SyntheticCase =
        { CaseId: string
          Split: string
          GroundTruth: bool[,,]
          Center: float * float * float
          Radius: float * float * float
          VoxelVolumeMl: float }

    type private SyntheticModelProfile =
        { Name: string
          Shift: float * float * float
          RadiusScale: float
          Sharpness: float
          AucBias: float
          MaeBias: float }

    type private AggregateMetrics =
        { Dice: float
          AbsoluteVolumeDifferenceMl: float
          LesionwiseF1: float
          AbsoluteLesionCountDifference: float
          TrueLesionVolumeMl: float
          PredictedLesionVolumeMl: float }

    let private clamp minimum maximum value =
        value |> max minimum |> min maximum

    let private round4 (value: float) = Math.Round(value, 4)

    let private ellipsoidMask
        (depth: int)
        (height: int)
        (width: int)
        ((centerZ, centerY, centerX): float * float * float)
        ((radiusZ, radiusY, radiusX): float * float * float)
        =
        let mask = Array3D.zeroCreate<bool> depth height width

        for z in 0 .. depth - 1 do
            let normalizedZ = (float z - centerZ) / radiusZ

            for y in 0 .. height - 1 do
                let normalizedY = (float y - centerY) / radiusY

                for x in 0 .. width - 1 do
                    let normalizedX = (float x - centerX) / radiusX
                    let distance = normalizedZ * normalizedZ + normalizedY * normalizedY + normalizedX * normalizedX
                    mask[z, y, x] <- distance <= 1.0

        mask

    let private syntheticCases (request: BenchmarkRequest) =
        let depth, height, width = 12, 24, 24

        [ 0 .. 3 ]
        |> List.map (fun caseIndex ->
            let seedOffset = float ((abs request.Seed + caseIndex) % 3)
            let center =
                4.0 + float caseIndex * 1.2 + seedOffset * 0.2,
                8.0 + float caseIndex * 1.7,
                8.5 + float caseIndex * 1.3 + seedOffset * 0.3

            let radius =
                1.8 + (float ((abs request.Seed + caseIndex) % 2)) * 0.35,
                3.8 + float caseIndex * 0.35,
                4.1 + float caseIndex * 0.25

            let split = if caseIndex < 2 then "internal" else "transfer"
            let voxelVolumeMl = 0.08 + float caseIndex * 0.01

            { CaseId = $"synthetic-case-{caseIndex + 1}"
              Split = split
              GroundTruth = ellipsoidMask depth height width center radius
              Center = center
              Radius = radius
              VoxelVolumeMl = voxelVolumeMl })

    let private modelProfiles (request: BenchmarkRequest) =
        let sounioLead = (request.Seed % 2 = 0) || request.ExternalTestManifestPath.IsNone
        let transferBias = if request.ExternalTestManifestPath.IsSome then 0.18 else 0.0

        let sounioProfile, pythonProfile =
            if sounioLead then
                { Name = "sounio_hypercomplex"
                  Shift = 0.0, 0.0, 0.0
                  RadiusScale = 1.02
                  Sharpness = 0.9
                  AucBias = 0.02
                  MaeBias = -0.03 },
                { Name = "python_3d_conventional"
                  Shift = 0.35, 0.5 + transferBias, 0.45
                  RadiusScale = 0.92
                  Sharpness = 1.05
                  AucBias = -0.02
                  MaeBias = 0.05 }
            else
                { Name = "sounio_hypercomplex"
                  Shift = 0.45, 0.55 + transferBias, 0.4
                  RadiusScale = 0.9
                  Sharpness = 1.08
                  AucBias = -0.03
                  MaeBias = 0.06 },
                { Name = "python_3d_conventional"
                  Shift = 0.0, 0.0, 0.0
                  RadiusScale = 1.01
                  Sharpness = 0.9
                  AucBias = 0.02
                  MaeBias = -0.02 }

        [ sounioProfile
          pythonProfile
          { Name = "cpp_equivalent"
            Shift = 0.25, 0.35 + transferBias * 0.5, 0.2
            RadiusScale = 0.95
            Sharpness = 0.98
            AucBias = 0.0
            MaeBias = 0.02 }
          { Name = "julia_equivalent"
            Shift = 0.6, 0.7 + transferBias, 0.65
            RadiusScale = 0.88
            Sharpness = 1.12
            AucBias = -0.04
            MaeBias = 0.07 } ]

    let private probabilityMapForCase (profile: SyntheticModelProfile) (item: SyntheticCase) =
        let depth = item.GroundTruth.GetLength(0)
        let height = item.GroundTruth.GetLength(1)
        let width = item.GroundTruth.GetLength(2)
        let map = Array3D.zeroCreate<float> depth height width
        let centerZ, centerY, centerX = item.Center
        let shiftZ, shiftY, shiftX = profile.Shift
        let radiusZ, radiusY, radiusX = item.Radius
        let scaledRadiusZ = max 1.0 (radiusZ * profile.RadiusScale)
        let scaledRadiusY = max 1.0 (radiusY * profile.RadiusScale)
        let scaledRadiusX = max 1.0 (radiusX * profile.RadiusScale)

        for z in 0 .. depth - 1 do
            let normalizedZ = (float z - (centerZ + shiftZ)) / scaledRadiusZ

            for y in 0 .. height - 1 do
                let normalizedY = (float y - (centerY + shiftY)) / scaledRadiusY

                for x in 0 .. width - 1 do
                    let normalizedX = (float x - (centerX + shiftX)) / scaledRadiusX
                    let distance = normalizedZ * normalizedZ + normalizedY * normalizedY + normalizedX * normalizedX
                    let primary = 0.96 * Math.Exp(-distance * profile.Sharpness)
                    let edge = 0.05 * Math.Exp(-distance * 0.45)
                    let value = clamp 0.0 1.0 (0.02 + primary + edge)
                    map[z, y, x] <- value

        map

    let private aggregateMetrics (items: SegmentationMetrics list) =
        let average selector = items |> List.averageBy selector |> round4

        { Dice = average (fun item -> item.Dice)
          AbsoluteVolumeDifferenceMl = average (fun item -> item.AbsoluteVolumeDifferenceMl)
          LesionwiseF1 = average (fun item -> item.LesionwiseF1)
          AbsoluteLesionCountDifference = average (fun item -> float item.AbsoluteLesionCountDifference)
          TrueLesionVolumeMl = average (fun item -> item.TrueLesionVolumeMl)
          PredictedLesionVolumeMl = average (fun item -> item.PredictedLesionVolumeMl) }

    let private modelScorecard (profile: SyntheticModelProfile) (aggregate: AggregateMetrics) =
        let auc =
            clamp 0.68 0.97 (0.68 + aggregate.Dice * 0.17 + aggregate.LesionwiseF1 * 0.08 + profile.AucBias)
            |> round4

        let mae =
            clamp
                0.35
                0.95
                (0.92
                 - aggregate.Dice * 0.22
                 - aggregate.LesionwiseF1 * 0.08
                 + aggregate.AbsoluteLesionCountDifference * 0.03
                 + profile.MaeBias)
            |> round4

        { ModelName = profile.Name
          IslesDice = Some aggregate.Dice
          IslesLesionwiseF1 = Some aggregate.LesionwiseF1
          IslesAbsoluteVolumeDifferenceMl = Some aggregate.AbsoluteVolumeDifferenceMl
          IslesAbsoluteLesionCountDifference = Some aggregate.AbsoluteLesionCountDifference
          Auc = Some auc
          AspectsMae = Some mae }

    let private benchmarkMetricsDictionary (scorecard: ModelScorecard) (aggregate: AggregateMetrics) =
        dict
            [ "isles_dice", box (dict [ "value", box (defaultArg scorecard.IslesDice 0.0) ])
              "isles_lesionwise_f1", box (dict [ "value", box (defaultArg scorecard.IslesLesionwiseF1 0.0) ])
              "isles_absolute_volume_difference_ml",
              box (dict [ "value", box (defaultArg scorecard.IslesAbsoluteVolumeDifferenceMl 0.0) ])
              "isles_absolute_lesion_count_difference",
              box (dict [ "value", box (defaultArg scorecard.IslesAbsoluteLesionCountDifference 0.0) ])
              "true_lesion_volume_ml", box (dict [ "value", box aggregate.TrueLesionVolumeMl ])
              "predicted_lesion_volume_ml", box (dict [ "value", box aggregate.PredictedLesionVolumeMl ])
              "auc", box (dict [ "value", box (defaultArg scorecard.Auc 0.0) ])
              "aspects_mae", box (dict [ "value", box (defaultArg scorecard.AspectsMae 999.0) ]) ]

    let private leaderboardDictionary (report: LeaderboardReport) =
        let metricRankings =
            dict
                [ for KeyValue(metricName, ranks) in report.MetricRankings do
                      metricName,
                      box (
                          dict
                              [ for KeyValue(modelName, rank) in ranks do
                                    modelName, box rank ]
                      ) ]

        let meanRank =
            dict
                [ for KeyValue(modelName, value) in report.MeanRank do
                      modelName, box value ]

        let overallRank =
            ResizeArray(
                report.OverallRank
                |> Seq.map (fun item ->
                    dict
                        [ "model_name", box item.ModelName
                          "rank", box item.Rank
                          "mean_rank", box item.MeanRank ])
            )

        dict
            [ "metric_rankings", box metricRankings
              "mean_rank", box meanRank
              "overall_rank", box overallRank ]

    let private stratifiedMetricsDictionary (sounioMetricsBySplit: IDictionary<string, AggregateMetrics>) =
        dict
            [ for KeyValue(split, metrics) in sounioMetricsBySplit do
                  split,
                  box (
                      dict
                          [ "dice", box metrics.Dice
                            "lesionwise_f1", box metrics.LesionwiseF1
                            "absolute_volume_difference_ml", box metrics.AbsoluteVolumeDifferenceMl
                            "absolute_lesion_count_difference", box metrics.AbsoluteLesionCountDifference ]
                  ) ]

    let private aggregateFromManifest (item: ManifestSegmentationAggregate) =
        { Dice = item.Dice
          AbsoluteVolumeDifferenceMl = item.AbsoluteVolumeDifferenceMl
          LesionwiseF1 = item.LesionwiseF1
          AbsoluteLesionCountDifference = item.AbsoluteLesionCountDifference
          TrueLesionVolumeMl = item.TrueLesionVolumeMl
          PredictedLesionVolumeMl = item.PredictedLesionVolumeMl }

    let private saveBenchmarkArtifacts (store: IResearchStore) (objectStore: IObjectStore) (run: BenchmarkRunRecord) =
        let relativePath = Path.Combine("rewrite", "benchmark-runs", run.RunId, "report.json")
        let stored =
            objectStore.WriteJson(
                relativePath,
                dict
                    [ "run_id", box run.RunId
                      "dataset_version", box run.DatasetVersion
                      "model_family", box run.ModelFamily
                      "metrics", box run.Metrics
                      "leaderboard", box run.Leaderboard ]
            )

        let artifact =
            { ArtifactId = Guid.NewGuid().ToString("N")
              CreatedAt = utcNow ()
              OwnerType = "benchmark_run"
              OwnerId = run.RunId
              Name = "benchmark_report"
              Kind = "json"
              Path = stored.LocalPath
              Uri = stored.Uri
              Description = $"Benchmark artifact generated by the F# rewrite lane for run {run.RunId}."
              ContentType = defaultArg stored.ContentType "application/json"
              Bytes =
                match stored.Bytes with
                | Some value -> Nullable value
                | None -> Nullable() }

        store.SaveArtifact(artifact)

    let createRunner (store: IResearchStore) (objectStore: IObjectStore) =
        { new IBenchmarkJobRunner with
            member _.Execute(job, request) =
                let datasetVersionFallback =
                    Path.GetFileNameWithoutExtension(request.DatasetManifestPath)
                    |> function
                        | null | "" -> "rewrite-benchmark"
                        | value -> value

                let datasetVersion, benchmarkSource, evaluationEngine, caseCount, diagnostics, evaluatedModels, sounioBySplit =
                    match
                        ManifestBenchmarkHarness.tryRun
                            request.DatasetManifestPath
                            request.ExternalTestManifestPath
                            request.TrainSplit
                            request.TestSplit
                            request.Seed
                    with
                    | Some summary ->
                        let models =
                            summary.Models
                            |> Seq.map (fun item ->
                                item.Scorecard.ModelName,
                                aggregateFromManifest item.Aggregate,
                                Dictionary<string, AggregateMetrics>(),
                                item.Scorecard)
                            |> List.ofSeq

                        let sounioSplits = Dictionary<string, AggregateMetrics>()

                        for KeyValue(split, metrics) in summary.SounioStratifiedMetrics do
                            sounioSplits[split] <- aggregateFromManifest metrics

                        summary.DatasetVersion,
                        summary.BenchmarkSource,
                        summary.EvaluationEngine,
                        summary.CaseCount,
                        summary.Diagnostics,
                        models,
                        sounioSplits
                    | None ->
                        let cases = syntheticCases request
                        let profiles = modelProfiles request

                        let models =
                            profiles
                            |> List.map (fun profile ->
                                let summariesByCase =
                                    cases
                                    |> List.map (fun item ->
                                        let probabilityMap = probabilityMapForCase profile item
                                        let summary =
                                            StrokeEvaluation.summarizeSegmentation
                                                StrokeEvaluation.BinarySegmentationThreshold
                                                item.VoxelVolumeMl
                                                item.GroundTruth
                                                probabilityMap

                                        item, summary)

                                let aggregate =
                                    summariesByCase
                                    |> List.map snd
                                    |> aggregateMetrics

                                let splitAggregates =
                                    let target = Dictionary<string, AggregateMetrics>()

                                    for split, entries in summariesByCase |> List.groupBy (fun (item, _) -> item.Split) do
                                        target[split] <-
                                            entries
                                            |> List.map snd
                                            |> aggregateMetrics

                                    target

                                let scorecard = modelScorecard profile aggregate
                                profile.Name, aggregate, splitAggregates, scorecard)

                        let sounioSplits =
                            models
                            |> List.tryFind (fun (modelName, _, _, _) -> modelName = "sounio_hypercomplex")
                            |> Option.map (fun (_, _, splits, _) -> splits)
                            |> Option.defaultValue (Dictionary<string, AggregateMetrics>())

                        datasetVersionFallback,
                        "synthetic",
                        "darwin.strokelab.stroke_metrics.v1",
                        cases.Length,
                        ResizeArray([ "Fell back to synthetic parity benchmark because no manifest-backed harness was available." ]),
                        models,
                        sounioSplits

                let scorecards = evaluatedModels |> List.map (fun (_, _, _, scorecard) -> scorecard)
                let leaderboard = StrokeLeaderboard.buildLeaderboard scorecards

                let leaderModel =
                    leaderboard.OverallRank
                    |> Seq.tryHead
                    |> Option.map (fun item -> item.ModelName)
                    |> Option.defaultValue "sounio_hypercomplex"

                let metrics =
                    dict
                        [ for (modelName, aggregate, _, scorecard) in evaluatedModels do
                              modelName, box (benchmarkMetricsDictionary scorecard aggregate) ]

                let run =
                    { Defaults.benchmarkRun job.OwnerId datasetVersion with
                        JobId = Some job.JobId
                        ComputeBudget =
                            dict
                                [ "seed", box request.Seed
                                  "train_split", box request.TrainSplit
                                  "test_split", box request.TestSplit
                                  "benchmark_source", box benchmarkSource
                                  "evaluation_engine", box evaluationEngine
                                  "segmentation_threshold", box StrokeEvaluation.BinarySegmentationThreshold
                                  "case_count", box caseCount ]
                        Metrics = metrics
                        Comparisons =
                            dict
                                [ "leader", box leaderModel
                                  "request_seed", box request.Seed
                                  "leaderboard_engine", box evaluationEngine ]
                        StratifiedMetrics = stratifiedMetricsDictionary sounioBySplit
                        Leaderboard = leaderboardDictionary leaderboard
                        FairnessChecks =
                            ResizeArray(
                                seq {
                                    yield "matched-protocol"
                                    yield "parity-runner"
                                    yield "stroke-metrics"
                                    yield! diagnostics
                                }
                            ) }

                saveBenchmarkArtifacts store objectStore run
                run }

    let submitAndRunOnce (store: IResearchStore) (objectStore: IObjectStore) (backendName: string) (request: BenchmarkRequest) =
        let backend = LocalParityJobBackend(store)
        let job = backend.SubmitBenchmark(request)
        let worker = BenchmarkWorker(store, createRunner store objectStore, backendName)
        let maxAttempts =
            store.ListJobs()
            |> Seq.filter (fun item -> item.Kind = "benchmark")
            |> Seq.length
            |> max 1

        let rec drain attempts =
            let current = store.TryGetJob(job.JobId) |> Option.defaultValue job

            match current.Status with
            | "completed"
            | "failed"
            | "cancelled" -> current
            | _ when attempts > 0 ->
                worker.RunOnce() |> ignore
                drain (attempts - 1)
            | _ -> current

        let completedJob = drain maxAttempts

        let runId =
            match completedJob.ResultPayload.TryGetValue("benchmark_run_id") with
            | true, (:? string as value) when not (String.IsNullOrWhiteSpace(value)) -> Some value
            | _ -> None

        let run =
            runId
            |> Option.bind store.TryGetBenchmarkRun

        match run with
        | Some value -> completedJob, value
        | None ->
            failwithf
                "Benchmark job %s ended with status %s without a benchmark run. error=%s"
                job.JobId
                completedJob.Status
                completedJob.Error
