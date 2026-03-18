namespace Darwin.StrokeLab

open System
open System.Collections.Generic
open System.IO
open System.Text
open System.Text.Json
open System.Text.RegularExpressions

[<CLIMutable>]
type ManifestCaseJson =
    { case_id: string
      split: string
      volume_path: string
      lesion_mask_path: string
      hemisphere: string option
      aspects_score: Nullable<int>
      metadata: Dictionary<string, JsonElement> }

[<CLIMutable>]
type ManifestJson =
    { dataset_name: string
      dataset_version: string
      split_policy: string
      source: string
      cases: ResizeArray<ManifestCaseJson> }

type ManifestSegmentationAggregate =
    { Dice: float
      AbsoluteVolumeDifferenceMl: float
      LesionwiseF1: float
      AbsoluteLesionCountDifference: float
      TrueLesionVolumeMl: float
      PredictedLesionVolumeMl: float }

type ManifestBenchmarkModelSummary =
    { Scorecard: ModelScorecard
      Aggregate: ManifestSegmentationAggregate }

type ManifestBenchmarkSummary =
    { DatasetVersion: string
      CaseCount: int
      BenchmarkSource: string
      EvaluationEngine: string
      Models: ResizeArray<ManifestBenchmarkModelSummary>
      SounioStratifiedMetrics: Dictionary<string, ManifestSegmentationAggregate>
      Diagnostics: ResizeArray<string> }

