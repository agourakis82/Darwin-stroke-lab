namespace Darwin.ResearchOs.Storage

open System
open System.IO
open Darwin.ResearchOs.Core

module RuntimePathing =
    let private hasRepoMarkers path =
        Directory.Exists(Path.Combine(path, "contracts"))
        && File.Exists(Path.Combine(path, "AGENTS.md"))

    let repoRoot () =
        let rec ascend (current: string) =
            if hasRepoMarkers current then
                current
            else
                let parent = Directory.GetParent(current)
                if isNull parent then current else ascend parent.FullName

        ascend (Path.GetFullPath(AppContext.BaseDirectory))

    let rewriteCacheRoot () =
        let configured = Environment.GetEnvironmentVariable("DARWIN_RESEARCH_OS_OBJECT_ROOT")
        if String.IsNullOrWhiteSpace(configured) then
            Path.Combine(repoRoot (), ".rewrite-cache")
        else
            Path.GetFullPath(configured)

type RuntimeServiceSet =
    { ResearchStore: IResearchStore
      ObjectStore: IObjectStore
      StateMode: string
      ObjectMode: string
      DbConfigured: bool
      ObjectRoot: string }

module RuntimeServiceSet =
    let private firstEnv names =
        names
        |> List.tryPick (fun name ->
            let value = Environment.GetEnvironmentVariable(name)
            if String.IsNullOrWhiteSpace(value) then None else Some value)

    let create () =
        let dbUrl = firstEnv [ "DARWIN_RESEARCH_OS_DB_URL"; "SOUNIO_STROKE_DB_URL" ]

        let researchStore, stateMode =
            match dbUrl with
            | Some connectionString ->
                let store = PostgresResearchStore(connectionString)
                store.Initialize()
                (store :> IResearchStore), "postgres"
            | None -> (InMemoryResearchStore() :> IResearchStore), "in-memory"

        let objectMode =
            firstEnv [ "DARWIN_RESEARCH_OS_OBJECT_STORE"; "SOUNIO_STROKE_OBJECT_STORE" ]
            |> Option.defaultValue "filesystem"

        let objectRoot = RuntimePathing.rewriteCacheRoot ()
        Directory.CreateDirectory(objectRoot) |> ignore

        let objectStore: IObjectStore =
            match objectMode with
            | "minio" -> MinioObjectStore(objectRoot) :> IObjectStore
            | _ -> FilesystemObjectStore(objectRoot) :> IObjectStore

        { ResearchStore = researchStore
          ObjectStore = objectStore
          StateMode = stateMode
          ObjectMode = objectMode
          DbConfigured = dbUrl.IsSome
          ObjectRoot = objectRoot }
