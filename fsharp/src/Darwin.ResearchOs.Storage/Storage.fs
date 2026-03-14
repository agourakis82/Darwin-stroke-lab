namespace Darwin.ResearchOs.Storage

open System
open System.Collections.Generic
open System.IO
open System.Text.Json
open Darwin.ResearchOs.Contracts
open Darwin.ResearchOs.Core

type StateStoreMode =
    | LegacySqlite
    | PostgresParity

type ObjectStoreMode =
    | LegacyFilesystem
    | MinioParity

type StorageProfile =
    { StateStoreMode: StateStoreMode
      ObjectStoreMode: ObjectStoreMode
      Notes: string list }

type PostgresStateStore(connectionDescriptor: string) =
    interface IStateStore with
        member _.Mode = "postgres"
        member _.ConnectionDescriptor = connectionDescriptor

type SqliteStateStore(path: string) =
    interface IStateStore with
        member _.Mode = "sqlite"
        member _.ConnectionDescriptor = path

type MinioObjectStore(rootDescriptor: string) =
    interface IObjectStore with
        member _.Mode = "minio"
        member _.RootDescriptor = rootDescriptor
        member _.WriteText(relativePath, content) =
            let target = Path.Combine(rootDescriptor, relativePath)
            let directory = Path.GetDirectoryName(target)
            if not (String.IsNullOrWhiteSpace(directory)) then
                Directory.CreateDirectory(directory) |> ignore
            File.WriteAllText(target, content)
            let normalized = relativePath.Replace("\\", "/").TrimStart('/')
            let prefix =
                if rootDescriptor.StartsWith("minio://", StringComparison.OrdinalIgnoreCase) then rootDescriptor.TrimEnd('/')
                else $"minio://{rootDescriptor.TrimEnd('/')}"
            { LocalPath = target
              Uri = Some $"{prefix}/{normalized}"
              Bytes = Some(int (FileInfo(target).Length))
              ContentType = Some "application/octet-stream" }
        member this.AppendText(relativePath, content) =
            let target = Path.Combine(rootDescriptor, relativePath)
            let directory = Path.GetDirectoryName(target)
            if not (String.IsNullOrWhiteSpace(directory)) then
                Directory.CreateDirectory(directory) |> ignore
            File.AppendAllText(target, content)
            let normalized = relativePath.Replace("\\", "/").TrimStart('/')
            let prefix =
                if rootDescriptor.StartsWith("minio://", StringComparison.OrdinalIgnoreCase) then rootDescriptor.TrimEnd('/')
                else $"minio://{rootDescriptor.TrimEnd('/')}"
            { LocalPath = target
              Uri = Some $"{prefix}/{normalized}"
              Bytes = Some(int (FileInfo(target).Length))
              ContentType = Some "application/octet-stream" }
        member this.WriteJson(relativePath, payload) =
            let json = JsonSerializer.Serialize(payload, JsonSerializerOptions(WriteIndented = true))
            (this :> IObjectStore).WriteText(relativePath, json)

type FilesystemObjectStore(rootDescriptor: string) =
    interface IObjectStore with
        member _.Mode = "filesystem"
        member _.RootDescriptor = rootDescriptor
        member _.WriteText(relativePath, content) =
            let target = Path.Combine(rootDescriptor, relativePath)
            let directory = Path.GetDirectoryName(target)
            if not (String.IsNullOrWhiteSpace(directory)) then
                Directory.CreateDirectory(directory) |> ignore
            File.WriteAllText(target, content)
            { LocalPath = target
              Uri = None
              Bytes = Some(int (FileInfo(target).Length))
              ContentType = Some "text/plain" }
        member _.AppendText(relativePath, content) =
            let target = Path.Combine(rootDescriptor, relativePath)
            let directory = Path.GetDirectoryName(target)
            if not (String.IsNullOrWhiteSpace(directory)) then
                Directory.CreateDirectory(directory) |> ignore
            File.AppendAllText(target, content)
            { LocalPath = target
              Uri = None
              Bytes = Some(int (FileInfo(target).Length))
              ContentType = Some "text/plain" }
        member this.WriteJson(relativePath, payload) =
            let json = JsonSerializer.Serialize(payload, JsonSerializerOptions(WriteIndented = true))
            (this :> IObjectStore).WriteText(relativePath, json)

