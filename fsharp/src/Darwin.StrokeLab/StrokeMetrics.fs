namespace Darwin.StrokeLab

open System
open System.Collections.Generic

type SegmentationMetrics =
    { Dice: float
      AbsoluteVolumeDifferenceMl: float
      LesionwiseF1: float
      AbsoluteLesionCountDifference: int
      TrueLesionVolumeMl: float
      PredictedLesionVolumeMl: float }

type ModelScorecard =
    { ModelName: string
      IslesDice: float option
      IslesLesionwiseF1: float option
      IslesAbsoluteVolumeDifferenceMl: float option
      IslesAbsoluteLesionCountDifference: float option
      Auc: float option
      AspectsMae: float option }

type LeaderboardEntry =
    { ModelName: string
      Rank: int
      MeanRank: float }

type LeaderboardReport =
    { MetricRankings: Dictionary<string, Dictionary<string, int>>
      MeanRank: Dictionary<string, float>
      OverallRank: ResizeArray<LeaderboardEntry> }

module StrokeEvaluation =
    let BinarySegmentationThreshold = 0.5
    let LesionMatchIouThreshold = 0.2

    let private shapeOf (values: 'T[,,]) =
        values.GetLength(0), values.GetLength(1), values.GetLength(2)

    let private scaledIndex sourceLength targetLength targetIndex =
        if sourceLength = targetLength then
            targetIndex
        else
            let scaled = int (float targetIndex * float sourceLength / float targetLength)
            min (sourceLength - 1) scaled

    let binaryPredictionMask (threshold: float) (probabilityMap: float[,,]) =
        let depth, height, width = shapeOf probabilityMap
        let mask = Array3D.zeroCreate<bool> depth height width

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    mask[z, y, x] <- probabilityMap[z, y, x] >= threshold

        mask

    let alignBinaryMask (mask: bool[,,]) (targetShape: int * int * int) =
        let sourceDepth, sourceHeight, sourceWidth = shapeOf mask
        let targetDepth, targetHeight, targetWidth = targetShape

        if (sourceDepth, sourceHeight, sourceWidth) = targetShape then
            mask
        else
            let aligned = Array3D.zeroCreate<bool> targetDepth targetHeight targetWidth

            for z in 0 .. targetDepth - 1 do
                let sourceZ = scaledIndex sourceDepth targetDepth z

                for y in 0 .. targetHeight - 1 do
                    let sourceY = scaledIndex sourceHeight targetHeight y

                    for x in 0 .. targetWidth - 1 do
                        let sourceX = scaledIndex sourceWidth targetWidth x
                        aligned[z, y, x] <- mask[sourceZ, sourceY, sourceX]

            aligned

    let private countTrue (mask: bool[,,]) =
        let depth, height, width = shapeOf mask
        let mutable total = 0

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    if mask[z, y, x] then
                        total <- total + 1

        total

    let diceScore (emptyValue: float) (groundTruth: bool[,,]) (prediction: bool[,,]) =
        let truth = alignBinaryMask groundTruth (shapeOf prediction)
        let truthTotal = countTrue truth
        let predTotal = countTrue prediction

        if truthTotal = 0 && predTotal = 0 then
            emptyValue
        elif truthTotal = 0 || predTotal = 0 then
            0.0
        else
            let depth, height, width = shapeOf prediction
            let mutable overlap = 0

            for z in 0 .. depth - 1 do
                for y in 0 .. height - 1 do
                    for x in 0 .. width - 1 do
                        if truth[z, y, x] && prediction[z, y, x] then
                            overlap <- overlap + 1

            (2.0 * float overlap) / float (truthTotal + predTotal)

    let alignedSegmentationVolumesMl (groundTruth: bool[,,]) (prediction: bool[,,]) (voxelVolumeMl: float) =
        let truth = alignBinaryMask groundTruth (shapeOf prediction)
        float (countTrue truth) * voxelVolumeMl, float (countTrue prediction) * voxelVolumeMl

    let absoluteVolumeDifferenceMl (groundTruth: bool[,,]) (prediction: bool[,,]) (voxelVolumeMl: float) =
        let trueVolumeMl, predictedVolumeMl = alignedSegmentationVolumesMl groundTruth prediction voxelVolumeMl
        abs (trueVolumeMl - predictedVolumeMl)

    let private collectConnectedComponents (mask: bool[,,]) =
        let depth, height, width = shapeOf mask
        let visited = Array3D.zeroCreate<bool> depth height width
        let components = ResizeArray<HashSet<int>>()
        let plane = height * width

        let flatten z y x = z * plane + y * width + x

        let neighbors =
            [| for dz in -1 .. 1 do
                   for dy in -1 .. 1 do
                       for dx in -1 .. 1 do
                           if dz <> 0 || dy <> 0 || dx <> 0 then
                               yield dz, dy, dx |]

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    if mask[z, y, x] && not visited[z, y, x] then
                        let cluster = HashSet<int>()
                        let frontier = Stack<int * int * int>()
                        frontier.Push(z, y, x)
                        visited[z, y, x] <- true

                        while frontier.Count > 0 do
                            let currentZ, currentY, currentX = frontier.Pop()
                            cluster.Add(flatten currentZ currentY currentX) |> ignore

                            for dz, dy, dx in neighbors do
                                let nextZ = currentZ + dz
                                let nextY = currentY + dy
                                let nextX = currentX + dx

                                if
                                    nextZ >= 0
                                    && nextZ < depth
                                    && nextY >= 0
                                    && nextY < height
                                    && nextX >= 0
                                    && nextX < width
                                    && mask[nextZ, nextY, nextX]
                                    && not visited[nextZ, nextY, nextX]
                                then
                                    visited[nextZ, nextY, nextX] <- true
                                    frontier.Push(nextZ, nextY, nextX)

                        components.Add(cluster)

        components

    let lesionwiseF1AndCountDifference
        (iouThreshold: float)
        (emptyValue: float)
        (groundTruth: bool[,,])
        (prediction: bool[,,])
        =
        let truth = alignBinaryMask groundTruth (shapeOf prediction)
        let truthComponents = collectConnectedComponents truth
        let predComponents = collectConnectedComponents prediction
        let truthCount = truthComponents.Count
        let predCount = predComponents.Count

        if truthCount = 0 && predCount = 0 then
            emptyValue, 0
        elif truthCount = 0 || predCount = 0 then
            0.0, abs (truthCount - predCount)
        else
            let candidatePairs = ResizeArray<float * int * int>()

            for truthIndex in 0 .. truthCount - 1 do
                let truthComponent = truthComponents[truthIndex]

                for predIndex in 0 .. predCount - 1 do
                    let predComponent = predComponents[predIndex]
                    let mutable overlap = 0

                    for value in truthComponent do
                        if predComponent.Contains(value) then
                            overlap <- overlap + 1

                    if overlap > 0 then
                        let unionCount = truthComponent.Count + predComponent.Count - overlap
                        let iou = float overlap / float (max unionCount 1)

                        if iou >= iouThreshold then
                            candidatePairs.Add(iou, truthIndex, predIndex)

            let matchedTruth = HashSet<int>()
            let matchedPred = HashSet<int>()
            let mutable truePositives = 0

            for _, truthIndex, predIndex in candidatePairs |> Seq.sortByDescending (fun (iou, _, _) -> iou) do
                if not (matchedTruth.Contains truthIndex) && not (matchedPred.Contains predIndex) then
                    matchedTruth.Add(truthIndex) |> ignore
                    matchedPred.Add(predIndex) |> ignore
                    truePositives <- truePositives + 1

            let precision =
                if predCount = 0 then 0.0 else float truePositives / float predCount

            let recall =
                if truthCount = 0 then 0.0 else float truePositives / float truthCount

            let f1 =
                if precision = 0.0 && recall = 0.0 then
                    0.0
                else
                    (2.0 * precision * recall) / (precision + recall)

            f1, abs (truthCount - predCount)

    let summarizeSegmentation (threshold: float) (voxelVolumeMl: float) (groundTruth: bool[,,]) (probabilityMap: float[,,]) =
        let prediction = binaryPredictionMask threshold probabilityMap
        let dice = diceScore 1.0 groundTruth prediction
        let lesionwiseF1, lesionCountDifference = lesionwiseF1AndCountDifference LesionMatchIouThreshold 1.0 groundTruth prediction
        let trueVolumeMl, predictedVolumeMl = alignedSegmentationVolumesMl groundTruth prediction voxelVolumeMl

        { Dice = dice
          AbsoluteVolumeDifferenceMl = abs (trueVolumeMl - predictedVolumeMl)
          LesionwiseF1 = lesionwiseF1
          AbsoluteLesionCountDifference = lesionCountDifference
          TrueLesionVolumeMl = trueVolumeMl
          PredictedLesionVolumeMl = predictedVolumeMl }

module StrokeLeaderboard =
    let private rankMetric (items: (string * float) list) (higherIsBetter: bool) =
        let ordered =
            if higherIsBetter then
                items |> List.sortByDescending snd
            else
                items |> List.sortBy snd

        let ranks = Dictionary<string, int>()

        ordered
        |> List.iteri (fun index (modelName, _) -> ranks[modelName] <- index + 1)

        ranks

    let buildLeaderboard (scorecards: seq<ModelScorecard>) =
        let items = scorecards |> Seq.toList
        let metricSpecs =
            [ "isles_dice", true, fun item -> item.IslesDice
              "isles_lesionwise_f1", true, fun item -> item.IslesLesionwiseF1
              "isles_absolute_volume_difference_ml", false, fun item -> item.IslesAbsoluteVolumeDifferenceMl
              "isles_absolute_lesion_count_difference", false, fun item -> item.IslesAbsoluteLesionCountDifference
              "auc", true, fun item -> item.Auc
              "aspects_mae", false, fun item -> item.AspectsMae ]

        let metricRankings = Dictionary<string, Dictionary<string, int>>()
        let scoreTotals = Dictionary<string, float>()
        let scoreCounts = Dictionary<string, int>()

        for item in items do
            scoreTotals[item.ModelName] <- 0.0
            scoreCounts[item.ModelName] <- 0

        for metricName, higherIsBetter, selector in metricSpecs do
            let metricValues =
                items
                |> List.choose (fun item ->
                    selector item |> Option.map (fun value -> item.ModelName, value))

            if not metricValues.IsEmpty then
                let ranks = rankMetric metricValues higherIsBetter
                metricRankings[metricName] <- ranks

                for KeyValue(modelName, rank) in ranks do
                    scoreTotals[modelName] <- scoreTotals[modelName] + float rank
                    scoreCounts[modelName] <- scoreCounts[modelName] + 1

        let meanRanks = Dictionary<string, float>()

        for item in items do
            let divisor = max scoreCounts[item.ModelName] 1
            meanRanks[item.ModelName] <- Math.Round(scoreTotals[item.ModelName] / float divisor, 4)

        let overall =
            items
            |> List.map (fun item -> item.ModelName, meanRanks[item.ModelName])
            |> List.sortBy snd

        { MetricRankings = metricRankings
          MeanRank = meanRanks
          OverallRank =
            ResizeArray(
                overall
                |> List.mapi (fun index (modelName, meanRank) ->
                    { ModelName = modelName
                      Rank = index + 1
                      MeanRank = meanRank })
            ) }
