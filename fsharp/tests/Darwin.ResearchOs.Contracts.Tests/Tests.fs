module Tests

open Darwin.ResearchOs.Contracts
open Xunit

[<Fact>]
let ``default safety policy stays local and deidentified`` () =
    let policy = Defaults.safetyPolicy ()
    Assert.Equal("local_deidentified", policy.PrivacyMode)
    Assert.Equal("autonomous_lab", policy.AutonomyMode)

[<Fact>]
let ``benchmark request keeps canonical split defaults`` () =
    let request = Defaults.benchmarkRequest "/datasets/fixture-mini.json"
    Assert.Equal("train", request.TrainSplit)
    Assert.Equal("test", request.TestSplit)
    Assert.Equal(13, request.Seed)