module StorageProfiles =
    let parityDefault =
        { StateStoreMode = PostgresParity
          ObjectStoreMode = MinioParity
          Notes =
              [ "Postgres + MinIO is the preferred parity target."
                "SQLite + filesystem remains the compatibility fallback during migration." ] }

type InMemoryResearchStore() =
    let jobs = Dictionary<string, JobRecord>()
    let benchmarkRuns = Dictionary<string, BenchmarkRunRecord>()
    let agentRuns = Dictionary<string, AgentRunRecord>()
    let campaigns = Dictionary<string, CampaignRecord>()
    let programs = Dictionary<string, ProgramRecord>()
    let artifacts = Dictionary<string, ArtifactRef>()
    let workerHeartbeats = Dictionary<string, WorkerHeartbeat>()

    interface IResearchStore with
        member _.SaveJob(job) =
            jobs[job.JobId] <- job

        member _.TryGetJob(jobId) =
            match jobs.TryGetValue(jobId) with
            | true, job -> Some job
            | _ -> None

        member _.ListJobs() =
            ResizeArray(jobs.Values)

        member _.UpdateJob(jobId, updater) =
            match jobs.TryGetValue(jobId) with
            | true, job ->
                let updated = updater job
                jobs[jobId] <- updated
                Some updated
            | _ -> None

        member _.ClaimNextJob(kind) =
            let candidate =
                jobs.Values
                |> Seq.filter (fun job -> job.Status = "queued")
                |> Seq.filter (fun job ->
                    match kind with
                    | Some expected -> job.Kind = expected
                    | None -> true)
                |> Seq.sortBy (fun job -> job.CreatedAt)
                |> Seq.tryHead

            match candidate with
            | None -> None
            | Some job ->
                let updated =
                    { job with
                        Status = "running"
                        UpdatedAt = DateTime.UtcNow.ToString("O") }
                jobs[job.JobId] <- updated
                Some updated

        member _.RequeueInflightJobs(kind) =
            let now = DateTime.UtcNow.ToString("O")
            for KeyValue(jobId, job) in jobs do
                let kindMatches =
                    match kind with
                    | Some expected -> job.Kind = expected
                    | None -> true
                if kindMatches && job.Status = "running" then
                    jobs[jobId] <- { job with Status = "queued"; UpdatedAt = now }

        member _.SaveBenchmarkRun(run) =
            benchmarkRuns[run.RunId] <- run

        member _.TryGetBenchmarkRun(runId) =
            match benchmarkRuns.TryGetValue(runId) with
            | true, run -> Some run
            | _ -> None

        member _.ListBenchmarkRuns() =
            ResizeArray(benchmarkRuns.Values)

        member _.SaveAgentRun(run) =
            agentRuns[run.RunId] <- run

        member _.TryGetAgentRun(runId) =
            match agentRuns.TryGetValue(runId) with
            | true, run -> Some run
            | _ -> None

        member _.ListAgentRuns() =
            ResizeArray(agentRuns.Values)

        member _.SaveCampaign(campaign) =
            campaigns[campaign.CampaignId] <- campaign

        member _.TryGetCampaign(campaignId) =
            match campaigns.TryGetValue(campaignId) with
            | true, campaign -> Some campaign
            | _ -> None

        member _.ListCampaigns() =
            ResizeArray(campaigns.Values)

        member _.SaveProgram(program) =
            programs[program.ProgramId] <- program

        member _.TryGetProgram(programId) =
            match programs.TryGetValue(programId) with
            | true, program -> Some program
            | _ -> None

        member _.ListPrograms() =
            ResizeArray(programs.Values)

        member _.SaveArtifact(artifact) =
            artifacts[artifact.ArtifactId] <- artifact

        member _.ListArtifacts() =
            ResizeArray(artifacts.Values)

        member _.SaveWorkerHeartbeat(heartbeat) =
            workerHeartbeats[heartbeat.WorkerId] <- heartbeat

        member _.ListWorkerHeartbeats(workerKind) =
            workerHeartbeats.Values
            |> Seq.filter (fun heartbeat ->
                match workerKind with
                | Some expected -> heartbeat.WorkerKind = expected
                | None -> true)
            |> ResizeArray