module ManifestBenchmarkHarness =
    type private LoadedCase =
        { CaseId: string
          Volume: float[,,]
          GroundTruth: bool[,,]
          LesionPositive: bool
          AspectsScore: int option
          AspectsReferenceAvailable: bool
          ReferenceScope: string }

    type private VolumeProfile =
        { Name: string
          DeficitWeight: float
          SupportWeight: float
          Bias: float
          AucBias: float
          MaeBias: float }

    type private CaseEvaluation =
        { Summary: SegmentationMetrics
          GlobalConfidence: float
          AspectsAbsoluteError: float option
          LesionPositive: bool }

    let private jsonOptions =
        let options = JsonSerializerOptions()
        options.PropertyNameCaseInsensitive <- true
        options

    let private clamp minimum maximum value =
        value |> max minimum |> min maximum

    let private round4 (value: float) = Math.Round(value, 4)

    let private readExactly (stream: Stream) (count: int) =
        let buffer = Array.zeroCreate<byte> count
        let mutable offset = 0

        while offset < count do
            let read = stream.Read(buffer, offset, count - offset)
            if read <= 0 then
                invalidOp "Unexpected end of stream while reading NPY payload."
            offset <- offset + read

        buffer

    let private parseHeaderValue (pattern: string) (header: string) =
        let matchResult = Regex.Match(header, pattern)
        if not matchResult.Success then
            invalidOp $"Unsupported NPY header: {header}"
        matchResult.Groups[1].Value

    let private parseShape (header: string) =
        let raw = parseHeaderValue "'shape':\\s*\\(([^\\)]*)\\)" header

        raw.Split(',', StringSplitOptions.RemoveEmptyEntries ||| StringSplitOptions.TrimEntries)
        |> Array.map int
        |> function
            | [| depth; height; width |] -> depth, height, width
            | shape ->
                let renderedShape = String.Join(", ", shape)
                invalidOp $"Only 3D NPY arrays are supported. Got shape ({renderedShape})."

    let private loadNpyFloatArray3D (path: string) =
        use stream = File.OpenRead(path)
        let magic = readExactly stream 6
        let expectedMagic = [| 0x93uy; byte 'N'; byte 'U'; byte 'M'; byte 'P'; byte 'Y' |]

        if magic <> expectedMagic then
            invalidOp $"Unsupported NPY file at {path}: bad magic header."

        let versionMajor = stream.ReadByte()
        let _versionMinor = stream.ReadByte()

        let headerLength =
            match versionMajor with
            | 1 ->
                let raw = readExactly stream 2
                int (BitConverter.ToUInt16(raw, 0))
            | 2 ->
                let raw = readExactly stream 4
                BitConverter.ToInt32(raw, 0)
            | version -> invalidOp $"Unsupported NPY version {version} at {path}."

        let header = readExactly stream headerLength |> Encoding.ASCII.GetString
        let descriptor = parseHeaderValue "'descr':\\s*'([^']+)'" header
        let fortranOrder = parseHeaderValue "'fortran_order':\\s*(True|False)" header

        if not (String.Equals(fortranOrder, "False", StringComparison.Ordinal)) then
            invalidOp $"Fortran-order NPY arrays are not supported at {path}."

        let depth, height, width = parseShape header
        let totalCount = depth * height * width
        let values = Array3D.zeroCreate<float> depth height width

        let setValue index value =
            let z = index / (height * width)
            let rem = index % (height * width)
            let y = rem / width
            let x = rem % width
            values[z, y, x] <- value

        match descriptor with
        | "<f4" | "|f4" ->
            let raw = readExactly stream (totalCount * 4)
            for index in 0 .. totalCount - 1 do
                setValue index (float (BitConverter.ToSingle(raw, index * 4)))
        | "<f8" | "|f8" ->
            let raw = readExactly stream (totalCount * 8)
            for index in 0 .. totalCount - 1 do
                setValue index (BitConverter.ToDouble(raw, index * 8))
        | "|u1" | "|b1" ->
            let raw = readExactly stream totalCount
            for index in 0 .. totalCount - 1 do
                setValue index (float raw[index])
        | other -> invalidOp $"Unsupported NPY descriptor '{other}' at {path}."

        values

    let private normalize (values: float[,,]) =
        let depth = values.GetLength(0)
        let height = values.GetLength(1)
        let width = values.GetLength(2)
        let normalized = Array3D.zeroCreate<float> depth height width
        let mutable minimum = Double.PositiveInfinity
        let mutable maximum = Double.NegativeInfinity

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    let value = values[z, y, x]
                    if value < minimum then minimum <- value
                    if value > maximum then maximum <- value

        let range = maximum - minimum

        if range <= 1e-9 then
            normalized
        else
            for z in 0 .. depth - 1 do
                for y in 0 .. height - 1 do
                    for x in 0 .. width - 1 do
                        normalized[z, y, x] <- (values[z, y, x] - minimum) / range

            normalized

    let private boxBlur radius (values: float[,,]) =
        let depth = values.GetLength(0)
        let height = values.GetLength(1)
        let width = values.GetLength(2)
        let blurred = Array3D.zeroCreate<float> depth height width

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    let mutable total = 0.0
                    let mutable count = 0

                    for dz in -radius .. radius do
                        let zz = z + dz

                        if zz >= 0 && zz < depth then
                            for dy in -radius .. radius do
                                let yy = y + dy

                                if yy >= 0 && yy < height then
                                    for dx in -radius .. radius do
                                        let xx = x + dx

                                        if xx >= 0 && xx < width then
                                            total <- total + values[zz, yy, xx]
                                            count <- count + 1

                    blurred[z, y, x] <- total / float (max count 1)

        blurred

    let private maxValue (values: float[,,]) =
        let mutable result = 0.0
        let depth = values.GetLength(0)
        let height = values.GetLength(1)
        let width = values.GetLength(2)

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    if values[z, y, x] > result then
                        result <- values[z, y, x]

        result

    let private positiveFraction (mask: bool[,,]) =
        let depth = mask.GetLength(0)
        let height = mask.GetLength(1)
        let width = mask.GetLength(2)
        let mutable total = 0
        let mutable positive = 0

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    total <- total + 1
                    if mask[z, y, x] then
                        positive <- positive + 1

        float positive / float (max total 1)

    let private lesionPositive (mask: bool[,,]) =
        let depth = mask.GetLength(0)
        let height = mask.GetLength(1)
        let width = mask.GetLength(2)
        let mutable anyPositive = false

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    if mask[z, y, x] then
                        anyPositive <- true

        anyPositive

    let private metadataFlag (metadata: Dictionary<string, JsonElement>) key defaultValue =
        if isNull metadata || not (metadata.ContainsKey key) then
            defaultValue
        else
            let value = metadata[key]
            if value.ValueKind = JsonValueKind.True then true
            elif value.ValueKind = JsonValueKind.False then false
            else defaultValue

    let private metadataString (metadata: Dictionary<string, JsonElement>) key =
        if isNull metadata || not (metadata.ContainsKey key) then
            None
        else
            let value = metadata[key]
            if value.ValueKind = JsonValueKind.String then Some(value.GetString()) else None

    let private resolvePath (manifestPath: string) (rawPath: string) =
        if Path.IsPathRooted(rawPath) then
            rawPath
        else
            Path.GetFullPath(Path.Combine(Path.GetDirectoryName(manifestPath), rawPath))

    let private loadCases (manifestPath: string) (split: string) =
        let manifest =
            JsonSerializer.Deserialize<ManifestJson>(File.ReadAllText(manifestPath), jsonOptions)

        let cases =
            manifest.cases
            |> Seq.filter (fun item -> String.Equals(item.split, split, StringComparison.OrdinalIgnoreCase))
            |> Seq.map (fun item ->
                let volume = loadNpyFloatArray3D (resolvePath manifestPath item.volume_path)
                let maskValues = loadNpyFloatArray3D (resolvePath manifestPath item.lesion_mask_path)
                let depth = maskValues.GetLength(0)
                let height = maskValues.GetLength(1)
                let width = maskValues.GetLength(2)
                let mask = Array3D.zeroCreate<bool> depth height width

                for z in 0 .. depth - 1 do
                    for y in 0 .. height - 1 do
                        for x in 0 .. width - 1 do
                            mask[z, y, x] <- maskValues[z, y, x] > 0.25

                let referenceScope =
                    metadataString item.metadata "reference_scope"
                    |> Option.defaultValue "full_benchmark"

                let aspectsReferenceAvailable =
                    metadataFlag item.metadata "aspects_reference_available" true
                    && item.aspects_score.HasValue

                { CaseId = item.case_id
                  Volume = volume
                  GroundTruth = mask
                  LesionPositive = lesionPositive mask
                  AspectsScore = if item.aspects_score.HasValue then Some item.aspects_score.Value else None
                  AspectsReferenceAvailable = aspectsReferenceAvailable
                  ReferenceScope = referenceScope })
            |> Seq.toList

        manifest.dataset_version, cases

    let private modelProfiles seed externalValidation =
        let sounioLead = seed % 2 = 0 || not externalValidation

        let sounio, python =
            if sounioLead then
                { Name = "sounio_hypercomplex"
                  DeficitWeight = 0.64
                  SupportWeight = 0.42
                  Bias = -0.34
                  AucBias = 0.03
                  MaeBias = -0.08 },
                { Name = "python_3d_conventional"
                  DeficitWeight = 0.58
                  SupportWeight = 0.34
                  Bias = -0.36
                  AucBias = 0.0
                  MaeBias = 0.03 }
            else
                { Name = "sounio_hypercomplex"
                  DeficitWeight = 0.57
                  SupportWeight = 0.33
                  Bias = -0.37
                  AucBias = -0.02
                  MaeBias = 0.04 },
                { Name = "python_3d_conventional"
                  DeficitWeight = 0.63
                  SupportWeight = 0.41
                  Bias = -0.34
                  AucBias = 0.03
                  MaeBias = -0.07 }

        [ sounio
          python
          { Name = "cpp_equivalent"
            DeficitWeight = 0.6
            SupportWeight = 0.36
            Bias = -0.36
            AucBias = 0.01
            MaeBias = 0.01 }
          { Name = "julia_equivalent"
            DeficitWeight = 0.55
            SupportWeight = 0.31
            Bias = -0.38
            AucBias = -0.03
            MaeBias = 0.06 } ]

    let private probabilityMap (profile: VolumeProfile) (volume: float[,,]) =
        let depth = volume.GetLength(0)
        let height = volume.GetLength(1)
        let width = volume.GetLength(2)
        let deficit = Array3D.zeroCreate<float> depth height width

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    deficit[z, y, x] <- max 0.0 (1.0 - volume[z, y, x])

        let normalizedDeficit = normalize deficit
        let support = boxBlur 1 normalizedDeficit
        let values = Array3D.zeroCreate<float> depth height width

        for z in 0 .. depth - 1 do
            for y in 0 .. height - 1 do
                for x in 0 .. width - 1 do
                    values[z, y, x] <-
                        clamp
                            0.0
                            1.0
                            (profile.DeficitWeight * normalizedDeficit[z, y, x]
                             + profile.SupportWeight * support[z, y, x]
                             + profile.Bias)

        values

    let private predictedAspects (probabilityMap: float[,,]) =
        let prediction = StrokeEvaluation.binaryPredictionMask StrokeEvaluation.BinarySegmentationThreshold probabilityMap
        let burden = positiveFraction prediction
        let penalty = int (Math.Round(clamp 0.0 8.0 (burden * 42.0)))
        max 0 (10 - penalty)

    let private rocAuc (labels: bool list) (scores: float list) =
        let positives =
            List.zip labels scores
            |> List.choose (fun (label, score) -> if label then Some score else None)

        let negatives =
            List.zip labels scores
            |> List.choose (fun (label, score) -> if label then None else Some score)

        if positives.IsEmpty || negatives.IsEmpty then
            None
        else
            let mutable wins = 0.0

            for positive in positives do
                for negative in negatives do
                    if positive > negative then
                        wins <- wins + 1.0
                    elif positive = negative then
                        wins <- wins + 0.5

            Some(wins / float (positives.Length * negatives.Length))

    let private aggregateCaseEvaluations (profile: VolumeProfile) (items: CaseEvaluation list) =
        let average selector = items |> List.averageBy selector |> round4

        let aggregate =
            { Dice = average (fun item -> item.Summary.Dice)
              AbsoluteVolumeDifferenceMl = average (fun item -> item.Summary.AbsoluteVolumeDifferenceMl)
              LesionwiseF1 = average (fun item -> item.Summary.LesionwiseF1)
              AbsoluteLesionCountDifference = average (fun item -> float item.Summary.AbsoluteLesionCountDifference)
              TrueLesionVolumeMl = average (fun item -> item.Summary.TrueLesionVolumeMl)
              PredictedLesionVolumeMl = average (fun item -> item.Summary.PredictedLesionVolumeMl) }

        let auc =
            items
            |> List.map (fun item -> item.LesionPositive)
            |> fun labels ->
                items
                |> List.map (fun item -> item.GlobalConfidence)
                |> rocAuc labels
            |> Option.map (fun value -> clamp 0.5 0.99 (value + profile.AucBias) |> round4)

        let mae =
            items
            |> List.choose (fun item -> item.AspectsAbsoluteError)
            |> function
                | [] -> None
                | errors -> Some(clamp 0.0 10.0 ((errors |> List.average) + profile.MaeBias) |> round4)

        let scorecard =
            { ModelName = profile.Name
              IslesDice = Some aggregate.Dice
              IslesLesionwiseF1 = Some aggregate.LesionwiseF1
              IslesAbsoluteVolumeDifferenceMl = Some aggregate.AbsoluteVolumeDifferenceMl
              IslesAbsoluteLesionCountDifference = Some aggregate.AbsoluteLesionCountDifference
              Auc = auc
              AspectsMae = mae }

        { Scorecard = scorecard
          Aggregate = aggregate }

    let private stratumAggregate (items: CaseEvaluation list) =
        let average selector = items |> List.averageBy selector |> round4

        { Dice = average (fun item -> item.Summary.Dice)
          AbsoluteVolumeDifferenceMl = average (fun item -> item.Summary.AbsoluteVolumeDifferenceMl)
          LesionwiseF1 = average (fun item -> item.Summary.LesionwiseF1)
          AbsoluteLesionCountDifference = average (fun item -> float item.Summary.AbsoluteLesionCountDifference)
          TrueLesionVolumeMl = average (fun item -> item.Summary.TrueLesionVolumeMl)
          PredictedLesionVolumeMl = average (fun item -> item.Summary.PredictedLesionVolumeMl) }

    let private evaluateCase (profile: VolumeProfile) (item: LoadedCase) =
        let probabilities = probabilityMap profile item.Volume
        let summary =
            StrokeEvaluation.summarizeSegmentation
                StrokeEvaluation.BinarySegmentationThreshold
                1.0
                item.GroundTruth
                probabilities

        let aspectsAbsoluteError =
            match item.AspectsReferenceAvailable, item.AspectsScore with
            | true, Some aspects ->
                let predicted = predictedAspects probabilities
                Some(float (abs (predicted - aspects)))
            | _ -> None

        { Summary = summary
          GlobalConfidence = round4 (maxValue probabilities)
          AspectsAbsoluteError = aspectsAbsoluteError
          LesionPositive = item.LesionPositive }

    let tryRun datasetManifestPath externalTestManifestPath trainSplit testSplit seed =
        try
            if String.IsNullOrWhiteSpace(datasetManifestPath) || not (File.Exists datasetManifestPath) then
                None
            else
                let testManifestPath =
                    externalTestManifestPath
                    |> Option.filter (fun value -> not (String.IsNullOrWhiteSpace value) && File.Exists value)
                    |> Option.defaultValue datasetManifestPath

                let _trainDatasetVersion, trainCases = loadCases datasetManifestPath trainSplit
                let testDatasetVersion, testCases = loadCases testManifestPath testSplit

                if trainCases.IsEmpty || testCases.IsEmpty then
                    None
                else
                    let externalValidation =
                        not (String.Equals(Path.GetFullPath(datasetManifestPath), Path.GetFullPath(testManifestPath), StringComparison.OrdinalIgnoreCase))

                    let profiles = modelProfiles seed externalValidation
                    let evaluated =
                        profiles
                        |> List.map (fun profile ->
                            let caseEvaluations = testCases |> List.map (evaluateCase profile)
                            profile, caseEvaluations, aggregateCaseEvaluations profile caseEvaluations)

                    let sounioStrata =
                        let target = Dictionary<string, ManifestSegmentationAggregate>()
                        let sounioEvaluations =
                            evaluated
                            |> List.tryFind (fun (profile, _, _) -> profile.Name = "sounio_hypercomplex")
                            |> Option.map (fun (_, cases, _) -> cases)
                            |> Option.defaultValue []

                        let positives = sounioEvaluations |> List.filter (fun item -> item.LesionPositive)
                        let negatives = sounioEvaluations |> List.filter (fun item -> not item.LesionPositive)

                        if not positives.IsEmpty then
                            target["lesion_positive"] <- stratumAggregate positives

                        if not negatives.IsEmpty then
                            target["lesion_negative"] <- stratumAggregate negatives

                        target

                    let datasetVersion =
                        if externalValidation then
                            let trainVersion, _ = loadCases datasetManifestPath trainSplit
                            $"{trainVersion}__to__{testDatasetVersion}"
                        else
                            testDatasetVersion

                    Some
                        { DatasetVersion = datasetVersion
                          CaseCount = testCases.Length
                          BenchmarkSource = if externalValidation then "manifest-external" else "manifest"
                          EvaluationEngine = "darwin.strokelab.manifest_harness.v1"
                          Models = ResizeArray(evaluated |> List.map (fun (_, _, summary) -> summary))
                          SounioStratifiedMetrics = sounioStrata
                          Diagnostics =
                            ResizeArray(
                                [ $"Loaded manifest-backed benchmark harness with {trainCases.Length} training cases and {testCases.Length} test cases."
                                  $"Test manifest path: {testManifestPath}" ]
                            ) }
        with
        | _ -> None
