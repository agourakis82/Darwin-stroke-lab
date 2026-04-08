namespace Darwin.ResearchOs.Core

open System
open System.Collections.Generic
open System.Diagnostics
open System.Globalization
open System.IO
open System.Runtime.InteropServices
open System.Text.RegularExpressions

module SounioRuntimeProbe =
    [<Struct; StructLayout(LayoutKind.Sequential)>]
    type private NativeKnowledge =
        val mutable Value: double
        val mutable Uncertainty: double
        val mutable Confidence: double

    [<Struct; StructLayout(LayoutKind.Sequential)>]
    type private NativeBootstrapKnowledge =
        val mutable Estimate: double
        val mutable PercentileLower: double
        val mutable PercentileUpper: double
        val mutable BootstrapSe: double
        val mutable Bias: double
        val mutable NSamples: uint32

    [<Struct; StructLayout(LayoutKind.Sequential)>]
    type private NativeCVKnowledge =
        val mutable Mean: double
        val mutable StdError: double
        val mutable StdDev: double
        val mutable K: uint32

    [<UnmanagedFunctionPointer(CallingConvention.Cdecl)>]
    type private KnowledgeNewDelegate = delegate of double * double * double -> NativeKnowledge

    [<UnmanagedFunctionPointer(CallingConvention.Cdecl)>]
    type private KnowledgeAddDelegate = delegate of NativeKnowledge * NativeKnowledge -> NativeKnowledge

    [<UnmanagedFunctionPointer(CallingConvention.Cdecl)>]
    type private KnowledgeCiLowerDelegate = delegate of NativeKnowledge -> double

    [<UnmanagedFunctionPointer(CallingConvention.Cdecl)>]
    type private KnowledgeCiUpperDelegate = delegate of NativeKnowledge -> double

    [<UnmanagedFunctionPointer(CallingConvention.Cdecl)>]
    type private BootstrapFromSamplesDelegate = delegate of double * nativeint * unativeint * double -> NativeBootstrapKnowledge

    [<UnmanagedFunctionPointer(CallingConvention.Cdecl)>]
    type private BootstrapToKnowledgeDelegate = delegate of NativeBootstrapKnowledge -> NativeKnowledge

    [<UnmanagedFunctionPointer(CallingConvention.Cdecl)>]
    type private CvFromFoldsDelegate = delegate of nativeint * unativeint -> NativeCVKnowledge

    [<UnmanagedFunctionPointer(CallingConvention.Cdecl)>]
    type private CvToKnowledgeDelegate = delegate of NativeCVKnowledge -> NativeKnowledge

    type private NativeFfiProbeResult =
        { LibraryPath: string option
          Detected: bool
          Usable: bool
          ProbeSucceeded: bool
          KernelExecutionAvailable: bool
          Output: ResizeArray<string>
          Diagnostics: ResizeArray<string> }

    type private NativeBackendSourceInventory =
        { Root: string option
          TensorSymbols: ResizeArray<string>
          UncertainSymbols: ResizeArray<string>
          OdeSymbols: ResizeArray<string>
          AutodiffSymbols: ResizeArray<string>
          Notes: ResizeArray<string> }

    type private SnioInteropInventory =
        { Root: string option
          EmbedHeaderPath: string option
          ProtocolPath: string option
          FsharpProtocolPath: string option
          SourceVersionJsonAvailable: bool
          Notes: ResizeArray<string> }

    let private resize (items: seq<'T>) = ResizeArray<'T>(items)

    let private format4 (value: double) =
        value.ToString("F4", CultureInfo.InvariantCulture)

    let private firstEnv names =
        names
        |> List.tryPick (fun name ->
            let value = Environment.GetEnvironmentVariable(name)
            if String.IsNullOrWhiteSpace(value) then None else Some value)

    let private runProcess workingDirectory executable arguments environment =
        try
            let psi = ProcessStartInfo()
            psi.FileName <- executable
            psi.WorkingDirectory <- workingDirectory
            psi.RedirectStandardOutput <- true
            psi.RedirectStandardError <- true
            psi.UseShellExecute <- false
            for argument in arguments do
                psi.ArgumentList.Add(argument)
            for KeyValue(key, value) in environment do
                psi.Environment[key] <- value

            use proc = new Process()
            proc.StartInfo <- psi
            proc.Start() |> ignore
            let stdout = proc.StandardOutput.ReadToEnd()
            let stderr = proc.StandardError.ReadToEnd()
            proc.WaitForExit()
            Ok(proc.ExitCode, stdout, stderr)
        with error ->
            Error error.Message

    let private runGit workingDirectory arguments =
        let env = Dictionary<string, string>()
        match runProcess workingDirectory "git" arguments env with
        | Ok(0, stdout, _) ->
            let trimmed = stdout.Trim()
            if String.IsNullOrWhiteSpace(trimmed) then None else Some trimmed
        | _ -> None

    let private isOfficialRemote (remoteUrl: string) =
        let normalized = remoteUrl.Trim().ToLowerInvariant()
        normalized = "git@github.com:sounio-lang/sounio.git"
        || normalized = "https://github.com/sounio-lang/sounio.git"
        || normalized = "https://github.com/sounio-lang/sounio"

    let private repoRoot () =
        let rec ascend (current: DirectoryInfo) =
            let contractsPath = Path.Combine(current.FullName, "contracts")
            let agentsPath = Path.Combine(current.FullName, "AGENTS.md")
            if Directory.Exists(contractsPath) && File.Exists(agentsPath) then
                current.FullName
            elif isNull current.Parent then
                current.FullName
            else
                ascend current.Parent

        ascend (DirectoryInfo(Path.GetFullPath(AppContext.BaseDirectory)))

    let private candidateRoots () =
        let roots = ResizeArray<string>()
        let seen = HashSet<string>(StringComparer.OrdinalIgnoreCase)
        let addRoot (path: string) =
            let full = Path.GetFullPath(path)
            if seen.Add(full) then
                roots.Add(full)

        match firstEnv [ "SOUNIO_ROOT" ] with
        | Some root -> addRoot root
        | None -> ()
        addRoot (Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "sounio-lang-sounio-origin-main"))
        addRoot (Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "sounio-lang-sounio"))
        addRoot (Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "sounio"))
        roots

    let private findInAncestors (startPath: string) (relativePath: string) =
        let startDirectory =
            if Directory.Exists(startPath) then
                DirectoryInfo(Path.GetFullPath(startPath))
            else
                let file = FileInfo(Path.GetFullPath(startPath))
                file.Directory

        let rec ascend (current: DirectoryInfo) =
            if isNull current then
                None
            else
                let candidate = Path.Combine(current.FullName, relativePath)
                if File.Exists(candidate) then
                    Some(Path.GetFullPath(candidate))
                elif isNull current.Parent then
                    None
                else
                    ascend current.Parent

        if isNull startDirectory then None else ascend startDirectory

    let private findGitRoot (path: string) =
        let start = FileInfo(path).Directory
        let rec ascend (current: DirectoryInfo) =
            if isNull current then None
            elif Directory.Exists(Path.Combine(current.FullName, ".git")) then Some current.FullName
            elif isNull current.Parent then None
            else ascend current.Parent

        if isNull start then None else ascend start

    let private resolveProbeProgram () =
        let probePath = Path.Combine(repoRoot (), "sounio", "kernels", "runtime_probe.sio")
        if File.Exists(probePath) then Some probePath else None

    let private runtimeLibraryFileName () =
        if OperatingSystem.IsMacOS() then
            "libsounio_runtime.dylib"
        elif OperatingSystem.IsWindows() then
            "sounio_runtime.dll"
        else
            "libsounio_runtime.so"

    let private detectRuntimeLibrary () =
        let envPath = firstEnv [ "SOUNIO_RUNTIME_LIB_PATH" ] |> Option.map Path.GetFullPath
        let rootedCandidates =
            candidateRoots ()
            |> Seq.collect (fun root ->
                [ Path.Combine(root, "runtime", "target", "release", runtimeLibraryFileName ())
                  Path.Combine(root, "target", "release", runtimeLibraryFileName ()) ])

        seq {
            match envPath with
            | Some candidate -> yield ("env", candidate)
            | None -> ()
            for candidate in rootedCandidates do
                yield ("default-root", Path.GetFullPath(candidate))
        }
        |> Seq.tryFind (fun (_, candidate) -> File.Exists(candidate))

    let private tryLoadDelegate<'T when 'T :> Delegate> (handle: nativeint) symbol =
        let mutable exportPtr = nativeint 0
        if NativeLibrary.TryGetExport(handle, symbol, &exportPtr) then
            Ok(Marshal.GetDelegateForFunctionPointer<'T>(exportPtr))
        else
            Error($"Missing native symbol: {symbol}")

    let private tryLoadOptionalDelegate<'T when 'T :> Delegate> (handle: nativeint) symbol (diagnostics: ResizeArray<string>) =
        match tryLoadDelegate<'T> handle symbol with
        | Ok value -> Some value
        | Error message ->
            diagnostics.Add(message)
            None

    let private nmArguments libraryPath =
        if OperatingSystem.IsMacOS() then
            [ "-gU"; libraryPath ]
        elif OperatingSystem.IsWindows() then
            []
        else
            [ "-gD"; libraryPath ]

    let private canonicalizeSymbol (symbol: string) =
        symbol.Trim().TrimStart('_')

    let private scientificFamilySymbolsFromExports (exported: seq<string>) =
        let exportedSet = Set.ofSeq exported
        let filterPrefix prefix =
            exportedSet
            |> Seq.filter (fun symbol -> symbol.StartsWith(prefix, StringComparison.Ordinal))
            |> Seq.sort
            |> ResizeArray

        let runtimeTensorSymbols = filterPrefix "sounio_tensor_"
        let runtimeUncertainSymbols = filterPrefix "sounio_uncertain_"
        let runtimeOdeSymbols = filterPrefix "sounio_ode_"
        let runtimeAutodiffSymbols =
            exportedSet
            |> Seq.filter (fun symbol ->
                symbol.StartsWith("sounio_autodiff_", StringComparison.Ordinal)
                || symbol.StartsWith("sounio_dual_", StringComparison.Ordinal))
            |> Seq.sort
            |> ResizeArray

        runtimeTensorSymbols, runtimeUncertainSymbols, runtimeOdeSymbols, runtimeAutodiffSymbols

    let private sourceOnlySymbols runtimeSymbols sourceSymbols =
        let runtimeSet = Set.ofSeq runtimeSymbols
        sourceSymbols
        |> Seq.filter (fun symbol -> not (runtimeSet.Contains(symbol)))
        |> Seq.sort
        |> ResizeArray

    let private allSourceOnlyScientificSymbols (runtimeTensorSymbols: seq<string>) (runtimeUncertainSymbols: seq<string>) (runtimeOdeSymbols: seq<string>) (runtimeAutodiffSymbols: seq<string>) sourceInventory =
        let all = ResizeArray<string>()
        all.AddRange(sourceOnlySymbols runtimeTensorSymbols sourceInventory.TensorSymbols)
        all.AddRange(sourceOnlySymbols runtimeUncertainSymbols sourceInventory.UncertainSymbols)
        all.AddRange(sourceOnlySymbols runtimeOdeSymbols sourceInventory.OdeSymbols)
        all.AddRange(sourceOnlySymbols runtimeAutodiffSymbols sourceInventory.AutodiffSymbols)
        all

    let private nativeBackendSourceInventory () =
        let notes = ResizeArray<string>()
        let sourceRoot =
            candidateRoots ()
            |> Seq.tryFind (fun root ->
                Directory.Exists(Path.Combine(root, "compiler", "src", "backend", "native")))

        let extractSymbols (path: string) =
            if not (File.Exists(path)) then
                ResizeArray<string>()
            else
                let regex = Regex(@"pub\s+(?:unsafe\s+)?extern\s+""C""\s+fn\s+(sounio_[A-Za-z0-9_]+)", RegexOptions.Compiled)
                File.ReadLines(path)
                |> Seq.choose (fun line ->
                    let matched = regex.Match(line)
                    if matched.Success then Some matched.Groups[1].Value else None)
                |> Seq.distinct
                |> Seq.sort
                |> ResizeArray

        match sourceRoot with
        | None ->
            notes.Add("No Sounio compiler native backend sources were detected under the candidate roots.")
            { Root = None
              TensorSymbols = ResizeArray()
              UncertainSymbols = ResizeArray()
              OdeSymbols = ResizeArray()
              AutodiffSymbols = ResizeArray()
              Notes = notes }
        | Some root ->
            let tensorSymbols =
                extractSymbols (Path.Combine(root, "compiler", "src", "backend", "native", "tensor_runtime.rs"))
            let uncertainSymbols =
                extractSymbols (Path.Combine(root, "compiler", "src", "backend", "native", "uncertain_runtime.rs"))
            let odeSymbols =
                extractSymbols (Path.Combine(root, "compiler", "src", "backend", "native", "ode_runtime.rs"))
            let autodiffSymbols =
                extractSymbols (Path.Combine(root, "compiler", "src", "backend", "native", "autodiff_runtime.rs"))

            if tensorSymbols.Count > 0 then
                notes.Add($"Detected {tensorSymbols.Count} tensor FFI symbols in compiler native backend sources.")
            if uncertainSymbols.Count > 0 then
                notes.Add($"Detected {uncertainSymbols.Count} uncertain FFI symbols in compiler native backend sources.")
            if odeSymbols.Count > 0 then
                notes.Add($"Detected {odeSymbols.Count} ODE FFI symbols in compiler native backend sources.")
            if autodiffSymbols.Count > 0 then
                notes.Add($"Detected {autodiffSymbols.Count} autodiff FFI symbols in compiler native backend sources.")

            { Root = Some root
              TensorSymbols = tensorSymbols
              UncertainSymbols = uncertainSymbols
              OdeSymbols = odeSymbols
              AutodiffSymbols = autodiffSymbols
              Notes = notes }

    let private snioInteropInventory () =
        let notes = ResizeArray<string>()
        let sourceRoot =
            candidateRoots ()
            |> Seq.tryFind (fun root ->
                File.Exists(Path.Combine(root, "interop", "embed", "sounio_embed.h"))
                || File.Exists(Path.Combine(root, "self-hosted", "interop", "protocol.sio")))

        let sourceHasVersionJson (filePath: string) =
            if not (File.Exists(filePath)) then
                false
            else
                File.ReadLines(filePath)
                |> Seq.exists (fun line -> line.Contains("--version-json", StringComparison.Ordinal))

        match sourceRoot with
        | None ->
            notes.Add("No SNIO embedding ABI files were detected under the candidate Sounio roots.")
            { Root = None
              EmbedHeaderPath = None
              ProtocolPath = None
              FsharpProtocolPath = None
              SourceVersionJsonAvailable = false
              Notes = notes }
        | Some root ->
            let embedHeaderPath = Path.Combine(root, "interop", "embed", "sounio_embed.h")
            let protocolPath = Path.Combine(root, "self-hosted", "interop", "protocol.sio")
            let fsharpProtocolPath = Path.Combine(root, "interop", "fsharp", "Sounio.Interop", "Protocol.fs")
            let compilerMainPath = Path.Combine(root, "self-hosted", "compiler", "main.sio")

            let embedHeader =
                if File.Exists(embedHeaderPath) then
                    notes.Add("Detected official Sounio embedding C header for session/kernel ABI.")
                    Some embedHeaderPath
                else
                    None

            let protocol =
                if File.Exists(protocolPath) then
                    notes.Add("Detected SNIO protocol source for session/kernel embedding.")
                    Some protocolPath
                else
                    None

            let fsharpProtocol =
                if File.Exists(fsharpProtocolPath) then
                    notes.Add("Detected upstream F# SNIO protocol binding/example.")
                    Some fsharpProtocolPath
                else
                    None

            let sourceVersionJsonAvailable = sourceHasVersionJson compilerMainPath
            if sourceVersionJsonAvailable then
                notes.Add("Sounio self-hosted compiler source advertises --version-json support.")

            { Root = Some root
              EmbedHeaderPath = embedHeader
              ProtocolPath = protocol
              FsharpProtocolPath = fsharpProtocol
              SourceVersionJsonAvailable = sourceVersionJsonAvailable
              Notes = notes }

    let abiInventory () : Darwin.ResearchOs.Contracts.SounioRuntimeAbiReport =
        let notes = ResizeArray<string>()
        let sourceInventory = nativeBackendSourceInventory ()
        let snioInventory = snioInteropInventory ()
        notes.AddRange(sourceInventory.Notes)
        notes.AddRange(snioInventory.Notes)

        match detectRuntimeLibrary () with
        | None ->
            notes.Add("No native Sounio runtime library was detected for ABI inventory.")
            { Detected = false
              LibraryPath = None
              SourceRoot = sourceInventory.Root
              SnioEmbedHeaderPath = snioInventory.EmbedHeaderPath
              SnioProtocolPath = snioInventory.ProtocolPath
              SnioFsharpProtocolPath = snioInventory.FsharpProtocolPath
              ExportCount = 0
              SourceExportCount =
                sourceInventory.TensorSymbols.Count
                + sourceInventory.UncertainSymbols.Count
                + sourceInventory.OdeSymbols.Count
                + sourceInventory.AutodiffSymbols.Count
              BinaryVersionJsonAvailable = false
              SourceVersionJsonAvailable = snioInventory.SourceVersionJsonAvailable
              ExportedSymbols = ResizeArray()
              DispatchSymbols = ResizeArray()
              HandlerSymbols = ResizeArray()
              IntrinsicSymbols = ResizeArray()
              KnowledgeSymbols = ResizeArray()
              BootstrapSymbols = ResizeArray()
              CvSymbols = ResizeArray()
              MathSymbols = ResizeArray()
              MemorySymbols = ResizeArray()
              RuntimeTensorSymbols = ResizeArray()
              RuntimeUncertainSymbols = ResizeArray()
              RuntimeOdeSymbols = ResizeArray()
              RuntimeAutodiffSymbols = ResizeArray()
              TensorSymbols = sourceInventory.TensorSymbols
              UncertainSymbols = sourceInventory.UncertainSymbols
              OdeSymbols = sourceInventory.OdeSymbols
              AutodiffSymbols = sourceInventory.AutodiffSymbols
              SourceOnlyScientificSymbols =
                allSourceOnlyScientificSymbols Seq.empty Seq.empty Seq.empty Seq.empty sourceInventory
              KernelExecutionSymbols = ResizeArray()
              NativeKernelExecutionAvailable = false
              KernelSessionProtocolAvailable = snioInventory.ProtocolPath.IsSome && snioInventory.EmbedHeaderPath.IsSome
              Notes = notes }
        | Some(_, libraryPath) ->
            if OperatingSystem.IsWindows() then
                notes.Add("ABI inventory via nm is not implemented on Windows.")
                { Detected = true
                  LibraryPath = Some libraryPath
                  SourceRoot = sourceInventory.Root
                  SnioEmbedHeaderPath = snioInventory.EmbedHeaderPath
                  SnioProtocolPath = snioInventory.ProtocolPath
                  SnioFsharpProtocolPath = snioInventory.FsharpProtocolPath
                  ExportCount = 0
                  SourceExportCount =
                    sourceInventory.TensorSymbols.Count
                    + sourceInventory.UncertainSymbols.Count
                    + sourceInventory.OdeSymbols.Count
                    + sourceInventory.AutodiffSymbols.Count
                  BinaryVersionJsonAvailable = false
                  SourceVersionJsonAvailable = snioInventory.SourceVersionJsonAvailable
                  ExportedSymbols = ResizeArray()
                  DispatchSymbols = ResizeArray()
                  HandlerSymbols = ResizeArray()
                  IntrinsicSymbols = ResizeArray()
                  KnowledgeSymbols = ResizeArray()
                  BootstrapSymbols = ResizeArray()
                  CvSymbols = ResizeArray()
                  MathSymbols = ResizeArray()
                  MemorySymbols = ResizeArray()
                  RuntimeTensorSymbols = ResizeArray()
                  RuntimeUncertainSymbols = ResizeArray()
                  RuntimeOdeSymbols = ResizeArray()
                  RuntimeAutodiffSymbols = ResizeArray()
                  TensorSymbols = sourceInventory.TensorSymbols
                  UncertainSymbols = sourceInventory.UncertainSymbols
                  OdeSymbols = sourceInventory.OdeSymbols
                  AutodiffSymbols = sourceInventory.AutodiffSymbols
                  SourceOnlyScientificSymbols =
                    allSourceOnlyScientificSymbols Seq.empty Seq.empty Seq.empty Seq.empty sourceInventory
                  KernelExecutionSymbols = ResizeArray()
                  NativeKernelExecutionAvailable = false
                  KernelSessionProtocolAvailable = snioInventory.ProtocolPath.IsSome && snioInventory.EmbedHeaderPath.IsSome
                  Notes = notes }
            else
                let env = Dictionary<string, string>()
                match runProcess (Directory.GetCurrentDirectory()) "nm" (nmArguments libraryPath) env with
                | Error message ->
                    notes.Add($"Could not execute nm for runtime library inventory: {message}")
                    { Detected = true
                      LibraryPath = Some libraryPath
                      SourceRoot = sourceInventory.Root
                      SnioEmbedHeaderPath = snioInventory.EmbedHeaderPath
                      SnioProtocolPath = snioInventory.ProtocolPath
                      SnioFsharpProtocolPath = snioInventory.FsharpProtocolPath
                      ExportCount = 0
                      SourceExportCount =
                        sourceInventory.TensorSymbols.Count
                        + sourceInventory.UncertainSymbols.Count
                        + sourceInventory.OdeSymbols.Count
                        + sourceInventory.AutodiffSymbols.Count
                      BinaryVersionJsonAvailable = false
                      SourceVersionJsonAvailable = snioInventory.SourceVersionJsonAvailable
                      ExportedSymbols = ResizeArray()
                      DispatchSymbols = ResizeArray()
                      HandlerSymbols = ResizeArray()
                      IntrinsicSymbols = ResizeArray()
                      KnowledgeSymbols = ResizeArray()
                      BootstrapSymbols = ResizeArray()
                      CvSymbols = ResizeArray()
                      MathSymbols = ResizeArray()
                      MemorySymbols = ResizeArray()
                      RuntimeTensorSymbols = ResizeArray()
                      RuntimeUncertainSymbols = ResizeArray()
                      RuntimeOdeSymbols = ResizeArray()
                      RuntimeAutodiffSymbols = ResizeArray()
                      TensorSymbols = sourceInventory.TensorSymbols
                      UncertainSymbols = sourceInventory.UncertainSymbols
                      OdeSymbols = sourceInventory.OdeSymbols
                      AutodiffSymbols = sourceInventory.AutodiffSymbols
                      SourceOnlyScientificSymbols =
                        allSourceOnlyScientificSymbols Seq.empty Seq.empty Seq.empty Seq.empty sourceInventory
                      KernelExecutionSymbols = ResizeArray()
                      NativeKernelExecutionAvailable = false
                      KernelSessionProtocolAvailable = snioInventory.ProtocolPath.IsSome && snioInventory.EmbedHeaderPath.IsSome
                      Notes = notes }
                | Ok(exitCode, stdout, stderr) ->
                    if exitCode <> 0 && not (String.IsNullOrWhiteSpace(stderr)) then
                        notes.Add($"nm reported a non-zero exit code while inspecting the runtime library: {stderr.Trim()}")

                    let exported =
                        stdout.Split(Environment.NewLine, StringSplitOptions.RemoveEmptyEntries)
                        |> Seq.choose (fun line ->
                            let trimmed = line.Trim()
                            if String.IsNullOrWhiteSpace(trimmed) then
                                None
                            else
                                let parts = trimmed.Split([| ' '; '\t' |], StringSplitOptions.RemoveEmptyEntries)
                                parts |> Array.tryLast |> Option.map canonicalizeSymbol)
                        |> Seq.filter (fun symbol -> symbol.StartsWith("sounio_"))
                        |> Seq.distinct
                        |> Seq.sort
                        |> ResizeArray

                    let dispatchSymbols =
                        exported
                        |> Seq.filter (fun symbol -> symbol.StartsWith("sounio_dispatch_"))
                        |> ResizeArray

                    let handlerSymbols =
                        exported
                        |> Seq.filter (fun symbol ->
                            symbol.StartsWith("sounio_push_handler_")
                            || symbol = "sounio_pop_handler"
                            || symbol = "sounio_handler_depth")
                        |> ResizeArray

                    let intrinsicSymbols =
                        exported
                        |> Seq.filter (fun symbol -> not (symbol.StartsWith("sounio_dispatch_")) && not (symbol.StartsWith("sounio_push_handler_")) && symbol <> "sounio_pop_handler" && symbol <> "sounio_handler_depth")
                        |> ResizeArray

                    let knowledgeSymbols =
                        intrinsicSymbols
                        |> Seq.filter (fun symbol -> symbol.StartsWith("sounio_knowledge_"))
                        |> ResizeArray

                    let bootstrapSymbols =
                        intrinsicSymbols
                        |> Seq.filter (fun symbol -> symbol.StartsWith("sounio_bootstrap_"))
                        |> ResizeArray

                    let cvSymbols =
                        intrinsicSymbols
                        |> Seq.filter (fun symbol -> symbol.StartsWith("sounio_cv_"))
                        |> ResizeArray

                    let mathPrefixes =
                        set
                            [ "sounio_sqrt_f64"
                              "sounio_sin_f64"
                              "sounio_cos_f64"
                              "sounio_tan_f64"
                              "sounio_asin_f64"
                              "sounio_acos_f64"
                              "sounio_atan_f64"
                              "sounio_atan2_f64"
                              "sounio_exp_f64"
                              "sounio_ln_f64"
                              "sounio_log10_f64"
                              "sounio_log2_f64"
                              "sounio_pow_f64"
                              "sounio_abs_f64"
                              "sounio_floor_f64"
                              "sounio_ceil_f64"
                              "sounio_round_f64"
                              "sounio_trunc_f64"
                              "sounio_min_f64"
                              "sounio_max_f64"
                              "sounio_clamp_f64" ]

                    let mathSymbols =
                        intrinsicSymbols
                        |> Seq.filter mathPrefixes.Contains
                        |> ResizeArray

                    let memorySymbols =
                        intrinsicSymbols
                        |> Seq.filter (fun symbol ->
                            symbol = "sounio_alloc"
                            || symbol = "sounio_alloc_zeroed"
                            || symbol = "sounio_dealloc"
                            || symbol = "sounio_realloc")
                        |> ResizeArray

                    let kernelExecutionSymbols =
                        exported
                        |> Seq.filter (fun symbol ->
                            symbol.Contains("kernel_run")
                            || symbol.Contains("kernel_load")
                            || symbol.Contains("execution_")
                            || symbol.Contains("session_"))
                        |> ResizeArray

                    let runtimeTensorSymbols, runtimeUncertainSymbols, runtimeOdeSymbols, runtimeAutodiffSymbols =
                        scientificFamilySymbolsFromExports exported

                    let sourceOnlyScientificSymbols =
                        let all = ResizeArray<string>()
                        all.AddRange(sourceOnlySymbols runtimeTensorSymbols sourceInventory.TensorSymbols)
                        all.AddRange(sourceOnlySymbols runtimeUncertainSymbols sourceInventory.UncertainSymbols)
                        all.AddRange(sourceOnlySymbols runtimeOdeSymbols sourceInventory.OdeSymbols)
                        all.AddRange(sourceOnlySymbols runtimeAutodiffSymbols sourceInventory.AutodiffSymbols)
                        all

                    if knowledgeSymbols.Count > 0 then
                        notes.Add($"Detected {knowledgeSymbols.Count} native knowledge symbols.")
                    if bootstrapSymbols.Count > 0 then
                        notes.Add($"Detected {bootstrapSymbols.Count} native bootstrap symbols.")
                    if cvSymbols.Count > 0 then
                        notes.Add($"Detected {cvSymbols.Count} native cross-validation symbols.")
                    if sourceInventory.Root.IsSome
                        && (sourceInventory.TensorSymbols.Count > 0
                            || sourceInventory.UncertainSymbols.Count > 0
                            || sourceInventory.OdeSymbols.Count > 0
                            || sourceInventory.AutodiffSymbols.Count > 0) then
                        notes.Add("Compiler native backend sources expose additional C ABI scientific functions beyond the current runtime dylib exports.")
                    if sourceOnlyScientificSymbols.Count > 0 then
                        notes.Add($"There are {sourceOnlyScientificSymbols.Count} scientific FFI symbols present in compiler native backend sources but not exported by the current runtime dylib.")
                    if kernelExecutionSymbols.Count = 0 then
                        notes.Add("No native kernel execution symbols were detected in the current runtime dylib; kernel execution still requires souc.")
                    if snioInventory.ProtocolPath.IsSome && snioInventory.EmbedHeaderPath.IsSome then
                        notes.Add("Official kernel/session embedding ABI is present in source via SNIO and sounio_embed.h, even though the current runtime dylib does not yet export the full host-facing kernel ABI.")

                    { Detected = true
                      LibraryPath = Some libraryPath
                      SourceRoot = sourceInventory.Root
                      SnioEmbedHeaderPath = snioInventory.EmbedHeaderPath
                      SnioProtocolPath = snioInventory.ProtocolPath
                      SnioFsharpProtocolPath = snioInventory.FsharpProtocolPath
                      ExportCount = exported.Count
                      SourceExportCount =
                        sourceInventory.TensorSymbols.Count
                        + sourceInventory.UncertainSymbols.Count
                        + sourceInventory.OdeSymbols.Count
                        + sourceInventory.AutodiffSymbols.Count
                      BinaryVersionJsonAvailable = false
                      SourceVersionJsonAvailable = snioInventory.SourceVersionJsonAvailable
                      ExportedSymbols = exported
                      DispatchSymbols = dispatchSymbols
                      HandlerSymbols = handlerSymbols
                      IntrinsicSymbols = intrinsicSymbols
                      KnowledgeSymbols = knowledgeSymbols
                      BootstrapSymbols = bootstrapSymbols
                      CvSymbols = cvSymbols
                      MathSymbols = mathSymbols
                      MemorySymbols = memorySymbols
                      RuntimeTensorSymbols = runtimeTensorSymbols
                      RuntimeUncertainSymbols = runtimeUncertainSymbols
                      RuntimeOdeSymbols = runtimeOdeSymbols
                      RuntimeAutodiffSymbols = runtimeAutodiffSymbols
                      TensorSymbols = sourceInventory.TensorSymbols
                      UncertainSymbols = sourceInventory.UncertainSymbols
                      OdeSymbols = sourceInventory.OdeSymbols
                      AutodiffSymbols = sourceInventory.AutodiffSymbols
                      SourceOnlyScientificSymbols = sourceOnlyScientificSymbols
                      KernelExecutionSymbols = kernelExecutionSymbols
                      NativeKernelExecutionAvailable = kernelExecutionSymbols.Count > 0
                      KernelSessionProtocolAvailable = snioInventory.ProtocolPath.IsSome && snioInventory.EmbedHeaderPath.IsSome
                      Notes = notes }

    let private probeNativeFfi () =
        let diagnostics = ResizeArray<string>()
        let output = ResizeArray<string>()

        match detectRuntimeLibrary () with
        | None ->
            diagnostics.Add("No native Sounio runtime library detected from SOUNIO_RUNTIME_LIB_PATH or default runtime roots.")
            { LibraryPath = None
              Detected = false
              Usable = false
              ProbeSucceeded = false
              KernelExecutionAvailable = false
              Output = output
              Diagnostics = diagnostics }
        | Some(_, libraryPath) ->
            let mutable handle = nativeint 0

            try
                handle <- NativeLibrary.Load(libraryPath)

                try
                    let knowledgeNew =
                        match tryLoadDelegate<KnowledgeNewDelegate> handle "sounio_knowledge_new" with
                        | Ok value -> value
                        | Error message -> failwith message

                    let knowledgeAdd =
                        match tryLoadDelegate<KnowledgeAddDelegate> handle "sounio_knowledge_add" with
                        | Ok value -> value
                        | Error message -> failwith message

                    let knowledgeCiLower =
                        match tryLoadDelegate<KnowledgeCiLowerDelegate> handle "sounio_knowledge_ci_lower" with
                        | Ok value -> value
                        | Error message -> failwith message

                    let knowledgeCiUpper =
                        match tryLoadDelegate<KnowledgeCiUpperDelegate> handle "sounio_knowledge_ci_upper" with
                        | Ok value -> value
                        | Error message -> failwith message

                    let bootstrapFromSamples =
                        tryLoadOptionalDelegate<BootstrapFromSamplesDelegate> handle "sounio_bootstrap_from_samples" diagnostics

                    let bootstrapToKnowledge =
                        tryLoadOptionalDelegate<BootstrapToKnowledgeDelegate> handle "sounio_bootstrap_to_knowledge" diagnostics

                    let cvFromFolds =
                        tryLoadOptionalDelegate<CvFromFoldsDelegate> handle "sounio_cv_from_folds" diagnostics

                    let cvToKnowledge =
                        tryLoadOptionalDelegate<CvToKnowledgeDelegate> handle "sounio_cv_to_knowledge" diagnostics

                    let a = knowledgeNew.Invoke(10.0, 0.5, 0.95)
                    let b = knowledgeNew.Invoke(20.0, 0.25, 0.90)
                    let summed = knowledgeAdd.Invoke(a, b)
                    let ciLower = knowledgeCiLower.Invoke(summed)
                    let ciUpper = knowledgeCiUpper.Invoke(summed)
                    output.Add($"knowledge_sum={format4 summed.Value}")
                    output.Add($"knowledge_uncertainty={format4 summed.Uncertainty}")
                    output.Add($"knowledge_confidence={format4 summed.Confidence}")
                    output.Add($"knowledge_ci=[{format4 ciLower}, {format4 ciUpper}]")

                    match bootstrapFromSamples, bootstrapToKnowledge with
                    | Some bootstrapFromSamples, Some bootstrapToKnowledge ->
                        let bootstrapSamples = [| 0.79; 0.82; 0.85; 0.83; 0.81 |]
                        let bootstrapHandle = GCHandle.Alloc(bootstrapSamples, GCHandleType.Pinned)
                        try
                            let boot =
                                bootstrapFromSamples.Invoke(
                                    0.82,
                                    bootstrapHandle.AddrOfPinnedObject(),
                                    unativeint bootstrapSamples.Length,
                                    0.05
                                )
                            let bootKnowledge = bootstrapToKnowledge.Invoke(boot)
                            output.Add($"bootstrap_estimate={format4 boot.Estimate}")
                            output.Add($"bootstrap_ci=[{format4 boot.PercentileLower}, {format4 boot.PercentileUpper}]")
                            output.Add($"bootstrap_as_knowledge={format4 bootKnowledge.Value}/{format4 bootKnowledge.Uncertainty}")
                        finally
                            if bootstrapHandle.IsAllocated then bootstrapHandle.Free()
                    | _ ->
                        diagnostics.Add("Native Sounio runtime does not currently expose bootstrap conversion symbols; bootstrap probe was skipped.")

                    match cvFromFolds, cvToKnowledge with
                    | Some cvFromFolds, Some cvToKnowledge ->
                        let folds = [| 0.76; 0.81; 0.83; 0.79; 0.84 |]
                        let foldsHandle = GCHandle.Alloc(folds, GCHandleType.Pinned)
                        try
                            let cv = cvFromFolds.Invoke(foldsHandle.AddrOfPinnedObject(), unativeint folds.Length)
                            let cvKnowledge = cvToKnowledge.Invoke(cv)
                            output.Add($"cv_mean={format4 cv.Mean}")
                            output.Add($"cv_std_error={format4 cv.StdError}")
                            output.Add($"cv_as_knowledge={format4 cvKnowledge.Value}/{format4 cvKnowledge.Uncertainty}")
                        finally
                            if foldsHandle.IsAllocated then foldsHandle.Free()
                    | _ ->
                        diagnostics.Add("Native Sounio runtime does not currently expose cross-validation conversion symbols; CV probe was skipped.")

                    diagnostics.Add("Native Sounio FFI is usable for runtime intrinsics and knowledge operations.")
                    diagnostics.Add("Kernel execution still falls back to souc because no native kernel execution ABI was detected in the runtime exports.")

                    { LibraryPath = Some libraryPath
                      Detected = true
                      Usable = true
                      ProbeSucceeded = true
                      KernelExecutionAvailable = false
                      Output = output
                      Diagnostics = diagnostics }
                with error ->
                    diagnostics.Add($"Native Sounio FFI probe failed: {error.Message}")
                    { LibraryPath = Some libraryPath
                      Detected = true
                      Usable = false
                      ProbeSucceeded = false
                      KernelExecutionAvailable = false
                      Output = output
                      Diagnostics = diagnostics }
            finally
                if handle <> nativeint 0 then
                    NativeLibrary.Free(handle)

    let private resolveKernelPath (requestedPath: string option) =
        match requestedPath with
        | Some path when not (String.IsNullOrWhiteSpace(path)) ->
            let absolute =
                if Path.IsPathRooted(path) then path else Path.Combine(repoRoot (), path)
            let resolved = Path.GetFullPath(absolute)
            if File.Exists(resolved) then Some resolved else None
        | _ -> resolveProbeProgram ()

    let private detectCandidate () =
        let vmWrapperCandidate =
            if OperatingSystem.IsMacOS() then
                seq {
                    yield findInAncestors AppContext.BaseDirectory (Path.Combine("scripts", "sounio-lima-souc"))
                    yield findInAncestors (Directory.GetCurrentDirectory()) (Path.Combine("scripts", "sounio-lima-souc"))
                    let candidate = Path.Combine(repoRoot (), "scripts", "sounio-lima-souc")
                    yield if File.Exists(candidate) then Some(Path.GetFullPath(candidate)) else None
                }
                |> Seq.choose id
                |> Seq.tryHead
            else
                None

        let canStartSouc (candidate: string) =
            let env = Dictionary<string, string>()
            match runProcess (Directory.GetCurrentDirectory()) candidate [ "--version" ] env with
            | Ok(0, _, _) -> true
            | _ -> false

        let envPath = firstEnv [ "SOUNIO_SOUC_PATH" ] |> Option.map Path.GetFullPath
        let pathCandidate =
            match Environment.GetEnvironmentVariable("PATH") with
            | null -> None
            | value ->
                value.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries)
                |> Array.tryPick (fun entry ->
                    let candidate = Path.Combine(entry, "souc")
                    if File.Exists(candidate) then Some candidate else None)

        let rootedCandidates =
            candidateRoots ()
            |> Seq.map (fun root -> Path.Combine(root, "compiler", "target", "release", "souc"))

        seq {
            match envPath with
            | Some candidate -> yield ("env", candidate)
            | None -> ()
            match vmWrapperCandidate with
            | Some candidate -> yield ("vm-wrapper", candidate)
            | None -> ()
            match pathCandidate with
            | Some candidate -> yield ("path", Path.GetFullPath(candidate))
            | None -> ()
            for candidate in rootedCandidates do
                yield ("default-root", Path.GetFullPath(candidate))
        }
        |> Seq.tryFind (fun (source, candidate) ->
            File.Exists(candidate)
            && (String.Equals(source, "vm-wrapper", StringComparison.Ordinal) || canStartSouc candidate))

    let private resolveStdlib (soucPath: string) =
        match firstEnv [ "SOUNIO_STDLIB_PATH" ] with
        | Some stdlib when Directory.Exists(stdlib) -> Some(Path.GetFullPath(stdlib))
        | _ ->
            candidateRoots ()
            |> Seq.tryPick (fun root ->
                let candidate = Path.Combine(root, "stdlib")
                if Directory.Exists(candidate) then Some candidate else None)
            |> Option.orElseWith (fun () ->
                let candidate =
                    DirectoryInfo(soucPath).Parent.Parent.Parent.Parent.FullName
                    |> fun root -> Path.Combine(root, "stdlib")
                if Directory.Exists(candidate) then Some candidate else None)

    let private resolveRootFromSoucPath (soucPath: string) =
        try
            let directory = DirectoryInfo(soucPath)
            if isNull directory.Parent || isNull directory.Parent.Parent || isNull directory.Parent.Parent.Parent || isNull directory.Parent.Parent.Parent.Parent then
                None
            else
                Some directory.Parent.Parent.Parent.Parent.FullName
        with _ ->
            None

    let private resolveSnioServeEntryPath (root: string) =
        let candidate = Path.Combine(root, "self-hosted", "interop", "serve_entry.sio")
        if File.Exists(candidate) then Some candidate else None

    let private soucSupportsFlag (soucPath: string) (flag: string) =
        let env = Dictionary<string, string>()
        match runProcess (Directory.GetCurrentDirectory()) soucPath [ "--help" ] env with
        | Ok(_, stdout, stderr) ->
            let combined = $"{stdout}\n{stderr}"
            combined.Contains(flag, StringComparison.Ordinal)
        | Error _ -> false

    let private resolveSnioSourceRoot (runtime: Darwin.ResearchOs.Contracts.SounioRuntimeProbeReport) =
        seq {
            match runtime.SnioProtocolPath with
            | Some protocolPath ->
                let interopDir = DirectoryInfo(Path.GetDirectoryName(protocolPath))
                if not (isNull interopDir) && not (isNull interopDir.Parent) then
                    yield interopDir.Parent.Parent.FullName
            | None -> ()

            match runtime.SounioRoot with
            | Some root -> yield root
            | None -> ()
        }
        |> Seq.tryHead

    type private SnioExecutionAttempt =
        { Attempted: bool
          Used: bool
          CheckSucceeded: bool
          RunSucceeded: bool
          Output: ResizeArray<string> }

    type private SnioServerProbeResult =
        { Attempted: bool
          Succeeded: bool
          BuiltInServeSupported: bool option
          ServeEntryPath: string option
          InfoValues: ResizeArray<int64>
          Capabilities: int64 option
          HealthOk: bool option
          StatsValue: int64 option
          Diagnostics: ResizeArray<string> }

    let private emptySnioServerProbeResult builtInServeSupported serveEntryPath =
        { Attempted = false
          Succeeded = false
          BuiltInServeSupported = builtInServeSupported
          ServeEntryPath = serveEntryPath
          InfoValues = ResizeArray()
          Capabilities = None
          HealthOk = None
          StatsValue = None
          Diagnostics = ResizeArray() }

    let private showcaseUsable
        (version: string option)
        (stdlibPath: string option)
        (checkSucceeded: bool)
        (runSucceeded: bool)
        (snioServer: SnioServerProbeResult)
        =
        version.IsSome
        && stdlibPath.IsSome
        && (snioServer.Succeeded || (checkSucceeded && runSucceeded))

    let private bashQuote (value: string) =
        "'" + value.Replace("'", "'\"'\"'") + "'"

    let private probeSnioServerBatch
        (soucPath: string)
        (stdlibPath: string)
        (serveEntryPath: string)
        : SnioServerProbeResult =
        let diagnostics = ResizeArray<string>()

        try
            let inputPath = Path.GetTempFileName()
            let outputPath = Path.GetTempFileName()

            try
                use inputStream = File.Open(inputPath, FileMode.Create, FileAccess.Write, FileShare.None)
                SnioProtocol.writeInfo inputStream
                SnioProtocol.writeCapabilities inputStream
                SnioProtocol.writeHealth inputStream
                SnioProtocol.writeStats inputStream
                SnioProtocol.writeShutdown inputStream

                let command =
                    String.concat " " [
                        "set -euo pipefail;"
                        "SOUNIO_STDLIB_PATH=" + bashQuote stdlibPath
                        bashQuote soucPath
                        "run"
                        bashQuote serveEntryPath
                        "<"
                        bashQuote inputPath
                        ">"
                        bashQuote outputPath
                    ]

                let psi = ProcessStartInfo()
                psi.FileName <- "/bin/bash"
                psi.UseShellExecute <- false
                psi.RedirectStandardOutput <- true
                psi.RedirectStandardError <- true
                psi.CreateNoWindow <- true
                psi.ArgumentList.Add("-lc")
                psi.ArgumentList.Add(command)

                use proc = Process.Start(psi)
                let stderr = proc.StandardError.ReadToEnd()
                let stdout = proc.StandardOutput.ReadToEnd()
                proc.WaitForExit()

                if proc.ExitCode <> 0 then
                    let result = emptySnioServerProbeResult (Some false) (Some serveEntryPath)
                    result.Diagnostics.Add($"SNIO batch probe failed: souc exited with code {proc.ExitCode}.")
                    if not (String.IsNullOrWhiteSpace(stderr)) then
                        result.Diagnostics.Add($"SNIO batch probe stderr: {stderr.Trim()}")
                    if not (String.IsNullOrWhiteSpace(stdout)) then
                        result.Diagnostics.Add($"SNIO batch probe stdout: {stdout.Trim()}")
                    { result with Attempted = true }
                else
                    use stream = new MemoryStream(File.ReadAllBytes(outputPath))

                    let nextResponse () =
                        try
                            Some(SnioProtocol.readResponse stream)
                        with error ->
                            diagnostics.Add($"SNIO batch probe response parse failed: {error.Message}")
                            None

                    match nextResponse () with
                    | Some(SnioProtocol.Response.ResultValues [| 0L |])
                    | Some(SnioProtocol.Response.ResultValues [||]) -> ()
                    | Some response ->
                        diagnostics.Add($"SNIO batch probe expected ready signal, got {response}.")
                    | None -> ()

                    let infoValues =
                        match nextResponse () with
                        | Some(SnioProtocol.Response.ResultValues values) -> ResizeArray(values)
                        | Some(SnioProtocol.Response.ErrorMessage message) ->
                            diagnostics.Add($"SNIO Info failed: {message}")
                            ResizeArray()
                        | Some response ->
                            diagnostics.Add($"SNIO Info returned unexpected response: {response}.")
                            ResizeArray()
                        | None -> ResizeArray()

                    let capabilities =
                        match nextResponse () with
                        | Some(SnioProtocol.Response.ResultValues [| value |]) -> Some value
                        | Some(SnioProtocol.Response.ErrorMessage message) ->
                            diagnostics.Add($"SNIO Capabilities failed: {message}")
                            None
                        | Some response ->
                            diagnostics.Add($"SNIO Capabilities returned unexpected response: {response}.")
                            None
                        | None -> None

                    let healthOk =
                        match nextResponse () with
                        | Some(SnioProtocol.Response.ResultValues [| value |]) -> Some(value <> 0L)
                        | Some(SnioProtocol.Response.ErrorMessage message) ->
                            diagnostics.Add($"SNIO Health failed: {message}")
                            None
                        | Some response ->
                            diagnostics.Add($"SNIO Health returned unexpected response: {response}.")
                            None
                        | None -> None

                    let statsValue =
                        match nextResponse () with
                        | Some(SnioProtocol.Response.ResultValues [| value |]) -> Some value
                        | Some(SnioProtocol.Response.ErrorMessage message) ->
                            diagnostics.Add($"SNIO Stats failed: {message}")
                            None
                        | Some response ->
                            diagnostics.Add($"SNIO Stats returned unexpected response: {response}.")
                            None
                        | None -> None

                    match nextResponse () with
                    | Some SnioProtocol.Response.Shutdown -> ()
                    | Some response ->
                        diagnostics.Add($"SNIO batch probe expected shutdown response, got {response}.")
                    | None -> ()

                    if not (String.IsNullOrWhiteSpace(stderr)) then
                        diagnostics.Add($"SNIO batch probe stderr: {stderr.Trim()}")
                    if not (String.IsNullOrWhiteSpace(stdout)) then
                        diagnostics.Add($"SNIO batch probe stdout: {stdout.Trim()}")

                    let succeeded =
                        infoValues.Count > 0
                        && capabilities.IsSome
                        && healthOk = Some true

                    if succeeded then
                        let infoSummary = infoValues |> Seq.map string |> String.concat ", "
                        diagnostics.Add($"SNIO batch probe succeeded via serve_entry.sio with info=[{infoSummary}].")

                    { Attempted = true
                      Succeeded = succeeded
                      BuiltInServeSupported = Some false
                      ServeEntryPath = Some serveEntryPath
                      InfoValues = infoValues
                      Capabilities = capabilities
                      HealthOk = healthOk
                      StatsValue = statsValue
                      Diagnostics = diagnostics }
            finally
                try File.Delete(inputPath) with _ -> ()
                try File.Delete(outputPath) with _ -> ()
        with error ->
            let result = emptySnioServerProbeResult (Some false) (Some serveEntryPath)
            result.Diagnostics.Add($"SNIO batch probe failed: {error.Message}")
            { result with Attempted = true }

    let private probeSnioServer
        (soucPath: string)
        (stdlibPath: string)
        (snioInventory: SnioInteropInventory)
        : SnioServerProbeResult =
        let builtInServeSupported = soucSupportsFlag soucPath "--serve"
        let serveEntryPath =
            snioInventory.Root
            |> Option.bind resolveSnioServeEntryPath

        if not builtInServeSupported && serveEntryPath.IsNone then
            let result = emptySnioServerProbeResult (Some false) serveEntryPath
            if snioInventory.ProtocolPath.IsSome || snioInventory.EmbedHeaderPath.IsSome then
                result.Diagnostics.Add("SNIO server probe skipped because the selected souc binary does not expose --serve and no upstream serve_entry.sio path was available.")
            result
        elif not builtInServeSupported && serveEntryPath.IsSome then
            probeSnioServerBatch soucPath stdlibPath serveEntryPath.Value
        else
            try
                let snio =
                    if builtInServeSupported then
                        new SounioSnioProcess(soucPath, Some stdlibPath)
                    else
                        new SounioSnioProcess(soucPath, Some stdlibPath, serveEntryPath.Value)

                use snio = snio

                let diagnostics = ResizeArray<string>()

                let infoValues =
                    try
                        snio.Info() |> ResizeArray
                    with error ->
                        diagnostics.Add($"SNIO Info failed: {error.Message}")
                        ResizeArray<int64>()

                if infoValues.Count = 0 then
                    diagnostics.Add("SNIO Info returned no structured values.")

                let capabilities =
                    try
                        Some(snio.Capabilities())
                    with error ->
                        diagnostics.Add($"SNIO Capabilities failed: {error.Message}")
                        None

                let healthOk =
                    try
                        Some(snio.Health())
                    with error ->
                        diagnostics.Add($"SNIO Health failed: {error.Message}")
                        None

                let statsValue =
                    try
                        Some(snio.Stats())
                    with error ->
                        diagnostics.Add($"SNIO Stats failed: {error.Message}")
                        None

                let succeeded =
                    infoValues.Count > 0
                    && capabilities.IsSome
                    && healthOk = Some true

                if succeeded then
                    let infoSummary = infoValues |> Seq.map string |> String.concat ", "
                    let serveMode = if builtInServeSupported then "--serve" else "serve_entry.sio"
                    diagnostics.Add($"SNIO server probe succeeded via {serveMode} with info=[{infoSummary}].")

                { Attempted = true
                  Succeeded = succeeded
                  BuiltInServeSupported = Some builtInServeSupported
                  ServeEntryPath = serveEntryPath
                  InfoValues = infoValues
                  Capabilities = capabilities
                  HealthOk = healthOk
                  StatsValue = statsValue
                  Diagnostics = diagnostics }
            with error ->
                let result = emptySnioServerProbeResult (Some builtInServeSupported) serveEntryPath
                result.Diagnostics.Add($"SNIO server probe failed: {error.Message}")
                { result with Attempted = true }

    let probe () : Darwin.ResearchOs.Contracts.SounioRuntimeProbeReport =
        let diagnostics = ResizeArray<string>()
        let nativeFfi = probeNativeFfi ()
        let sourceInventory = nativeBackendSourceInventory ()
        let snioInventory = snioInteropInventory ()
        let abi = abiInventory ()
        diagnostics.AddRange(nativeFfi.Diagnostics)
        diagnostics.AddRange(sourceInventory.Notes)
        diagnostics.AddRange(snioInventory.Notes)
        diagnostics.AddRange(abi.Notes |> Seq.filter (fun note -> note.Contains("scientific FFI symbols")))

        let binaryVersionJsonAvailable =
            match detectCandidate () with
            | Some (_, soucPath) ->
                let env = Dictionary<string, string>()
                match runProcess (Directory.GetCurrentDirectory()) soucPath [ "--help" ] env with
                | Ok(_, stdout, stderr) ->
                    $"{stdout}\n{stderr}".Contains("--version-json", StringComparison.Ordinal)
                | Error _ -> false
            | None -> false

        match detectCandidate () with
        | None when not nativeFfi.Detected ->
            let snioServer = emptySnioServerProbeResult None None
            diagnostics.Add("No souc binary detected from SOUNIO_SOUC_PATH, PATH, or default roots.")
            let report: Darwin.ResearchOs.Contracts.SounioRuntimeProbeReport =
                { Detected = false
                  Usable = false
                  Source = "missing"
                  SounioRoot = None
                  NativeBackendSourceRoot = sourceInventory.Root
                  SnioEmbedHeaderPath = snioInventory.EmbedHeaderPath
                  SnioProtocolPath = snioInventory.ProtocolPath
                  SnioFsharpProtocolPath = snioInventory.FsharpProtocolPath
                  GitRoot = None
                  SoucPath = None
                  RuntimeLibraryPath = nativeFfi.LibraryPath
                  StdlibPath = None
                  ProbeProgramPath = resolveProbeProgram ()
                  Version = None
                  RemoteUrl = None
                  OfficialRemote = None
                  WorktreeClean = None
                  BinaryVersionJsonAvailable = binaryVersionJsonAvailable
                  SourceVersionJsonAvailable = snioInventory.SourceVersionJsonAvailable
                  NativeFfiDetected = nativeFfi.Detected
                  NativeFfiUsable = nativeFfi.Usable
                  NativeProbeSucceeded = nativeFfi.ProbeSucceeded
                  NativeKernelExecutionAvailable = nativeFfi.KernelExecutionAvailable
                  SnioEmbeddingDetected = snioInventory.ProtocolPath.IsSome || snioInventory.EmbedHeaderPath.IsSome
                  SnioEmbeddingUsable = false
                  KernelSessionProtocolAvailable = snioInventory.ProtocolPath.IsSome && snioInventory.EmbedHeaderPath.IsSome
                  SnioServerProbeAttempted = snioServer.Attempted
                  SnioServerProbeSucceeded = snioServer.Succeeded
                  SnioBuiltInServeSupported = snioServer.BuiltInServeSupported
                  SnioServeEntryPath = snioServer.ServeEntryPath
                  SnioInfoValues = snioServer.InfoValues
                  SnioCapabilities = snioServer.Capabilities
                  SnioHealthOk = snioServer.HealthOk
                  SnioStatsValue = snioServer.StatsValue
                  NativeProbeOutput = nativeFfi.Output
                  RuntimeTensorFfiSymbols = abi.RuntimeTensorSymbols
                  RuntimeUncertainFfiSymbols = abi.RuntimeUncertainSymbols
                  RuntimeOdeFfiSymbols = abi.RuntimeOdeSymbols
                  RuntimeAutodiffFfiSymbols = abi.RuntimeAutodiffSymbols
                  NativeTensorFfiSymbols = sourceInventory.TensorSymbols
                  NativeUncertainFfiSymbols = sourceInventory.UncertainSymbols
                  NativeOdeFfiSymbols = sourceInventory.OdeSymbols
                  NativeAutodiffFfiSymbols = sourceInventory.AutodiffSymbols
                  SourceOnlyScientificFfiSymbols = abi.SourceOnlyScientificSymbols
                  CheckSucceeded = false
                  RunSucceeded = false
                  ProbeOutput = ResizeArray()
                  Diagnostics = diagnostics }
            report
        | None ->
            let snioServer = emptySnioServerProbeResult None None
            diagnostics.Add("No souc binary detected, so native FFI is available only for runtime intrinsics and diagnostics.")
            let report: Darwin.ResearchOs.Contracts.SounioRuntimeProbeReport =
                { Detected = true
                  Usable = false
                  Source = "native-ffi-only"
                  SounioRoot = None
                  NativeBackendSourceRoot = sourceInventory.Root
                  SnioEmbedHeaderPath = snioInventory.EmbedHeaderPath
                  SnioProtocolPath = snioInventory.ProtocolPath
                  SnioFsharpProtocolPath = snioInventory.FsharpProtocolPath
                  GitRoot = None
                  SoucPath = None
                  RuntimeLibraryPath = nativeFfi.LibraryPath
                  StdlibPath = None
                  ProbeProgramPath = resolveProbeProgram ()
                  Version = None
                  RemoteUrl = None
                  OfficialRemote = None
                  WorktreeClean = None
                  BinaryVersionJsonAvailable = binaryVersionJsonAvailable
                  SourceVersionJsonAvailable = snioInventory.SourceVersionJsonAvailable
                  NativeFfiDetected = nativeFfi.Detected
                  NativeFfiUsable = nativeFfi.Usable
                  NativeProbeSucceeded = nativeFfi.ProbeSucceeded
                  NativeKernelExecutionAvailable = nativeFfi.KernelExecutionAvailable
                  SnioEmbeddingDetected = snioInventory.ProtocolPath.IsSome || snioInventory.EmbedHeaderPath.IsSome
                  SnioEmbeddingUsable = false
                  KernelSessionProtocolAvailable = snioInventory.ProtocolPath.IsSome && snioInventory.EmbedHeaderPath.IsSome
                  SnioServerProbeAttempted = snioServer.Attempted
                  SnioServerProbeSucceeded = snioServer.Succeeded
                  SnioBuiltInServeSupported = snioServer.BuiltInServeSupported
                  SnioServeEntryPath = snioServer.ServeEntryPath
                  SnioInfoValues = snioServer.InfoValues
                  SnioCapabilities = snioServer.Capabilities
                  SnioHealthOk = snioServer.HealthOk
                  SnioStatsValue = snioServer.StatsValue
                  NativeProbeOutput = nativeFfi.Output
                  RuntimeTensorFfiSymbols = abi.RuntimeTensorSymbols
                  RuntimeUncertainFfiSymbols = abi.RuntimeUncertainSymbols
                  RuntimeOdeFfiSymbols = abi.RuntimeOdeSymbols
                  RuntimeAutodiffFfiSymbols = abi.RuntimeAutodiffSymbols
                  NativeTensorFfiSymbols = sourceInventory.TensorSymbols
                  NativeUncertainFfiSymbols = sourceInventory.UncertainSymbols
                  NativeOdeFfiSymbols = sourceInventory.OdeSymbols
                  NativeAutodiffFfiSymbols = sourceInventory.AutodiffSymbols
                  SourceOnlyScientificFfiSymbols = abi.SourceOnlyScientificSymbols
                  CheckSucceeded = false
                  RunSucceeded = false
                  ProbeOutput = ResizeArray()
                  Diagnostics = diagnostics }
            report
        | Some(source, soucPath) ->
            let stdlibPath = resolveStdlib soucPath
            let snioServer =
                match stdlibPath with
                | Some stdlib -> probeSnioServer soucPath stdlib snioInventory
                | None -> emptySnioServerProbeResult (Some(soucSupportsFlag soucPath "--serve")) None
            let gitRoot = findGitRoot soucPath
            let remoteUrl = gitRoot |> Option.bind (fun root -> runGit root [ "remote"; "get-url"; "origin" ])
            let worktreeClean =
                gitRoot
                |> Option.bind (fun root ->
                    runGit root [ "status"; "--porcelain" ]
                    |> Option.map String.IsNullOrWhiteSpace)
            let version =
                let env = Dictionary<string, string>()
                match runProcess (Directory.GetCurrentDirectory()) soucPath [ "--version" ] env with
                | Ok(0, stdout, _) ->
                    let trimmed = stdout.Trim()
                    if String.IsNullOrWhiteSpace(trimmed) then None else Some trimmed
                | Ok(_, _, stderr) ->
                    diagnostics.Add($"souc --version failed: {stderr.Trim()}")
                    None
                | Error message ->
                    diagnostics.Add($"souc --version could not start: {message}")
                    None

            let checkSucceeded, runSucceeded, probeOutput =
                match stdlibPath, resolveProbeProgram () with
                | Some stdlib, Some probeProgram ->
                    let env = Dictionary<string, string>()
                    env["SOUNIO_STDLIB_PATH"] <- stdlib

                    let checkResult = runProcess (Directory.GetCurrentDirectory()) soucPath [ "check"; probeProgram ] env
                    let runResult = runProcess (Directory.GetCurrentDirectory()) soucPath [ "run"; probeProgram ] env

                    let checkSucceeded =
                        match checkResult with
                        | Ok(0, _, _) -> true
                        | Ok(_, _, stderr) ->
                            diagnostics.Add($"souc check failed: {stderr.Trim()}")
                            false
                        | Error message ->
                            diagnostics.Add($"souc check could not start: {message}")
                            false

                    let runSucceeded, probeOutput =
                        match runResult with
                        | Ok(0, stdout, _) ->
                            true,
                            stdout.Split(Environment.NewLine, StringSplitOptions.RemoveEmptyEntries)
                            |> resize
                        | Ok(_, _, stderr) ->
                            diagnostics.Add($"souc run failed: {stderr.Trim()}")
                            false, ResizeArray()
                        | Error message ->
                            diagnostics.Add($"souc run could not start: {message}")
                            false, ResizeArray()

                    checkSucceeded, runSucceeded, probeOutput
                | None, _ ->
                    diagnostics.Add("No stdlib path could be resolved for the detected souc binary.")
                    false, false, ResizeArray()
                | _, None ->
                    diagnostics.Add("Runtime probe program is missing from sounio/kernels/runtime_probe.sio.")
                    false, false, ResizeArray()

            let officialRemote = remoteUrl |> Option.map isOfficialRemote
            let sounioRoot =
                stdlibPath
                |> Option.map DirectoryInfo
                |> Option.bind (fun info -> if isNull info.Parent then None else Some info.Parent.FullName)

            match sourceInventory.Root, sounioRoot with
            | Some sourceRoot, Some runtimeRoot when not (String.Equals(sourceRoot, runtimeRoot, StringComparison.OrdinalIgnoreCase)) ->
                diagnostics.Add($"Preferred Sounio source checkout for native backend capabilities is {sourceRoot}, but the currently usable souc/runtime binaries come from {runtimeRoot}.")
            | Some sourceRoot, None ->
                diagnostics.Add($"Preferred Sounio source checkout for native backend capabilities is {sourceRoot}.")
            | _ -> ()

            if officialRemote = Some true && worktreeClean = Some false then
                diagnostics.Add("Detected official Sounio checkout is usable locally but not promotion-grade because the worktree is dirty.")
            if snioInventory.ProtocolPath.IsSome && snioInventory.EmbedHeaderPath.IsSome then
                diagnostics.Add("Official kernel/session embedding ABI is present in the upstream source tree via SNIO (`souc --serve`) and `sounio_embed.h`.")
            if snioInventory.SourceVersionJsonAvailable && not binaryVersionJsonAvailable then
                diagnostics.Add("The upstream source tree advertises --version-json, but the currently built souc binary does not expose it yet.")
            diagnostics.AddRange(snioServer.Diagnostics)
            if snioServer.Succeeded && not (checkSucceeded && runSucceeded) then
                diagnostics.Add("Marked usable via upstream-first SNIO server probe even though the legacy runtime_probe.sio CLI smoke did not complete.")

            let usable =
                showcaseUsable version stdlibPath checkSucceeded runSucceeded snioServer

            let report: Darwin.ResearchOs.Contracts.SounioRuntimeProbeReport =
                { Detected = true
                  Usable = usable
                  Source = source
                  SounioRoot = sounioRoot
                  NativeBackendSourceRoot = sourceInventory.Root
                  SnioEmbedHeaderPath = snioInventory.EmbedHeaderPath
                  SnioProtocolPath = snioInventory.ProtocolPath
                  SnioFsharpProtocolPath = snioInventory.FsharpProtocolPath
                  GitRoot = gitRoot
                  SoucPath = Some soucPath
                  RuntimeLibraryPath = nativeFfi.LibraryPath
                  StdlibPath = stdlibPath
                  ProbeProgramPath = resolveProbeProgram ()
                  Version = version
                  RemoteUrl = remoteUrl
                  OfficialRemote = officialRemote
                  WorktreeClean = worktreeClean
                  BinaryVersionJsonAvailable = binaryVersionJsonAvailable
                  SourceVersionJsonAvailable = snioInventory.SourceVersionJsonAvailable
                  NativeFfiDetected = nativeFfi.Detected
                  NativeFfiUsable = nativeFfi.Usable
                  NativeProbeSucceeded = nativeFfi.ProbeSucceeded
                  NativeKernelExecutionAvailable = nativeFfi.KernelExecutionAvailable
                  SnioEmbeddingDetected = snioInventory.ProtocolPath.IsSome || snioInventory.EmbedHeaderPath.IsSome
                  SnioEmbeddingUsable = snioServer.Succeeded
                  KernelSessionProtocolAvailable = snioInventory.ProtocolPath.IsSome && snioInventory.EmbedHeaderPath.IsSome
                  SnioServerProbeAttempted = snioServer.Attempted
                  SnioServerProbeSucceeded = snioServer.Succeeded
                  SnioBuiltInServeSupported = snioServer.BuiltInServeSupported
                  SnioServeEntryPath = snioServer.ServeEntryPath
                  SnioInfoValues = snioServer.InfoValues
                  SnioCapabilities = snioServer.Capabilities
                  SnioHealthOk = snioServer.HealthOk
                  SnioStatsValue = snioServer.StatsValue
                  NativeProbeOutput = nativeFfi.Output
                  RuntimeTensorFfiSymbols = abi.RuntimeTensorSymbols
                  RuntimeUncertainFfiSymbols = abi.RuntimeUncertainSymbols
                  RuntimeOdeFfiSymbols = abi.RuntimeOdeSymbols
                  RuntimeAutodiffFfiSymbols = abi.RuntimeAutodiffSymbols
                  NativeTensorFfiSymbols = sourceInventory.TensorSymbols
                  NativeUncertainFfiSymbols = sourceInventory.UncertainSymbols
                  NativeOdeFfiSymbols = sourceInventory.OdeSymbols
                  NativeAutodiffFfiSymbols = sourceInventory.AutodiffSymbols
                  SourceOnlyScientificFfiSymbols = abi.SourceOnlyScientificSymbols
                  CheckSucceeded = checkSucceeded
                  RunSucceeded = runSucceeded
                  ProbeOutput = probeOutput
                  Diagnostics = diagnostics }
            report

    let private executeKernelWithIds
        (executionId: string)
        (jobId: string)
        (request: Darwin.ResearchOs.Contracts.SounioKernelExecutionRequest)
        : Darwin.ResearchOs.Contracts.SounioKernelExecutionResult =
        let createdAt = DateTime.UtcNow.ToString("O")
        let runtime = probe ()
        let diagnostics = ResizeArray<string>(runtime.Diagnostics)
        let formatI64Values (label: string) (values: int64[]) =
            let rendered = values |> Array.map string |> String.concat ", "
            $"{label}=[{rendered}]"

        let kernelPath =
            resolveKernelPath request.KernelPath
            |> function
                | Some path -> path
                | None ->
                    diagnostics.Add("Requested kernel path was not found and no default probe kernel is available.")
                    request.KernelPath |> Option.defaultValue "missing-kernel"

        let runtimeProbeKernelRequested =
            File.Exists(kernelPath)
            && String.Equals(Path.GetFileName(kernelPath), "runtime_probe.sio", StringComparison.OrdinalIgnoreCase)

        let checkSucceeded, runSucceeded, snioAttempted, snioUsed, output =
            match runtime.Usable, runtime.SoucPath, runtime.StdlibPath with
            | true, Some soucPath, Some stdlibPath when File.Exists(kernelPath) ->
                let trySnioExecution () =
                    let builtInServeSupported = soucSupportsFlag soucPath "--serve"
                    let sourceRoot = resolveSnioSourceRoot runtime
                    let preferredServeEntry =
                        sourceRoot
                        |> Option.bind resolveSnioServeEntryPath

                    if not builtInServeSupported && preferredServeEntry.IsNone then
                        if runtime.KernelSessionProtocolAvailable then
                            diagnostics.Add("SNIO kernel/session ABI is present upstream, but the currently selected souc binary cannot yet start a session server via --serve and no serve_entry.sio source path is available.")
                        { Attempted = false
                          Used = false
                          CheckSucceeded = false
                          RunSucceeded = false
                          Output = ResizeArray() }
                    else
                        try
                            let snio =
                                if builtInServeSupported then
                                    new SounioSnioProcess(soucPath, Some stdlibPath)
                                else
                                    new SounioSnioProcess(soucPath, Some stdlibPath, preferredServeEntry.Value)
                            use snio = snio
                            let sessionId = snio.SessionCreate()
                            try
                                let described = snio.KernelDescribe(sessionId, kernelPath)
                                let describeOk = described.Length > 0 && described[0] = 1L
                                if not describeOk then
                                    let describeSummary = formatI64Values "describe" described
                                    diagnostics.Add($"SNIO kernel describe did not report success for {kernelPath}: {describeSummary}")
                                    { Attempted = true
                                      Used = false
                                      CheckSucceeded = false
                                      RunSucceeded = false
                                      Output = ResizeArray() }
                                else
                                    let kernelId = if described.Length > 1 then described[1] else 0L
                                    let executionValues = snio.KernelExecute(sessionId, kernelId, [||])
                                    let outputValues = snio.KernelOutput(sessionId)
                                    let diagnosticValues = snio.KernelDiagnostics(sessionId)
                                    let artifactValues = snio.KernelArtifacts(sessionId)
                                    let executeSummary = formatI64Values "snio.kernel_execute" executionValues
                                    let outputSummary = formatI64Values "snio.kernel_output" outputValues
                                    let diagnosticSummary = formatI64Values "snio.kernel_diagnostics" diagnosticValues
                                    let artifactSummary = formatI64Values "snio.kernel_artifacts" artifactValues

                                    let snioOutput = ResizeArray<string>()
                                    snioOutput.Add(executeSummary)
                                    snioOutput.Add(outputSummary)
                                    snioOutput.Add(diagnosticSummary)
                                    snioOutput.Add(artifactSummary)
                                    diagnostics.Add("Kernel execution completed through upstream SNIO session/kernel embedding ABI.")
                                    { Attempted = true
                                      Used = true
                                      CheckSucceeded = true
                                      RunSucceeded = true
                                      Output = snioOutput }
                            finally
                                try
                                    snio.SessionDestroy(sessionId)
                                with error ->
                                    diagnostics.Add($"SNIO session destroy reported an error: {error.Message}")
                        with error ->
                            diagnostics.Add($"SNIO kernel/session execution attempt failed. {error.Message}")
                            { Attempted = true
                              Used = false
                              CheckSucceeded = false
                              RunSucceeded = false
                              Output = ResizeArray() }

                let snio = trySnioExecution ()
                if snio.Used then
                    snio.CheckSucceeded, snio.RunSucceeded, snio.Attempted, snio.Used, snio.Output
                elif request.RequireSnio then
                    diagnostics.Add("RequireSnio=true blocked CLI fallback because no usable SNIO session/kernel path succeeded.")
                    false, false, snio.Attempted, snio.Used, ResizeArray()
                else
                    let env = Dictionary<string, string>()
                    env["SOUNIO_STDLIB_PATH"] <- stdlibPath

                    if runtime.NativeFfiUsable && not runtime.NativeKernelExecutionAvailable then
                        diagnostics.Add("Native Sounio FFI is available, but kernel execution still uses souc because the current runtime ABI does not expose a kernel-run entrypoint.")
                    if runtime.KernelSessionProtocolAvailable && snio.Attempted then
                        diagnostics.Add("Darwin attempted the upstream SNIO kernel/session ABI first, but this execution used CLI fallback because the selected souc binary could not complete the SNIO path.")
                    elif runtime.KernelSessionProtocolAvailable then
                        diagnostics.Add("Darwin knows about the upstream SNIO kernel/session ABI, but this execution used CLI fallback because the current souc binary could not be used through the available SNIO startup paths.")

                    let checkResult = runProcess (Directory.GetCurrentDirectory()) soucPath [ "check"; kernelPath ] env
                    let runResult = runProcess (Directory.GetCurrentDirectory()) soucPath [ "run"; kernelPath ] env

                    let checkSucceeded =
                        match checkResult with
                        | Ok(0, _, _) -> true
                        | Ok(_, _, stderr) ->
                            diagnostics.Add($"souc check failed for {kernelPath}: {stderr.Trim()}")
                            false
                        | Error message ->
                            diagnostics.Add($"souc check could not start for {kernelPath}: {message}")
                            false

                    let runSucceeded, output =
                        match runResult with
                        | Ok(0, stdout, _) ->
                            true,
                            stdout.Split(Environment.NewLine, StringSplitOptions.RemoveEmptyEntries)
                            |> resize
                        | Ok(_, _, stderr) ->
                            diagnostics.Add($"souc run failed for {kernelPath}: {stderr.Trim()}")
                            false, ResizeArray()
                        | Error message ->
                            diagnostics.Add($"souc run could not start for {kernelPath}: {message}")
                            false, ResizeArray()

                    checkSucceeded, runSucceeded, snio.Attempted, false, output
            | _ when runtime.NativeFfiUsable && runtime.NativeProbeSucceeded && runtimeProbeKernelRequested && not request.RequireSnio ->
                diagnostics.Add("Executed runtime_probe.sio via native Sounio FFI fallback because no cluster-usable souc/SNIO kernel execution path is available.")
                true, true, false, false, ResizeArray(runtime.NativeProbeOutput)
            | _ ->
                if request.RequireSnio then
                    diagnostics.Add("RequireSnio=true blocked CLI fallback because no usable SNIO session/kernel path succeeded.")
                if not runtime.Detected then
                    diagnostics.Add("No Sounio runtime is currently detected.")
                elif not runtime.Usable then
                    diagnostics.Add("Detected Sounio runtime is not usable for kernel execution.")
                elif not (File.Exists(kernelPath)) then
                    diagnostics.Add($"Kernel path does not exist: {kernelPath}")
                false, false, false, false, ResizeArray()

        let result: Darwin.ResearchOs.Contracts.SounioKernelExecutionResult =
            { ExecutionId = executionId
              JobId = jobId
              CreatedAt = createdAt
              Label =
                if String.IsNullOrWhiteSpace(request.Label) then
                    "Sounio Kernel Execution"
                else
                    request.Label
              KernelPath = kernelPath
              Status = if checkSucceeded && runSucceeded then "completed" else "failed"
              CheckSucceeded = checkSucceeded
              RunSucceeded = runSucceeded
              SnioAttempted = snioAttempted
              SnioUsed = snioUsed
              Output = output
              Diagnostics = diagnostics
              ArtifactIds = ResizeArray()
              Runtime = runtime }
        result

    let executeKernel (request: Darwin.ResearchOs.Contracts.SounioKernelExecutionRequest) : Darwin.ResearchOs.Contracts.SounioKernelExecutionResult =
        let executionId = Guid.NewGuid().ToString("N")
        let jobId = $"sounio-kernel-{executionId}"
        executeKernelWithIds executionId jobId request

    let executeKernelForJob (job: Darwin.ResearchOs.Contracts.JobRecord) (request: Darwin.ResearchOs.Contracts.SounioKernelExecutionRequest) =
        executeKernelWithIds job.OwnerId job.JobId request

    let persistKernelExecution
        (store: Darwin.ResearchOs.Core.IResearchStore)
        (objectStore: Darwin.ResearchOs.Core.IObjectStore)
        (backendName: string)
        (executor: string)
        (request: Darwin.ResearchOs.Contracts.SounioKernelExecutionRequest)
        (result: Darwin.ResearchOs.Contracts.SounioKernelExecutionResult)
        =
        let artifactIds = ResizeArray<string>()

        let kernelExecutionDictionary (result: Darwin.ResearchOs.Contracts.SounioKernelExecutionResult) =
            let dict = Dictionary<string, obj>()
            dict["execution_id"] <- box result.ExecutionId
            dict["job_id"] <- box result.JobId
            dict["created_at"] <- box result.CreatedAt
            dict["label"] <- box result.Label
            dict["kernel_path"] <- box result.KernelPath
            dict["status"] <- box result.Status
            dict["check_succeeded"] <- box result.CheckSucceeded
            dict["run_succeeded"] <- box result.RunSucceeded
            dict["snio_attempted"] <- box result.SnioAttempted
            dict["snio_used"] <- box result.SnioUsed
            dict["output"] <- box (ResizeArray(result.Output))
            dict["diagnostics"] <- box (ResizeArray(result.Diagnostics))
            dict["artifact_ids"] <- box (ResizeArray(result.ArtifactIds))
            dict["require_snio"] <- box request.RequireSnio
            dict["runtime"] <- box result.Runtime
            dict

        let persistArtifact name kind contentType relativePath content =
            let stored = objectStore.WriteText(relativePath, content)
            let artifact: Darwin.ResearchOs.Contracts.ArtifactRef =
                { ArtifactId = Guid.NewGuid().ToString("N")
                  CreatedAt = DateTime.UtcNow.ToString("O")
                  OwnerType = "sounio_kernel_run"
                  OwnerId = result.ExecutionId
                  Name = name
                  Kind = kind
                  Path = stored.LocalPath
                  Uri = stored.Uri
                  Description = $"Artifact generated by F# Sounio kernel run {result.ExecutionId}."
                  ContentType = defaultArg stored.ContentType contentType
                  Bytes =
                    match stored.Bytes with
                    | Some value -> Nullable value
                    | None -> Nullable() }
            store.SaveArtifact(artifact)
            artifactIds.Add(artifact.ArtifactId)

        if request.PersistArtifacts then
            let outputText =
                if result.Output.Count = 0 then ""
                else String.Join(Environment.NewLine, result.Output)

            let diagnosticsText =
                if result.Diagnostics.Count = 0 then ""
                else String.Join(Environment.NewLine, result.Diagnostics)

            persistArtifact
                "kernel_stdout"
                "text"
                "text/plain"
                (Path.Combine("rewrite", "sounio-runtime", result.ExecutionId, "stdout.txt"))
                outputText

            persistArtifact
                "kernel_diagnostics"
                "text"
                "text/plain"
                (Path.Combine("rewrite", "sounio-runtime", result.ExecutionId, "diagnostics.txt"))
                diagnosticsText

            let reportStored =
                objectStore.WriteJson(
                    Path.Combine("rewrite", "sounio-runtime", result.ExecutionId, "report.json"),
                    kernelExecutionDictionary result
                )

            let reportArtifact: Darwin.ResearchOs.Contracts.ArtifactRef =
                { ArtifactId = Guid.NewGuid().ToString("N")
                  CreatedAt = DateTime.UtcNow.ToString("O")
                  OwnerType = "sounio_kernel_run"
                  OwnerId = result.ExecutionId
                  Name = "kernel_report"
                  Kind = "json"
                  Path = reportStored.LocalPath
                  Uri = reportStored.Uri
                  Description = $"Structured report for F# Sounio kernel run {result.ExecutionId}."
                  ContentType = defaultArg reportStored.ContentType "application/json"
                  Bytes =
                    match reportStored.Bytes with
                    | Some value -> Nullable value
                    | None -> Nullable() }
            store.SaveArtifact(reportArtifact)
            artifactIds.Add(reportArtifact.ArtifactId)

        let updated = { result with ArtifactIds = artifactIds }
        let existingJob = store.TryGetJob(result.JobId)
        let basePayload =
            match existingJob with
            | Some job -> Dictionary<string, obj>(job.ResultPayload)
            | None -> Dictionary<string, obj>()
        basePayload["backend"] <- box backendName
        basePayload["artifact_prefix"] <- box $"{updated.ExecutionId}/"
        basePayload["cancel_requested"] <-
            if basePayload.ContainsKey("cancel_requested") then basePayload["cancel_requested"] else box false
        basePayload["execution_id"] <- box updated.ExecutionId
        basePayload["snio_attempted"] <- box updated.SnioAttempted
        basePayload["snio_used"] <- box updated.SnioUsed

        let requestPayload =
            let payload =
                match existingJob with
                | Some job -> Dictionary<string, obj>(job.RequestPayload)
                | None -> Dictionary<string, obj>()
            payload["label"] <- box request.Label
            payload["kernel_path"] <- box (defaultArg request.KernelPath "")
            payload["persist_artifacts"] <- box request.PersistArtifacts
            payload["require_snio"] <- box request.RequireSnio
            payload

        let job: Darwin.ResearchOs.Contracts.JobRecord =
            { JobId = updated.JobId
              CreatedAt = existingJob |> Option.map _.CreatedAt |> Option.defaultValue updated.CreatedAt
              UpdatedAt = DateTime.UtcNow.ToString("O")
              OwnerType = "sounio_kernel_run"
              OwnerId = updated.ExecutionId
              Kind = "sounio_runtime"
              Status = updated.Status
              QueueName = existingJob |> Option.map _.QueueName |> Option.defaultValue "rewrite-fsharp"
              Executor = existingJob |> Option.map _.Executor |> Option.defaultValue executor
              RequestPayload = requestPayload
              ResultPayload = basePayload
              Error =
                if updated.Status = "completed" then ""
                elif updated.Diagnostics.Count > 0 then updated.Diagnostics[0]
                else "Sounio kernel execution failed." }
        store.SaveJob(job)
        let heartbeat: Darwin.ResearchOs.Contracts.WorkerHeartbeat =
            { WorkerId = $"sounio-runtime-{updated.ExecutionId}"
              WorkerKind = "sounio_runtime"
              Backend = backendName
              Executor = executor
              Hostname = Environment.MachineName
              CreatedAt = updated.CreatedAt
              UpdatedAt = DateTime.UtcNow.ToString("O")
              Notes = $"Executed {updated.KernelPath}" }
        store.SaveWorkerHeartbeat(heartbeat)
        updated
