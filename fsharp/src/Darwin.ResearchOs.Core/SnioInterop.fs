namespace Darwin.ResearchOs.Core

open System
open System.Diagnostics
open System.IO
open System.Runtime.InteropServices

/// Darwin-local mirror of Sounio's official interop/fsharp layer.
/// Keep this close to upstream so Darwin showcases the real Sounio ABI,
/// while still allowing Darwin-specific environment/bootstrap logic.
[<RequireQualifiedAccess>]
module SnioProtocol =
    let Magic = [| 0x53uy; 0x4Euy; 0x49uy; 0x4Fuy |]

    [<Literal>]
    let MsgCallFunc = 1uy

    [<Literal>]
    let MsgResult = 2uy

    [<Literal>]
    let MsgError = 3uy

    [<Literal>]
    let MsgShutdown = 4uy

    [<Literal>]
    let MsgInfo = 5uy

    [<Literal>]
    let MsgCapabilities = 6uy

    [<Literal>]
    let MsgCompile = 7uy

    [<Literal>]
    let MsgDiagnostics = 9uy

    [<Literal>]
    let MsgHealth = 10uy

    [<Literal>]
    let MsgGpuCaps = 11uy

    [<Literal>]
    let MsgInit = 12uy

    [<Literal>]
    let MsgStats = 13uy

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

    let writeMagic (stream: Stream) =
        stream.Write(Magic, 0, Magic.Length)

    let writeU8 (stream: Stream) (value: byte) =
        stream.WriteByte(value)

    let writeU16LE (stream: Stream) (value: uint16) =
        let buffer = BitConverter.GetBytes(value)
        stream.Write(buffer, 0, buffer.Length)

    let writeU32LE (stream: Stream) (value: uint32) =
        let buffer = BitConverter.GetBytes(value)
        stream.Write(buffer, 0, buffer.Length)

    let writeI64LE (stream: Stream) (value: int64) =
        let buffer = BitConverter.GetBytes(value)
        stream.Write(buffer, 0, buffer.Length)

    let writeCallFunc (stream: Stream) (funcName: string) (args: int64[]) =
        let nameBytes = Text.Encoding.UTF8.GetBytes(funcName)
        let bodyLen = uint32 (2 + nameBytes.Length + 8 + args.Length * 8)

        writeMagic stream
        writeU8 stream MsgCallFunc
        writeU32LE stream bodyLen
        writeU16LE stream (uint16 nameBytes.Length)
        stream.Write(nameBytes, 0, nameBytes.Length)
        writeI64LE stream (int64 args.Length)
        for argument in args do
            writeI64LE stream argument
        stream.Flush()

    let writeCallFuncF64 (stream: Stream) (funcName: string) (args: float[]) =
        let rawArgs = args |> Array.map BitConverter.DoubleToInt64Bits
        writeCallFunc stream funcName rawArgs

    let writeShutdown (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgShutdown
        writeU32LE stream 0u
        stream.Flush()

    let writeInfo (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgInfo
        writeU32LE stream 0u
        stream.Flush()

    let writeCapabilities (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgCapabilities
        writeU32LE stream 0u
        stream.Flush()

    let writeCompile (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgCompile
        writeU32LE stream 0u
        stream.Flush()

    let writeDiagnostics (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgDiagnostics
        writeU32LE stream 0u
        stream.Flush()

    let writeHealth (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgHealth
        writeU32LE stream 0u
        stream.Flush()

    let writeGpuCaps (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgGpuCaps
        writeU32LE stream 0u
        stream.Flush()

    let writeInit (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgInit
        writeU32LE stream 0u
        stream.Flush()

    let writeStats (stream: Stream) =
        writeMagic stream
        writeU8 stream MsgStats
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
        let pathBytes = Text.Encoding.UTF8.GetBytes(sourcePath)
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

    let private writeSessionQuery (stream: Stream) (msgType: byte) (sessionId: int64) =
        writeMagic stream
        writeU8 stream msgType
        writeU32LE stream 8u
        writeI64LE stream sessionId
        stream.Flush()

    let writeKernelOutput (stream: Stream) (sessionId: int64) =
        writeSessionQuery stream MsgKernelOutput sessionId

    let writeKernelDiagnostics (stream: Stream) (sessionId: int64) =
        writeSessionQuery stream MsgKernelDiagnostics sessionId

    let writeKernelArtifacts (stream: Stream) (sessionId: int64) =
        writeSessionQuery stream MsgKernelArtifacts sessionId

    let private readExact (stream: Stream) (buffer: byte[]) (offset: int) (count: int) =
        let mutable read = 0
        while read < count do
            let countRead = stream.Read(buffer, offset + read, count - read)
            if countRead = 0 then
                raise (EndOfStreamException("Unexpected EOF reading from Sounio process"))
            read <- read + countRead

    let private readBytes (stream: Stream) (count: int) =
        let buffer = Array.zeroCreate<byte> count
        readExact stream buffer 0 count
        buffer

    let private readU8 (stream: Stream) =
        let value = stream.ReadByte()
        if value = -1 then
            raise (EndOfStreamException("Unexpected EOF reading SNIO byte"))
        byte value

    let private readU16LE (stream: Stream) =
        let buffer = readBytes stream 2
        BitConverter.ToUInt16(buffer, 0)

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
            let errorLength = readU16LE stream
            let errorBytes = readBytes stream (int errorLength)
            Response.ErrorMessage(Text.Encoding.UTF8.GetString(errorBytes))
        | value when value = MsgShutdown ->
            Response.Shutdown
        | _ ->
            let _ = readBytes stream (int bodyLen)
            failwithf "Unknown SNIO message type: %d" msgType

/// Mirrored from upstream SounioProcess with Darwin-specific env/bootstrap hooks.
type SounioProcess
    (
        soucPath: string,
        ?programPath: string,
        ?extraArgs: string[],
        ?environment: seq<string * string>
    ) =

    let mutable proc: Process option = None
    let mutable disposed = false

    let startProcess () =
        let psi = ProcessStartInfo()
        psi.FileName <- soucPath
        psi.UseShellExecute <- false
        psi.RedirectStandardInput <- true
        psi.RedirectStandardOutput <- true
        psi.RedirectStandardError <- true
        psi.CreateNoWindow <- true

        match programPath with
        | Some path ->
            psi.ArgumentList.Add("run")
            psi.ArgumentList.Add(path)
            psi.ArgumentList.Add("--")
            psi.ArgumentList.Add("--serve")
        | None ->
            psi.ArgumentList.Add("--serve")

        match extraArgs with
        | Some args ->
            for argument in args do
                psi.ArgumentList.Add(argument)
        | None -> ()

        match environment with
        | Some pairs ->
            for key, value in pairs do
                psi.Environment[key] <- value
        | None -> ()

        let child = Process.Start(psi)
        proc <- Some child

        try
            match SnioProtocol.readResponse child.StandardOutput.BaseStream with
            | SnioProtocol.Response.ResultValues [| 0L |]
            | SnioProtocol.Response.ResultValues _ -> child
            | SnioProtocol.Response.ErrorMessage message ->
                let stderr = child.StandardError.ReadToEnd()
                failwithf
                    "Sounio process startup error: %s%s"
                    message
                    (if String.IsNullOrWhiteSpace(stderr) then "" else $" | stderr: {stderr.Trim()}")
            | SnioProtocol.Response.Shutdown ->
                let stderr = child.StandardError.ReadToEnd()
                failwithf
                    "Sounio process shut down during startup.%s"
                    (if String.IsNullOrWhiteSpace(stderr) then "" else $" stderr: {stderr.Trim()}")
        with error ->
            let stderr =
                try child.StandardError.ReadToEnd()
                with _ -> ""

            if not child.HasExited then
                try
                    child.Kill(true)
                with _ ->
                    ()

            proc <- None
            let invocation =
                let renderedArgs =
                    psi.ArgumentList
                    |> Seq.map (fun value -> if value.Contains(" ") then $"\"{value}\"" else value)
                    |> String.concat " "
                $"{soucPath} {renderedArgs}"
            let extra =
                if String.IsNullOrWhiteSpace(stderr) then ""
                else $" stderr: {stderr.Trim()}"
            failwithf "Could not start Sounio process with '%s': %s%s" invocation error.Message extra

    let ensureStarted () =
        match proc with
        | Some child when not child.HasExited -> child
        | _ -> startProcess ()

    member _.CallRaw(funcName: string, args: int64[]) : int64[] =
        let child = ensureStarted ()
        SnioProtocol.writeCallFunc child.StandardInput.BaseStream funcName args
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "Sounio error in '%s': %s" funcName message
        | SnioProtocol.Response.Shutdown -> failwith "Unexpected shutdown"

    member this.CallF64(funcName: string, args: float[]) : float[] =
        let rawArgs = args |> Array.map BitConverter.DoubleToInt64Bits
        let rawValues = this.CallRaw(funcName, rawArgs)
        rawValues |> Array.map BitConverter.Int64BitsToDouble

    member this.CallScalar(funcName: string, args: int64[]) : int64 =
        let values = this.CallRaw(funcName, args)
        if values.Length = 0 then
            failwithf "Expected result from '%s', got empty" funcName
        values[0]

    member this.CallScalarF64(funcName: string, args: float[]) : float =
        let values = this.CallF64(funcName, args)
        if values.Length = 0 then
            failwithf "Expected result from '%s', got empty" funcName
        values[0]

    member this.DotProduct(a: float[], b: float[]) : float =
        if a.Length <> b.Length then
            invalidArg "b" "Vectors must have equal length"
        this.CallScalarF64("dot_product", Array.append a b)

    member this.VecAdd(a: float[], b: float[]) : float[] =
        if a.Length <> b.Length then
            invalidArg "b" "Vectors must have equal length"
        this.CallF64("vec_add", Array.append a b)

    member this.VecScale(scalar: float, values: float[]) : float[] =
        this.CallF64("vec_scale", Array.append [| scalar |] values)

    member this.Sum(values: float[]) : float =
        this.CallScalarF64("sum", values)

    member _.SessionCreate(?flags: int64) : int64 =
        let child = ensureStarted ()
        SnioProtocol.writeSessionCreate child.StandardInput.BaseStream (defaultArg flags 0L)
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues [| sessionId |] -> sessionId
        | SnioProtocol.Response.ErrorMessage message -> failwithf "SessionCreate error: %s" message
        | _ -> failwith "Unexpected response from SessionCreate"

    member _.SessionDestroy(sessionId: int64) =
        let child = ensureStarted ()
        SnioProtocol.writeSessionDestroy child.StandardInput.BaseStream sessionId
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues [| 1L |]
        | SnioProtocol.Response.ResultValues [||] -> ()
        | SnioProtocol.Response.ErrorMessage message -> failwithf "SessionDestroy error: %s" message
        | _ -> failwith "Unexpected response from SessionDestroy"

    member _.KernelDescribe(sessionId: int64, sourcePath: string, ?flags: int64) : int64[] =
        let child = ensureStarted ()
        SnioProtocol.writeKernelDescribe child.StandardInput.BaseStream sessionId sourcePath (defaultArg flags 0L)
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "KernelDescribe error: %s" message
        | _ -> failwith "Unexpected response from KernelDescribe"

    member _.KernelExecute(sessionId: int64, kernelId: int64, args: int64[]) : int64[] =
        let child = ensureStarted ()
        SnioProtocol.writeKernelExecute child.StandardInput.BaseStream sessionId kernelId args
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "KernelExecute error: %s" message
        | _ -> failwith "Unexpected response from KernelExecute"

    member _.KernelOutput(sessionId: int64) : int64[] =
        let child = ensureStarted ()
        SnioProtocol.writeKernelOutput child.StandardInput.BaseStream sessionId
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "KernelOutput error: %s" message
        | _ -> failwith "Unexpected response from KernelOutput"

    member _.KernelDiagnostics(sessionId: int64) : int64[] =
        let child = ensureStarted ()
        SnioProtocol.writeKernelDiagnostics child.StandardInput.BaseStream sessionId
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "KernelDiagnostics error: %s" message
        | _ -> failwith "Unexpected response from KernelDiagnostics"

    member _.KernelArtifacts(sessionId: int64) : int64[] =
        let child = ensureStarted ()
        SnioProtocol.writeKernelArtifacts child.StandardInput.BaseStream sessionId
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "KernelArtifacts error: %s" message
        | _ -> failwith "Unexpected response from KernelArtifacts"

    member _.Info() : int64[] =
        let child = ensureStarted ()
        SnioProtocol.writeInfo child.StandardInput.BaseStream
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues values -> values
        | SnioProtocol.Response.ErrorMessage message -> failwithf "Info error: %s" message
        | _ -> failwith "Unexpected response from Info"

    member _.Capabilities() : int64 =
        let child = ensureStarted ()
        SnioProtocol.writeCapabilities child.StandardInput.BaseStream
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues [| value |] -> value
        | SnioProtocol.Response.ErrorMessage message -> failwithf "Capabilities error: %s" message
        | _ -> failwith "Unexpected response from Capabilities"

    member _.Health() : bool =
        let child = ensureStarted ()
        SnioProtocol.writeHealth child.StandardInput.BaseStream
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues [| 1L |] -> true
        | _ -> false

    member _.Init() : bool =
        let child = ensureStarted ()
        SnioProtocol.writeInit child.StandardInput.BaseStream
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues [| 1L |] -> true
        | _ -> false

    member _.GpuCaps() : int64 =
        let child = ensureStarted ()
        SnioProtocol.writeGpuCaps child.StandardInput.BaseStream
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues [| value |] -> value
        | SnioProtocol.Response.ErrorMessage message -> failwithf "GpuCaps error: %s" message
        | _ -> failwith "Unexpected response from GpuCaps"

    member _.Stats() : int64 =
        let child = ensureStarted ()
        SnioProtocol.writeStats child.StandardInput.BaseStream
        match SnioProtocol.readResponse child.StandardOutput.BaseStream with
        | SnioProtocol.Response.ResultValues [| value |] -> value
        | SnioProtocol.Response.ErrorMessage message -> failwithf "Stats error: %s" message
        | _ -> failwith "Unexpected response from Stats"

    member this.Ping() : bool =
        try
            this.CallScalar("ping", [||]) = 1L
        with _ ->
            false

    member _.Shutdown() =
        match proc with
        | Some child when not child.HasExited ->
            try
                SnioProtocol.writeShutdown child.StandardInput.BaseStream
                child.WaitForExit(5000) |> ignore
                if not child.HasExited then
                    child.Kill(true)
            with _ ->
                try
                    child.Kill(true)
                with _ ->
                    ()
            proc <- None
        | _ ->
            proc <- None

    interface IDisposable with
        member this.Dispose() =
            if not disposed then
                disposed <- true
                this.Shutdown()

/// Thin Darwin compatibility wrapper over the upstream-shaped process type.
type SounioSnioProcess(soucPath: string, stdlibPath: string option, ?serveEntryPath: string) =
    let environment =
        match stdlibPath with
        | Some path when not (String.IsNullOrWhiteSpace(path)) -> [ "SOUNIO_STDLIB_PATH", path ]
        | _ -> []

    let inner =
        match serveEntryPath with
        | Some entry -> new SounioProcess(soucPath, programPath = entry, environment = environment)
        | None -> new SounioProcess(soucPath, environment = environment)

    member _.CallRaw(funcName: string, args: int64[]) = inner.CallRaw(funcName, args)
    member _.CallF64(funcName: string, args: float[]) = inner.CallF64(funcName, args)
    member _.CallScalar(funcName: string, args: int64[]) = inner.CallScalar(funcName, args)
    member _.CallScalarF64(funcName: string, args: float[]) = inner.CallScalarF64(funcName, args)
    member _.SessionCreate(?flags: int64) = inner.SessionCreate(?flags = flags)
    member _.SessionDestroy(sessionId: int64) = inner.SessionDestroy(sessionId)
    member _.KernelDescribe(sessionId: int64, sourcePath: string, ?flags: int64) = inner.KernelDescribe(sessionId, sourcePath, ?flags = flags)
    member _.KernelExecute(sessionId: int64, kernelId: int64, args: int64[]) = inner.KernelExecute(sessionId, kernelId, args)
    member _.KernelOutput(sessionId: int64) = inner.KernelOutput(sessionId)
    member _.KernelDiagnostics(sessionId: int64) = inner.KernelDiagnostics(sessionId)
    member _.KernelArtifacts(sessionId: int64) = inner.KernelArtifacts(sessionId)
    member _.Info() = inner.Info()
    member _.Capabilities() = inner.Capabilities()
    member _.Health() = inner.Health()
    member _.Init() = inner.Init()
    member _.GpuCaps() = inner.GpuCaps()
    member _.Stats() = inner.Stats()
    member _.Ping() = inner.Ping()
    member _.Shutdown() = inner.Shutdown()
    member _.Inner = inner

    interface IDisposable with
        member _.Dispose() =
            (inner :> IDisposable).Dispose()

/// Direct shared-library call path, mirrored from Sounio's official NativeKernel.fs.
type NativeKernel(libraryPath: string) =
    let handle =
        let loaded = NativeLibrary.Load(libraryPath)
        if loaded = IntPtr.Zero then
            failwithf "NativeKernel: failed to load '%s'" libraryPath
        loaded

    let mutable disposed = false

    member _.GetExport(funcName: string) : IntPtr =
        let exportAddress = NativeLibrary.GetExport(handle, funcName)
        if exportAddress = IntPtr.Zero then
            failwithf "NativeKernel: symbol '%s' not found in '%s'" funcName libraryPath
        exportAddress

    member _.HasExport(funcName: string) : bool =
        let mutable exportAddress = IntPtr.Zero
        NativeLibrary.TryGetExport(handle, funcName, &exportAddress)

    member _.Call0(funcName: string) : int64 =
        let exportAddress = NativeLibrary.GetExport(handle, funcName)
        let call = Marshal.GetDelegateForFunctionPointer<Func<int64>>(exportAddress)
        call.Invoke()

    member _.Call1(funcName: string, a: int64) : int64 =
        let exportAddress = NativeLibrary.GetExport(handle, funcName)
        let call = Marshal.GetDelegateForFunctionPointer<Func<int64, int64>>(exportAddress)
        call.Invoke(a)

    member _.Call2(funcName: string, a: int64, b: int64) : int64 =
        let exportAddress = NativeLibrary.GetExport(handle, funcName)
        let call = Marshal.GetDelegateForFunctionPointer<Func<int64, int64, int64>>(exportAddress)
        call.Invoke(a, b)

    member _.Call3(funcName: string, a: int64, b: int64, c: int64) : int64 =
        let exportAddress = NativeLibrary.GetExport(handle, funcName)
        let call = Marshal.GetDelegateForFunctionPointer<Func<int64, int64, int64, int64>>(exportAddress)
        call.Invoke(a, b, c)

    member _.Call4(funcName: string, a: int64, b: int64, c: int64, d: int64) : int64 =
        let exportAddress = NativeLibrary.GetExport(handle, funcName)
        let call = Marshal.GetDelegateForFunctionPointer<Func<int64, int64, int64, int64, int64>>(exportAddress)
        call.Invoke(a, b, c, d)

    member this.CallI64(funcName: string, args: int64[]) : int64 =
        match args.Length with
        | 0 -> this.Call0(funcName)
        | 1 -> this.Call1(funcName, args[0])
        | 2 -> this.Call2(funcName, args[0], args[1])
        | 3 -> this.Call3(funcName, args[0], args[1], args[2])
        | 4 -> this.Call4(funcName, args[0], args[1], args[2], args[3])
        | _ -> failwithf "NativeKernel.CallI64: too many args (%d), max 4 supported" args.Length

    member this.CallF64(funcName: string, args: float[]) : float =
        let rawArgs = args |> Array.map BitConverter.DoubleToInt64Bits
        let result = this.CallI64(funcName, rawArgs)
        BitConverter.Int64BitsToDouble(result)

    member _.LibraryPath = libraryPath
    member _.Handle = handle

    interface IDisposable with
        member _.Dispose() =
            if not disposed then
                disposed <- true
                NativeLibrary.Free(handle)

