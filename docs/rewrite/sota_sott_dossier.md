# SOTA / SOTT Dossier

## Thesis

The rewrite target is not “replace Python because Python is bad.”  
It is:

**use Sounio as the scientific kernel, F# as the typed research operating layer, and Stan as the probabilistic baseline lane.**

## Why This Stack

- `Sounio`: owns scientific semantics, manifests, kernels, provenance-sensitive execution, and benchmark-backed claims.
- `F#`: owns the control plane because it gives us strong domain modeling, units of measure, ASP.NET Core integration, and lower glue burden than Python for the platform layer.
- `Stan`: remains the narrow but credible probabilistic comparison lane for calibration and uncertainty.

## Role-Based Comparison

### Against Python

- Python still wins on ecosystem breadth.
- This repo already demonstrates the downside: FastAPI, Pydantic, worker orchestration, storage, agentic flows, and K8s glue are spread across a large dynamic system.
- The rewrite thesis is that `F# + Sounio` should reduce semantic drift and orchestration glue while keeping the benchmark-first core intact.

### Against Rust

- Rust remains the strongest comparison point on systems burden and K8s edge code.
- It is not the chosen primary rewrite because the goal is a typed orchestration language with more direct scientific expressiveness and lower ceremony.

### Against Julia / Turing / Gen

- Julia remains an important scientific reference lane and a valid future comparison for probabilistic and scientific expressiveness.
- It is not the chosen primary control-plane language for this rewrite.

### Against Stan

- Stan is the strongest narrow probabilistic baseline in this stack.
- It is not a full platform language and should stay scoped to calibration, uncertainty, and posterior checks.

## Claim Tags

Every rewrite claim should be marked as one of:

- `implemented`
- `experimental`
- `aspirational`

This prevents the rewrite from turning into architectural marketing.
