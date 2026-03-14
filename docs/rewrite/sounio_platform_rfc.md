# RFC: Sounio Platform Requirements For Darwin Research OS

## Status

- Owner: Darwin Research OS integration lane
- Audience: Sounio language/runtime maintainer
- Intent: implementation-grade request, not marketing
- Scope: what Sounio needs to provide so it can become the primary scientific kernel for a benchmark-first, cluster-native research operating system

## Executive Summary

Darwin Research OS is evolving toward this split:

- `Sounio`: scientific kernel, experiment semantics, domain computation, provenance-sensitive execution
- `F#`: typed control plane, jobs, campaigns, programs, portfolio, auth, API, workers, K8s/Kueue integration
- `Stan`: narrow probabilistic baseline lane for calibration and uncertainty comparison

Today, Sounio is already useful in the project. It can be located, checked, and executed, and the F# rewrite lane already runs real `.sio` kernels against the native local toolchain. That is enough for development, but not enough for Sounio to become the operational center of a serious research platform.

The current main blockers are:

1. Sounio is still primarily consumed through `souc check/run`, which makes orchestration, queueing, diagnostics, and cluster execution more fragile than they should be.
2. There is no stable, versioned, official embedding contract for host runtimes like F#.
3. Kernel inputs, outputs, metrics, provenance, and artifacts do not yet have a first-class runtime contract.
4. Cluster packaging, reproducibility, and promotion-grade runtime guarantees are not yet strong enough to support benchmark-backed scientific claims at scale.

This RFC asks for Sounio to provide the minimum operational substrate needed to make it the real scientific kernel of Darwin Research OS.

## What Success Looks Like

Sounio is not just “a language we shell out to.” It becomes:

- embeddable from F# through a stable official runtime API
- capable of running benchmark kernels with typed input/output contracts
- able to emit artifacts, provenance, and diagnostics as structured runtime values
- bundleable for K8s/HPC execution with deterministic promotion rules
- credible as a language someone would adopt for Scientific/HPC systems, not just isolated kernels

## Current Integration Reality

In the current repo:

- the F# lane already probes the native Sounio runtime and executes a real kernel
- benchmark jobs can already be queued and drained entirely through the F# control plane
- campaigns and programs can already ride the F# queue
- K8s/HPC is already a first-class architectural target in the Python lane

That means the host platform is ready for Sounio to take on more responsibility. The missing pieces are now mostly on the Sounio runtime/productization side.

## Non-Goals

This RFC is **not** asking Sounio to:

- replace the Darwin control plane
- become a web framework
- own auth, HTTP serving, or Kubernetes object lifecycle directly
- replace Stan as a probabilistic baseline lane
- replace the upstream compiler repo with product-specific Darwin code

The goal is a clean kernel/runtime contract, not scope explosion.

## Required Deliverables

### 1. Stable Official FFI / Embedding ABI

Sounio should expose a stable host-facing ABI that supports:

- runtime discovery
- runtime initialization and shutdown
- kernel compilation / loading
- kernel execution
- structured result retrieval
- structured diagnostics retrieval
- artifact enumeration and retrieval
- version and capability reporting

This ABI should be:

- officially documented
- versioned
- backward-compatible within a declared major line
- usable from F#, Rust, and C-compatible hosts

### 2. Embeddable Runtime Library

In addition to the `souc` CLI, Sounio should ship a runtime library suitable for embedding:

- shared library target, e.g. `.dylib` / `.so`
- narrow C ABI, even if the implementation is Rust or another internal language
- explicit memory ownership rules
- explicit lifecycle rules
- thread-safety guarantees documented

The CLI remains useful, but it should not be the only production integration surface.

### 3. First-Class Kernel Contract

Kernels need an official operational contract.

A kernel should expose:

- identity
- version
- expected input schema
- expected output schema
- produced artifact kinds
- required stdlib/runtime capabilities
- provenance fields
- confidence/uncertainty fields, where applicable

The runtime should support loading this contract without executing the full kernel.

### 4. Deterministic Packaging For Cluster/HPC

Sounio should support packaging a kernel for reliable execution across workers and clusters:

- kernel hash
- stdlib hash
- compiler version
- runtime ABI version
- dependency lock or equivalent
- build metadata
- source provenance

