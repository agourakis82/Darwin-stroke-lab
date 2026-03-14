namespace Darwin.ResearchOs.Core

open System
open System.Diagnostics
open System.IO
open System.Text

[<RequireQualifiedAccess>]
module SnioProtocol =
    let Magic = [| 0x53uy; 0x4Euy; 0x49uy; 0x4Fuy |]

    [<Literal>]
    let MsgResult = 2uy

    [<Literal>]
    let MsgError = 3uy

    [<Literal>]
    let MsgShutdown = 4uy

    [<Literal>]
    let MsgSessionCreate = 14uy

    [<Literal>]
    let MsgSessionDestroy = 15uy

    [<Literal>]
    let MsgKernelDescribe = 16uy

    [<Literal>]
    let MsgKernelExecute = 17uy

    [<Literal>]
    let MsgKernelOutput = 18uy

    [<Literal>]
    let MsgKernelDiagnostics = 19uy

    [<Literal>]
    let MsgKernelArtifacts = 20uy

    let private writeMagic (stream: Stream) =
        stream.Write(Magic, 0, Magic.Length)

    let private writeU8 (stream: Stream) (value: byte) =
        stream.WriteByte(value)

    let private writeU16LE (stream: Stream) (value: uint16) =
        let buffer = BitConverter.GetBytes(value)
        stream.Write(buffer, 0, buffer.Length)

    let private writeU32LE (stream: Stream) (value: uint32) =
        let buffer = BitConverter.GetBytes(value)
        stream.Write(buffer, 0, buffer.Length)

    let private writeI64LE (stream: Stream) (value: int64) =
        let buffer = BitConverter.GetBytes(value)
        stream.Write(buffer, 0, buffer.Length)

    let writeShutdown (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgShutdown
        writeU32LE stream 0u
        stream.Flush()

    let writeSessionCreate (stream: Stream) (flags: int64) =
        writeMagic stream
        writeU8 stream MsgSessionCreate
        writeU32LE stream 16u
        writeI64LE stream 0L
        writeI64LE stream flags
        stream.Flush()

    let writeSessionDestroy (stream: Stream) (sessionId: int64) =
        writeMagic stream
        writeU8 stream MsgSessionDestroy
        writeU32LE stream 8u
        writeI64LE stream sessionId
        stream.Flush()

    let writeKernelDescribe (stream: Stream) (sessionId: int64) (sourcePath: string) (flags: int64) =
        let pathBytes = Encoding.UTF8.GetBytes(sourcePath)
        let bodyLen = uint32 (8 + 2 + pathBytes.Length + 8)
        writeMagic stream
        writeU8 stream MsgKernelDescribe
        writeU32LE stream bodyLen
        writeI64LE stream sessionId
        writeU16LE stream (uint16 pathBytes.Length)
        stream.Write(pathBytes, 0, pathBytes.Length)
        writeI64LE stream flags
        stream.Flush()

    let writeKernelExecute (stream: Stream) (sessionId: int64) (kernelId: int64) (args: int64[]) =
        let bodyLen = uint32 (8 + 8 + 8 + args.Length * 8)
        writeMagic stream
        writeU8 stream MsgKernelExecute
        writeU32LE stream bodyLen
        writeI64LE stream sessionId
        writeI64LE stream kernelId
        writeI64LE stream (int64 args.Length)
        for argument in args do
            writeI64LE stream argument
        stream.Flush()

    let private writeSessionMessage (stream: Stream) (msgType: byte) (sessionId: int64) =
        writeMagic stream
        writeU8 stream msgType
        writeU32LE stream 8u
        writeI64LE stream sessionId
        stream.Flush()

    let writeKernelOutput (stream: Stream) (sessionId: int64) =
        writeSessionMessage stream MsgKernelOutput sessionId

    let writeKernelDiagnostics (stream: Stream) (sessionId: int64) =
        writeSessionMessage stream MsgKernelDiagnostics sessionId

    let writeKernelArtifacts (stream: Stream) (sessionId: int64) =
        writeSessionMessage stream MsgKernelArtifacts sessionId

    let private readExact (stream: Stream) (buffer: byte[]) (count: int) =
        let mutable pos = 0
        while pos < count do
            let n = stream.Read(buffer, pos, count - pos)
            if n = 0 then
                raise (EndOfStreamException("Unexpected EOF reading SNIO response"))
            pos <- pos + n

    let private readBytes (stream: Stream) (count: int) =
        let buffer = Array.zeroCreate<byte> count
        readExact stream buffer count
        buffer

    let private readU8 (stream: Stream) =
        let value = stream.ReadByte()
        if value = -1 then
            raise (EndOfStreamException("Unexpected EOF reading SNIO byte"))
        byte value

    let private readU32LE (stream: Stream) =
        let buffer = readBytes stream 4
        BitConverter.ToUInt32(buffer, 0)

    let private readI64LE (stream: Stream) =
        let buffer = readBytes stream 8
        BitConverter.ToInt64(buffer, 0)

    [<Struct>]
    type Response =
        | ResultValues of values: int64[]
        | ErrorMessage of message: string
        | Shutdown

    let readResponse (stream: Stream) =
        let magic = readBytes stream 4
        if magic <> Magic then
            failwithf "Invalid SNIO magic: %A" magic

        let msgType = readU8 stream
        let bodyLen = readU32LE stream

        match msgType with
        | value when value = MsgResult ->
            let count = readI64LE stream
            let values = Array.init (int count) (fun _ -> readI64LE stream)
            Response.ResultValues values
        | value when value = MsgError ->
            let errLenBytes = readBytes stream 2
            let errLen = BitConverter.ToUInt16(errLenBytes, 0)
            let errBytes = readBytes stream (int errLen)
            Response.ErrorMessage(Encoding.UTF8.GetString(errBytes))
        | value when value = MsgShutdown ->
            Response.Shutdown
        | _ ->
            let _ = readBytes stream (int bodyLen)
            failwithf "Unknown SNIO message type: %d" msgType

type SounioSnioProcess(soucPath: string, stdlibPath: string option, ?serveEntryPath: string) =
    let mutable proc: Process option = None
    let mutable disposed = false

    let startProcess () =
        let psi = ProcessStartInfo()
        psi.FileName <- soucPath
        match serveEntryPath with
        | Some entry -> psi.Arguments <- sprintf "run %s" entry
        | None -> psi.Arguments <- "--serve"
        psi.UseShellExecute <- false
        psi.RedirectStandardInput <- true
        psi.RedirectStandardOutput <- true
        psi.RedirectStandardError <- true
        psi.CreateNoWindow <- true

        match stdlibPath with
        | Some path when not (String.IsNullOrWhiteSpace(path)) -> psi.Environment["SOUNIO_STDLIB_PATH"] <- path
        | _ -> ()

        let child = Process.Start(psi)
        proc <- Some child

        try
            match SnioProtocol.readResponse child.StandardOutput.BaseStream with
            | SnioProtocol.Response.ResultValues [| 0L |] -> child
            | SnioProtocol.Response.ResultValues _ -> child
            | SnioProtocol.Response.ErrorMessage message ->
                let stderr = child.StandardError.ReadToEnd()
                failwithf "Sounio SNIO startup error: %s%s" message (if String.IsNullOrWhiteSpace(stderr) then "" else $" | stderr: {stderr.Trim()}")
            | SnioProtocol.Response.Shutdown ->
                let stderr = child.StandardError.ReadToEnd()
                failwithf "Sounio SNIO server shut down during startup.%s" (if String.IsNullOrWhiteSpace(stderr) then "" else $" stderr: {stderr.Trim()}")
        with error ->
            let stderr =
                try child.StandardError.ReadToEnd()
                with _ -> ""
            if not child.HasExited then
                try child.Kill(true)
                with _ -> ()
            proc <- None
            let extra =
                if String.IsNullOrWhiteSpace(stderr) then ""
                else $" stderr: {stderr.Trim()}"
            let invocation =
                match serveEntryPath with
                | Some entry -> sprintf "'%s run %s'" soucPath entry
                | None -> sprintf "'%s --serve'" soucPath
            failwithf "Could not start Sounio SNIO process with %s: %s%s" invocation error.Message extra

    let ensureStarted () =
        match proc with
        | Some child when not child.HasExited -> child
        | _ -> startProcess ()

    member _.SessionCreate(?flags: int64) =
        let child = ensureStarted ()
        SnioProtocol.writeSessionCreate child.StandardInput.BaseStream (defaultArg flags 0L)
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues [| sessionId |] -> sessionId
        | SnioProtocol.Response.ErrorMessage message -> failwithf "SNIO SessionCreate error: %s" message
        | _ -> failwith "Unexpected SNIO response from SessionCreate"

    member _.SessionDestroy(sessionId: int64) =
        let child = ensureStarted ()
        SnioProtocol.writeSessionDestroy child.StandardInput.BaseStream sessionId
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues [| 1L |]
        | SnioProtocol.Response.ResultValues [||] -> ()
        | SnioProtocol.Response.ErrorMessage message -> failwithf "SNIO SessionDestroy error: %s" message
        | _ -> failwith "Unexpected SNIO response from SessionDestroy"

    member _.KernelDescribe(sessionId: int64, sourcePath: string, ?flags: int64) =
        let child = ensureStarted ()
        SnioProtocol.writeKernelDescribe child.StandardInput.BaseStream sessionId sourcePath (defaultArg flags 0L)
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "SNIO KernelDescribe error: %s" message
        | _ -> failwith "Unexpected SNIO response from KernelDescribe"

    member _.KernelExecute(sessionId: int64, kernelId: int64, args: int64[]) =
        let child = ensureStarted ()
        SnioProtocol.writeKernelExecute child.StandardInput.BaseStream sessionId kernelId args
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "SNIO KernelExecute error: %s" message
        | _ -> failwith "Unexpected SNIO response from KernelExecute"

    member _.KernelOutput(sessionId: int64) =
        let child = ensureStarted ()
        SnioProtocol.writeKernelOutput child.StandardInput.BaseStream sessionId
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "SNIO KernelOutput error: %s" message
        | _ -> failwith "Unexpected SNIO response from KernelOutput"

    member _.KernelDiagnostics(sessionId: int64) =
        let child = ensureStarted ()
        SnioProtocol.writeKernelDiagnostics child.StandardInput.BaseStream sessionId
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "SNIO KernelDiagnostics error: %s" message
        | _ -> failwith "Unexpected SNIO response from KernelDiagnostics"

    member _.KernelArtifacts(sessionId: int64) =
        let child = ensureStarted ()
        SnioProtocol.writeKernelArtifacts child.StandardInput.BaseStream sessionId
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "SNIO KernelArtifacts error: %s" message
        | _ -> failwith "Unexpected SNIO response from KernelArtifacts"

    member _.Shutdown() =
        match proc with
        | Some child when not child.HasExited ->
            try
                SnioProtocol.writeShutdown child.StandardInput.BaseStream
                child.WaitForExit(5000) |> ignore
                if not child.HasExited then
                    child.Kill(true)
            with _ ->
                try child.Kill(true) with _ -> ()
            proc <- None
        | _ -> proc <- None

    interface IDisposable with
        member this.Dispose() =
            if not disposed then
                disposed <- true
                this.Shutdown()
