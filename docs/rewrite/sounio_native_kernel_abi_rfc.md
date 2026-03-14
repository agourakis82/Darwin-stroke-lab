# RFC: Native Kernel Execution ABI For Sounio

## Status

- Owner: Darwin Research OS rewrite lane
- Audience: Sounio runtime/compiler maintainer
- Intent: focused implementation request
- Scope: close the gap between the existing native runtime FFI and full host-driven kernel execution

## Why This RFC Exists

The current Sounio runtime FFI already gives us a meaningful native surface:

- memory intrinsics
- math intrinsics
- `Knowledge`
- `BootstrapKnowledge`
- `CVKnowledge`
- dispatch / handler symbols for runtime effects

That is enough for the F# lane to load `libsounio_runtime` natively and exercise epistemic operations without shelling out.

What it still does **not** provide is a host-facing kernel execution contract.

Today, Darwin can truthfully say:

- native runtime FFI exists and is usable
- native kernel execution ABI does not yet exist
- `.sio` kernel execution still falls back to `souc`

This RFC asks for the minimum native ABI needed to remove that fallback.

## Current Observed ABI Reality

On macOS, the runtime dylib currently exports families like:

- `sounio_knowledge_*`
- `sounio_bootstrap_*`
- `sounio_cv_*`
- `sounio_dispatch_*`
- `sounio_push_handler_*`
- `sounio_pop_handler`
- `sounio_handler_depth`

What Darwin did **not** detect in the dylib:

- `session_create`
- `session_destroy`
- `kernel_load`
- `kernel_run`
- `execution_status`
- `execution_output`
- `execution_artifact_*`

That is the exact reason `souc` is still in the operational path.

## What We Need

### 1. Session lifecycle

The host needs to create and destroy a reusable runtime session.

Suggested shape:

```c
typedef struct sounio_session sounio_session;

int sounio_session_create(const char* stdlib_path, sounio_session** out);
int sounio_session_destroy(sounio_session* session);
```

### 2. Kernel introspection

The host needs to inspect a kernel without running it.

Suggested shape:

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

### 3. Kernel execution

The host needs to run a kernel with structured input and retrieve structured output.

Suggested shape:

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

### 4. Runtime metadata

The host also needs a boring, versioned metadata surface:

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

## Why This Matters For Darwin

Without the native kernel ABI, Darwin must keep doing this:

- queue job in F#
- shell out to `souc`
- parse stdout/stderr
- reconstruct execution semantics in the host

With the native kernel ABI, Darwin can do this instead:

- queue job in F#
- open runtime session
- inspect kernel contract
- run kernel natively
- collect typed output, artifacts, diagnostics, provenance
- persist all of it directly into the platform

That removes a large amount of orchestration fragility.

## Acceptance Criteria

This RFC is satisfied when Darwin can do all of the following without shelling out:

1. create a runtime session from F#
2. inspect a kernel contract
3. execute a `.sio` kernel with structured input
4. retrieve structured output and diagnostics
5. enumerate structured artifacts
6. record runtime/compiler/ABI metadata for benchmark provenance

## Bottom Line

Sounio already has a real native runtime FFI.

That is a strong foundation.

The next decisive step is not “more FFI exists.”

The next decisive step is:

- make kernel execution part of that FFI
- make the contract inspectable
- make the result structured

That is what will let Darwin move `souc` out of the operational hot path.
