# RFC: Export Scientific Native Backend FFI From Sounio Runtime

## Why This Exists

Darwin can now see two things clearly:

1. the current `libsounio_runtime` dylib exposes a real host-facing FFI for
   `knowledge` and core intrinsics
2. the newer Sounio native backend sources already define additional C ABI
   scientific functions for:
   - tensor
   - uncertain propagation
   - ODE
   - autodiff

Those newer functions are visible in compiler native backend sources, but they
are not yet exported through the current runtime dylib that Darwin loads.

That means the Darwin F# lane can truthfully say:

- native Sounio FFI exists and is usable today
- scientific native backend FFI exists in source today
- those scientific functions are not yet part of the host-loadable runtime ABI

## What Darwin Detects Today

### Already exported by the runtime dylib

- `sounio_knowledge_*`
- math intrinsics
- memory intrinsics
- print/assert/panic

### Present in compiler native backend sources but not in the runtime dylib

Tensor:

- `sounio_tensor_matmul`
- `sounio_tensor_einsum`
- `sounio_tensor_reshape`
- `sounio_tensor_transpose`
- `sounio_tensor_add`
- `sounio_tensor_mul`
- `sounio_tensor_scale`
- `sounio_tensor_matvec`

Uncertain:

- `sounio_uncertain_add`
- `sounio_uncertain_sub`
- `sounio_uncertain_mul`
- `sounio_uncertain_div`
- `sounio_uncertain_combine`
- `sounio_uncertain_scale`
- `sounio_uncertain_pow`
- `sounio_uncertain_sqrt`
- `sounio_uncertain_exp`
- `sounio_uncertain_log`

ODE:

- `sounio_ode_dopri5_step`
- `sounio_ode_cashkarp_step`
- `sounio_ode_step`
- `sounio_ode_bdf_step`
- `sounio_ode_lsoda_step`

Autodiff:

- `sounio_autodiff_forward`
- `sounio_autodiff_reverse`
- `sounio_dual_add`
- `sounio_dual_sub`
- `sounio_dual_mul`
- `sounio_dual_div`
- `sounio_dual_exp`
- `sounio_dual_log`
- `sounio_dual_sin`
- `sounio_dual_cos`
- `sounio_dual_pow`
- `sounio_dual_sqrt`

## What We Are Asking For

Promote these scientific native backend functions into a stable host-loadable
runtime surface.

That does not mean every compiler internal detail must become public.

It means the runtime package that host systems load should expose a supported
subset of these scientific operations as part of the official FFI surface.

## Why This Matters For Darwin

If these functions become part of the loadable runtime ABI, Darwin can:

- call scientific kernels more directly from F#
- reduce orchestration glue
- stop treating tensor/uncertain/ODE/autodiff as source-only capabilities
- persist stronger runtime provenance for benchmark claims
- move more execution work out of shelling through `souc`

This does not replace the need for a native kernel execution ABI.

It complements it.

## Minimum Recommended Export Set

### Priority 1

- `sounio_tensor_*`
- `sounio_uncertain_*`

These are immediately useful for benchmark and scientific platform work.

### Priority 2

- `sounio_dual_*`
- `sounio_autodiff_*`

These matter for differentiable scientific kernels and future optimizer loops.

### Priority 3

- `sounio_ode_*`

These matter for simulation-heavy labs and future domain expansion.

## ABI Expectations

If these functions are exported officially, they should come with:

- documented signatures
- memory ownership rules
- pointer safety expectations
- stability guarantees by ABI version
- indication of which symbols are experimental vs stable

## Bottom Line

Sounio already has more scientific native FFI than the current runtime dylib
suggests.

The next practical step is to expose that scientific backend power through the
host-loadable runtime ABI, so Darwin and similar systems can use it directly.
