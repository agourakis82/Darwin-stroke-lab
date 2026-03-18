namespace Darwin.ResearchOs.Storage

open System
open System.Collections.Generic
open System.Text.Json
open System.Text.Json.Serialization
open Darwin.ResearchOs.Contracts
open Darwin.ResearchOs.Core
open Npgsql

module internal StoreJson =
    let rec private elementToObject (element: JsonElement) : obj =
        match element.ValueKind with
        | JsonValueKind.Object ->
            let mapped = Dictionary<string, obj>()
            for property in element.EnumerateObject() do
                mapped[property.Name] <- elementToObject property.Value
            box mapped
        | JsonValueKind.Array ->
            let mapped = ResizeArray<obj>()
            for item in element.EnumerateArray() do
                mapped.Add(elementToObject item)
            box mapped
        | JsonValueKind.String -> box (element.GetString())
        | JsonValueKind.Number ->
            match element.TryGetInt64() with
            | true, value -> box value
            | _ -> box (element.GetDouble())
        | JsonValueKind.True
        | JsonValueKind.False -> box (element.GetBoolean())
        | JsonValueKind.Null
        | JsonValueKind.Undefined -> null
        | _ -> box (element.ToString())

    type private ObjectJsonConverter() =
        inherit JsonConverter<obj>()

        override _.Read(reader: byref<Utf8JsonReader>, _typeToConvert: Type, _options: JsonSerializerOptions) : obj =
            use document = JsonDocument.ParseValue(&reader)
            elementToObject document.RootElement

        override _.Write(writer: Utf8JsonWriter, value: obj, options: JsonSerializerOptions) =
            if isNull value then
                writer.WriteNullValue()
            else
                JsonSerializer.Serialize(writer, value, value.GetType(), options)

    let options =
        let configured = JsonSerializerOptions(PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, WriteIndented = true)
        configured.PropertyNameCaseInsensitive <- true
        configured.Converters.Add(ObjectJsonConverter())
        configured

    let serialize (value: 'T) =
        JsonSerializer.Serialize(value, options)

    let deserialize<'T> (payload: string) =
        JsonSerializer.Deserialize<'T>(payload, options)

module internal PgHelpers =
    let openConnection connectionString =
        let connection = new NpgsqlConnection(connectionString)
        connection.Open()
        connection

    let execute connectionString (sql: string) =
        use connection = openConnection connectionString
        use command = new NpgsqlCommand(sql, connection)
        command.ExecuteNonQuery() |> ignore

    let loadOptional<'T> connectionString (sql: string) (configure: NpgsqlCommand -> unit) =
        use connection = openConnection connectionString
        use command = new NpgsqlCommand(sql, connection)
        configure command
        use reader = command.ExecuteReader()
        if reader.Read() then
            let payload = reader.GetString(0)
            Some(StoreJson.deserialize<'T> payload)
        else
            None

    let loadMany<'T> connectionString (sql: string) (configure: NpgsqlCommand -> unit) =
        use connection = openConnection connectionString
        use command = new NpgsqlCommand(sql, connection)
        configure command
        use reader = command.ExecuteReader()
        let items = ResizeArray<'T>()
        while reader.Read() do
            items.Add(StoreJson.deserialize<'T>(reader.GetString(0)))
        items

    let savePayload connectionString (sql: string) (configure: NpgsqlCommand -> unit) =
        use connection = openConnection connectionString
        use command = new NpgsqlCommand(sql, connection)
        configure command
        command.ExecuteNonQuery() |> ignore

type PostgresResearchStore(connectionString: string) =
    let upsertPayload tableName idColumn idValue statusColumn statusValue updatedAtColumn updatedAt payload =
        let sql =
            $"""
            INSERT INTO {tableName} ({idColumn}, {statusColumn}, {updatedAtColumn}, payload)
            VALUES (@id, @status, CAST(@updated_at AS timestamptz), CAST(@payload AS jsonb))
            ON CONFLICT ({idColumn}) DO UPDATE
            SET {statusColumn} = EXCLUDED.{statusColumn},
                {updatedAtColumn} = EXCLUDED.{updatedAtColumn},
                payload = EXCLUDED.payload
            """

        PgHelpers.savePayload connectionString sql (fun command ->
            command.Parameters.AddWithValue("id", idValue) |> ignore
            command.Parameters.AddWithValue("status", statusValue) |> ignore
            command.Parameters.AddWithValue("updated_at", updatedAt) |> ignore
            command.Parameters.AddWithValue("payload", payload) |> ignore)

    let upsertSimple tableName idColumn idValue payload =
        let sql =
            $"""
            INSERT INTO {tableName} ({idColumn}, payload)
            VALUES (@id, CAST(@payload AS jsonb))
            ON CONFLICT ({idColumn}) DO UPDATE
            SET payload = EXCLUDED.payload
            """

        PgHelpers.savePayload connectionString sql (fun command ->
            command.Parameters.AddWithValue("id", idValue) |> ignore
            command.Parameters.AddWithValue("payload", payload) |> ignore)

    interface IResearchStore with
        member _.SaveJob(job) =
            let sql =
                """
                INSERT INTO jobs (job_id, owner_type, owner_id, kind, status, updated_at, payload)
                VALUES (@job_id, @owner_type, @owner_id, @kind, @status, CAST(@updated_at AS timestamptz), CAST(@payload AS jsonb))
                ON CONFLICT (job_id) DO UPDATE
                SET owner_type = EXCLUDED.owner_type,
                    owner_id = EXCLUDED.owner_id,
                    kind = EXCLUDED.kind,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at,
                    payload = EXCLUDED.payload
                """

            PgHelpers.savePayload connectionString sql (fun command ->
                command.Parameters.AddWithValue("job_id", job.JobId) |> ignore
                command.Parameters.AddWithValue("owner_type", job.OwnerType) |> ignore
                command.Parameters.AddWithValue("owner_id", job.OwnerId) |> ignore
                command.Parameters.AddWithValue("kind", job.Kind) |> ignore
                command.Parameters.AddWithValue("status", job.Status) |> ignore
                command.Parameters.AddWithValue("updated_at", job.UpdatedAt) |> ignore
                command.Parameters.AddWithValue("payload", StoreJson.serialize job) |> ignore)

        member this.TryGetJob(jobId) =
            PgHelpers.loadOptional<JobRecord> connectionString
                "SELECT payload::text FROM jobs WHERE job_id = @id"
                (fun command -> command.Parameters.AddWithValue("id", jobId) |> ignore)

        member this.ListJobs() =
            PgHelpers.loadMany<JobRecord> connectionString
                "SELECT payload::text FROM jobs ORDER BY updated_at DESC"
                ignore

        member this.UpdateJob(jobId, updater) =
            match (this :> IResearchStore).TryGetJob(jobId) with
            | None -> None
            | Some current ->
                let updated = updater current
                (this :> IResearchStore).SaveJob(updated)
                Some updated

        member _.ClaimNextJob(kind) =
            use connection = PgHelpers.openConnection connectionString
            use tx = connection.BeginTransaction()

            let query =
                match kind with
                | Some _ ->
                    """
                    SELECT job_id, payload::text
                    FROM jobs
                    WHERE status = 'queued' AND kind = @kind
                    ORDER BY updated_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """
                | None ->
                    """
                    SELECT job_id, payload::text
                    FROM jobs
                    WHERE status = 'queued'
                    ORDER BY updated_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """

            use selectCommand = new NpgsqlCommand(query, connection, tx)
            match kind with
            | Some value -> selectCommand.Parameters.AddWithValue("kind", value) |> ignore
            | None -> ()

            use reader = selectCommand.ExecuteReader()
            if not (reader.Read()) then
                None
            else
                let jobId = reader.GetString(0)
                let payload = reader.GetString(1)
                reader.Close()

                let current = StoreJson.deserialize<JobRecord>(payload)
                let updated =
                    { current with
                        Status = "running"
                        UpdatedAt = DateTime.UtcNow.ToString("O") }

                use updateCommand =
                    new NpgsqlCommand(
                        """
                        UPDATE jobs
                        SET status = @status, updated_at = CAST(@updated_at AS timestamptz), payload = CAST(@payload AS jsonb)
                        WHERE job_id = @job_id
                        """,
                        connection,
                        tx
                    )

                updateCommand.Parameters.AddWithValue("status", updated.Status) |> ignore
                updateCommand.Parameters.AddWithValue("updated_at", updated.UpdatedAt) |> ignore
                updateCommand.Parameters.AddWithValue("payload", StoreJson.serialize updated) |> ignore
                updateCommand.Parameters.AddWithValue("job_id", updated.JobId) |> ignore
                updateCommand.ExecuteNonQuery() |> ignore
                tx.Commit()
                Some updated

        member this.RequeueInflightJobs(kind) =
            let sql =
                match kind with
                | Some _ -> "SELECT payload::text FROM jobs WHERE status = 'running' AND kind = @kind"
                | None -> "SELECT payload::text FROM jobs WHERE status = 'running'"

            let inflight =
                PgHelpers.loadMany<JobRecord> connectionString
                    sql
                    (fun command ->
                        match kind with
                        | Some value -> command.Parameters.AddWithValue("kind", value) |> ignore
                        | None -> ())

            for job in inflight do
                let payload = Dictionary<string, obj>(job.ResultPayload)
                payload["recovered_after_restart"] <- box true
                (this :> IResearchStore).SaveJob(
                    { job with
                        Status = "queued"
                        UpdatedAt = DateTime.UtcNow.ToString("O")
                        ResultPayload = payload }
                )

        member _.SaveBenchmarkRun(run) =
            upsertSimple "benchmark_runs" "run_id" run.RunId (StoreJson.serialize run)

        member _.TryGetBenchmarkRun(runId) =
            PgHelpers.loadOptional<BenchmarkRunRecord> connectionString
                "SELECT payload::text FROM benchmark_runs WHERE run_id = @id"
                (fun command -> command.Parameters.AddWithValue("id", runId) |> ignore)

        member _.ListBenchmarkRuns() =
            PgHelpers.loadMany<BenchmarkRunRecord> connectionString
                "SELECT payload::text FROM benchmark_runs ORDER BY run_id ASC"
                ignore

        member _.SaveAgentRun(run) =
            upsertPayload
                "agent_runs"
                "run_id"
                run.RunId
                "status"
                run.Status
                "updated_at"
                run.UpdatedAt
                (StoreJson.serialize run)

        member _.TryGetAgentRun(runId) =
            PgHelpers.loadOptional<AgentRunRecord> connectionString
                "SELECT payload::text FROM agent_runs WHERE run_id = @id"
                (fun command -> command.Parameters.AddWithValue("id", runId) |> ignore)

        member _.ListAgentRuns() =
            PgHelpers.loadMany<AgentRunRecord> connectionString
                "SELECT payload::text FROM agent_runs ORDER BY updated_at DESC"
                ignore

        member _.SaveCampaign(campaign) =
            upsertPayload
                "campaigns"
                "campaign_id"
                campaign.CampaignId
                "status"
                campaign.Status
                "updated_at"
                campaign.UpdatedAt
                (StoreJson.serialize campaign)

        member _.TryGetCampaign(campaignId) =
            PgHelpers.loadOptional<CampaignRecord> connectionString
                "SELECT payload::text FROM campaigns WHERE campaign_id = @id"
                (fun command -> command.Parameters.AddWithValue("id", campaignId) |> ignore)

        member _.ListCampaigns() =
            PgHelpers.loadMany<CampaignRecord> connectionString
                "SELECT payload::text FROM campaigns ORDER BY updated_at DESC"
                ignore

        member _.SaveProgram(program) =
            upsertPayload
                "programs"
                "program_id"
                program.ProgramId
                "status"
                program.Status
                "updated_at"
                program.UpdatedAt
                (StoreJson.serialize program)

        member _.TryGetProgram(programId) =
            PgHelpers.loadOptional<ProgramRecord> connectionString
                "SELECT payload::text FROM programs WHERE program_id = @id"
                (fun command -> command.Parameters.AddWithValue("id", programId) |> ignore)

        member _.ListPrograms() =
            PgHelpers.loadMany<ProgramRecord> connectionString
                "SELECT payload::text FROM programs ORDER BY updated_at DESC"
                ignore

        member _.SaveArtifact(artifact) =
            let sql =
                """
                INSERT INTO artifact_refs (artifact_id, owner_type, owner_id, created_at, payload)
                VALUES (@artifact_id, @owner_type, @owner_id, CAST(@created_at AS timestamptz), CAST(@payload AS jsonb))
                ON CONFLICT (artifact_id) DO UPDATE
                SET owner_type = EXCLUDED.owner_type,
                    owner_id = EXCLUDED.owner_id,
                    created_at = EXCLUDED.created_at,
                    payload = EXCLUDED.payload
                """
            PgHelpers.savePayload connectionString sql (fun command ->
                command.Parameters.AddWithValue("artifact_id", artifact.ArtifactId) |> ignore
                command.Parameters.AddWithValue("owner_type", artifact.OwnerType) |> ignore
                command.Parameters.AddWithValue("owner_id", artifact.OwnerId) |> ignore
                command.Parameters.AddWithValue("created_at", artifact.CreatedAt) |> ignore
                command.Parameters.AddWithValue("payload", StoreJson.serialize artifact) |> ignore)

        member _.ListArtifacts() =
            PgHelpers.loadMany<ArtifactRef> connectionString
                "SELECT payload::text FROM artifact_refs ORDER BY created_at ASC"
                ignore

        member _.SaveWorkerHeartbeat(heartbeat) =
            let sql =
                """
                INSERT INTO worker_heartbeats (worker_id, worker_kind, updated_at, payload)
                VALUES (@worker_id, @worker_kind, CAST(@updated_at AS timestamptz), CAST(@payload AS jsonb))
                ON CONFLICT (worker_id) DO UPDATE
                SET worker_kind = EXCLUDED.worker_kind,
                    updated_at = EXCLUDED.updated_at,
                    payload = EXCLUDED.payload
                """
            PgHelpers.savePayload connectionString sql (fun command ->
                command.Parameters.AddWithValue("worker_id", heartbeat.WorkerId) |> ignore
                command.Parameters.AddWithValue("worker_kind", heartbeat.WorkerKind) |> ignore
                command.Parameters.AddWithValue("updated_at", heartbeat.UpdatedAt) |> ignore
                command.Parameters.AddWithValue("payload", StoreJson.serialize heartbeat) |> ignore)

        member _.ListWorkerHeartbeats(workerKind) =
            let sql =
                match workerKind with
                | Some _ -> "SELECT payload::text FROM worker_heartbeats WHERE worker_kind = @worker_kind ORDER BY updated_at DESC"
                | None -> "SELECT payload::text FROM worker_heartbeats ORDER BY updated_at DESC"

            PgHelpers.loadMany<WorkerHeartbeat> connectionString
                sql
                (fun command ->
                    match workerKind with
                    | Some kind -> command.Parameters.AddWithValue("worker_kind", kind) |> ignore
                    | None -> ())

    member _.Initialize() =
        PgHelpers.execute connectionString
            """
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                run_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS campaigns (
                campaign_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS programs (
                program_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifact_refs (
                artifact_id TEXT PRIMARY KEY,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS worker_heartbeats (
                worker_id TEXT PRIMARY KEY,
                worker_kind TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status_kind_updated ON jobs (status, kind, updated_at);
            CREATE INDEX IF NOT EXISTS idx_campaigns_status_updated ON campaigns (status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_programs_status_updated ON programs (status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_status_updated ON agent_runs (status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_artifact_refs_owner ON artifact_refs (owner_type, owner_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_kind_updated ON worker_heartbeats (worker_kind, updated_at);
            """
