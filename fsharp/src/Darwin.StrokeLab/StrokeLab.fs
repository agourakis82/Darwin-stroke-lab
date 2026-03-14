namespace Darwin.StrokeLab

open Darwin.ResearchOs.Core

type StrokeManifestBinding =
    { DatasetManifestPath: string
      TrainSplit: string
      TestSplit: string
      UsesSounioKernel: bool }

type StrokeParityCheckpoint =
    { KeepsCurrentApi: bool
      KeepsBenchmarkArtifacts: bool
      KeepsCurrentFixtures: bool
      Notes: string list }

type StrokeKernelAdapter() =
    interface IStrokeKernelAdapter with
        member _.KernelName = "Sounio"
        member _.UsesOfficialSounioRuntime = true
        member _.BaselineProbabilisticLane = "Stan"

module StrokePlan =
    let currentCheckpoint () =
        { KeepsCurrentApi = true
          KeepsBenchmarkArtifacts = true
          KeepsCurrentFixtures = true
          Notes =
              [ "Stroke remains the flagship lab during the rewrite."
                "Heavy scientific math stays in Sounio wherever practical."
                "Python stays only as the temporary compatibility shell until parity is reached." ] }