This package should be promotable from:

- `dev-usable`
- `benchmark-usable`
- `promotion-grade`

The distinction matters. Darwin already surfaces the difference between “usable local runtime” and “clean official runtime eligible for stronger claims.”

### 5. Structured Provenance And Diagnostics

The runtime should return structured provenance and diagnostics, not only text streams.

Required outputs:

- build diagnostics
- runtime diagnostics
- capability flags
- artifact metadata
- input lineage
- execution timestamps
- kernel hash / stdlib hash / runtime version
- confidence or knowledge metadata when the kernel uses those semantics

## Proposed Runtime Surface

The exact names can change, but Darwin needs an interface shaped roughly like this.

### Runtime Discovery

```c
typedef struct {
  const char* runtime_version;
  const char* compiler_version;
  const char* abi_version;
  const char* stdlib_path;
  int supports_gpu;
  int supports_provenance;
  int supports_kernel_contracts;
} sounio_runtime_info;

int sounio_runtime_get_info(sounio_runtime_info* out);
```

### Session Lifecycle

```c
typedef struct sounio_session sounio_session;

int sounio_session_create(const char* stdlib_path, sounio_session** out);
int sounio_session_destroy(sounio_session* session);
```

### Kernel Contract / Introspection

```c
typedef struct {
  const char* kernel_id;
  const char* kernel_version;
  const char* input_schema_json;
  const char* output_schema_json;
  const char* artifact_schema_json;
  const char* capability_requirements_json;
} sounio_kernel_contract;

int sounio_kernel_describe(
  sounio_session* session,
  const char* kernel_path,
  sounio_kernel_contract* out
);
```

### Kernel Execution

```c
typedef struct sounio_execution sounio_execution;

int sounio_kernel_run_json(
  sounio_session* session,
  const char* kernel_path,
  const char* input_json,
  sounio_execution** out
);

int sounio_execution_status(sounio_execution* execution, int* out_status);
int sounio_execution_output_json(sounio_execution* execution, const char** out_json);
int sounio_execution_diagnostics_json(sounio_execution* execution, const char** out_json);
int sounio_execution_artifact_count(sounio_execution* execution, int* out_count);
int sounio_execution_artifact_json(sounio_execution* execution, int index, const char** out_json);
int sounio_execution_destroy(sounio_execution* execution);
```

This is intentionally boring. That is a good thing. Darwin needs boring reliability here.

## Kernel Contract Requirements

The kernel contract should let the host know:

- what input shape is required
- what output values are expected
- what artifact types can be emitted
- what guarantees are available

Minimum contract sections:

### Input

- schema
- required fields
- optional fields
- size constraints
- accepted tensor/array types
- accepted hypercomplex representations

### Output

- metrics
- predictions
- saliency or explanation structures
- calibration/confidence sections
- provenance summary

### Artifacts

- name
- kind
- MIME type
- whether text, JSON, binary, tensor, or image
- whether reproducibility-critical

### Capabilities

- requires GPU or not
- CPU fallback available or not
- minimum ABI version
- minimum stdlib version
- supported execution modes

## Provenance Model

Darwin wants Sounio to be more than “numerical code that ran.” We want it to be able to say:

- what kernel ran
- from what source hash
- with what runtime
- using what stdlib
- against what declared input shape
- producing what artifacts
- under what confidence semantics

If Sounio has `Knowledge<T>` or related epistemic semantics, those should be exportable as structured values.

Minimum provenance payload:

```json
{
  "kernel_id": "stroke.hypercomplex.score",
  "kernel_hash": "sha256:...",
  "runtime_version": "0.x.y",
  "stdlib_hash": "sha256:...",
  "execution_started_at": "...",
  "execution_finished_at": "...",
  "inputs": {
    "shape": "...",
    "schema_version": "..."
  },
  "artifacts": [
    {
      "name": "heatmap",
      "kind": "tensor",
      "content_type": "application/octet-stream"
    }
  ],
  "confidence": {
    "model": "knowledge",
    "supported": true
  }
}
```

## GPU Requirements

The Darwin side does not need a vague “GPU backend exists.” It needs operational GPU support.

Required GPU-facing capabilities:

- detect GPU support programmatically
- detect why GPU is unavailable
- select execution mode explicitly
- expose CPU fallback predictably
- export kernel compilation diagnostics
- surface backend identity in the result payload

Desired runtime flags:

- `execution_mode = cpu | gpu`
- `gpu_backend = metal | cuda | other`
- `fallback_used = true | false`

This is especially important because Darwin spans:

- local Mac development
- Linux K8s
- HPC workloads

The runtime needs to make those differences explicit.

## Packaging And Promotion

Darwin needs two kinds of Sounio use:

### 1. Development Use

Allowed:

- dirty worktree
- local stdlib
- local toolchain
- runtime probes

### 2. Promotion-Grade Use

Required:

- official remote
- clean worktree or versioned release artifact
- pinned stdlib
- versioned compiler/runtime
- reproducible kernel package

Sounio should expose enough metadata for the host to decide whether a runtime is:

- usable
- benchmark-usable
- promotion-grade

This distinction already exists conceptually in Darwin. Sounio should make it official.

## Build And Distribution Expectations

Sounio should ship or support:

- `souc` CLI for development and diagnostics
- embeddable runtime library for host languages
- packageable runtime bundle for container images
- machine-readable version and capability output

For Darwin, the ideal distribution set is:

- CLI
- shared runtime library
- lockable stdlib package
- machine-readable runtime manifest

## Expected F# Integration Shape

Darwin’s target F# integration should look like this:

- `Darwin.ResearchOs.Core.SounioRuntime` wraps the official runtime library
- `Darwin.ResearchOs.Worker` uses Sounio runtime for benchmark/job execution
- `Darwin.ResearchOs.Api` uses the same runtime for diagnostics and lightweight execution requests
- K8s/HPC jobs run the same packaged runtime contract

The F# side should not need to:

- scrape stdout for core outputs
- infer runtime capabilities from ad hoc strings
- guess artifact shapes

## Expected K8s/HPC Integration Shape

Darwin already has:

- queue/job abstraction
- K8s/Kueue direction
- benchmark-first lifecycle

Sounio should fit into that with:

- packaged kernels
- declared runtime capabilities
- deterministic artifact layout
- promotion metadata

Ideal job lifecycle:

1. host resolves packaged kernel
2. host validates runtime + stdlib + ABI
3. host executes kernel with declared input contract
4. host stores structured result + artifacts
5. host records provenance and promotion status

## Suggested Phased Delivery

### Phase 1: Runtime Stability

- official ABI versioning
- embeddable runtime library
- basic FFI docs
- runtime info / version / capability API

### Phase 2: Kernel Contract

- kernel introspection API
- structured input/output/artifact schema
- diagnostics JSON

### Phase 3: Provenance And Promotion

- provenance payloads
- promotion-grade metadata
- stdlib hashing / packaging

### Phase 4: GPU Operationalization

- explicit GPU capability reporting
- backend selection
- structured GPU diagnostics

### Phase 5: Cluster Packaging

- packaged kernel bundle
- deterministic runtime package
- documentation for worker/container usage

## Acceptance Tests Darwin Would Run

Sounio should be considered ready for full kernel ownership in Darwin when these pass:

1. F# can initialize the runtime without shelling out.
2. F# can introspect a kernel contract without running it.
3. F# can execute a kernel and capture structured output.
4. F# can retrieve structured diagnostics and artifacts.
5. F# can distinguish `usable` from `promotion-grade`.
6. The same kernel package runs locally and in a K8s worker with consistent metadata.
7. Provenance emitted by Sounio is sufficient to anchor benchmark claims.

## What This Unlocks

If Sounio ships this substrate, Darwin can:

- move benchmark execution ownership from Python compatibility code into the F# rewrite lane
- use Sounio as the primary scientific kernel rather than a subprocess
- run cluster jobs with promotion-grade kernel packages
- attach provenance, confidence, and diagnostics directly to runs, campaigns, programs, and portfolio summaries
- make a stronger adoption case for Sounio in Scientific/HPC settings

## Direct Ask To The Sounio Maintainer

If only three things can be done soon, they should be:

1. stable embeddable FFI/runtime API
2. first-class kernel contract with structured outputs/artifacts
3. deterministic package + promotion metadata for cluster execution

Those three alone would change Sounio from “powerful but external” to “the operational center of a benchmark-first research OS.”
