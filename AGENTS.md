# AGENTS.md

## Identity

This repository is the **seed of a larger Darwin Research OS**, currently
implemented under the package name `sounio_stroke_lab` because stroke was the
first concrete lab.

The package name is historical. The architectural intent is larger:

- a **platform layer** for scientific agents, benchmarks, artifacts, tracing,
  MCP, and job orchestration
- a **domain layer** for applied labs
- a **Sounio kernel layer** for scientific semantics and domain-specific
  computation

Today, **stroke is the flagship lab**, not the final boundary of the project.

## Essence To Preserve

If future work loses any of the following, it has drifted off course:

1. **Sounio is the scientific kernel**
   - This repo should integrate and operationalize `Sounio`.
   - It should not try to replace the `sounio` language repository.
   - Claims about `Sounio` must be benchmark-backed and clearly scoped.

2. **Benchmark-first over demo-first**
   - Scientific claims must be tied to manifests, runs, metrics, and artifacts.
   - Synthetic fixtures are acceptable for testing infrastructure, not for the
     main scientific claim.
   - The correct sequence is:
     `manifest -> run -> artifact -> metric -> report -> claim`.

3. **Platform-first, lab-second**
   - Reusable infrastructure belongs in the platform layer.
   - Stroke-specific logic belongs in the stroke layer.
   - New domains should reuse the platform instead of cloning ad hoc logic.

4. **Cluster/HPC-first for heavy compute**
   - Local mode is for development and reproducibility.
   - Dev-local mode should still look like a control plane: API, queue/state,
     worker, and artifact store should stay separable even before real K8s.
   - Real benchmarks, ablations, and large runs should be designed to move to
     K8s/HPC cleanly.
   - Agent workflows should evolve toward job submission and artifact harvest,
     not synchronous in-process compute.
   - The current local benchmark worker is a transitional backend, not the
     final execution model.

5. **Agentic orchestration must remain auditable**
   - Tool calls, handoffs, traces, and artifacts must stay inspectable.
   - Public/deep research tools must receive sanitized inputs only.
   - Raw scientific inputs and local file paths stay local unless explicitly
     allowed.

6. **No clinical overclaim**
   - This repo is research infrastructure.
   - Stroke outputs are exploratory and benchmark-oriented, not clinical truth.

## What This Repo Is

This repo currently contains three intertwined layers.

### 1. Platform Core

These modules are part of the emerging Darwin Research OS core:

- `agentic.py`
- `platform_core.py`
- `job_backend.py`
- `state_store.py`
- `object_store.py`
- `mcp_registry.py`
- `mcp_server.py`
- `trace_audit.py`
- `storage.py`
- `schemas.py`
- `service.py`
- `main.py`
- `cli.py`
- `research_corpus.py`
- `live_research.py`

Responsibilities:

- agent runs
- queueing and durable execution
- state persistence and object-backed artifact persistence
- backend selection for local execution now and K8s/HPC later
- MCP surfaces
- tracing and audit
- storage of runs, artifacts, and reports
- API and CLI control plane

### 2. Stroke Lab

These modules are stroke-specific and should stay explicitly domain-bound:

- `stroke_lab.py`
- `atlas.py`
- `preprocessing.py`
- `features.py`
- `hypercomplex.py`
- `runners.py`
- `trainable_models.py`
- `dataset_manifest.py`
- `benchmark.py`
- `evaluation.py`
- `cohort_analysis.py`
- `failure_analysis.py`
- `research_protocol.py`
- `leaderboard.py`
- `reporting.py`
- `scientific_reporting.py`
- `case_exports.py`

Responsibilities:

- DICOM/NIfTI/NCCT handling
- ASPECTS-oriented pipelines
- stroke benchmark logic
- stroke reports and figures

`service.py` currently acts as a compatibility facade across both layers.

### 3. Sounio Integration Layer

These modules define how the platform talks to the upstream language/runtime:

- `sounio_runtime.py`
- the `Sounio` branches in `runners.py`
- related config and runtime checks

Responsibilities:

- locate official `souc`
- validate official runtime assumptions
- execute Sounio kernels
- keep Python as orchestration/shell where needed

## What This Repo Is Not

This repo is **not**:

- the `sounio` compiler repository
- a generic website or product shell disconnected from benchmarks
- a clinical device
- a dumping ground for upstream language marketing work

If a task mainly belongs to the language repo, document the integration surface
here and do the primary implementation there.

## Anti-Drift Rules

Use these rules before making structural changes.

### Rule 1: Prefer extraction of platform concepts, not accidental renaming

Do not rename everything away from stroke unless the replacement is backed by a
real abstraction. Good examples:

- generic `JobRun`
- generic `ArtifactRef`
- generic `AgentRun`

Bad examples:

- renaming `Study` to `Item` without clarifying the domain boundary

### Rule 2: Keep stroke working while broadening the platform

When introducing generic platform concepts:

- preserve current stroke behavior
- add generic contracts around it
- do not break the existing stroke API surface without a migration reason

### Rule 3: Add new domains as labs, not as scattered exceptions

If genomics or another domain is introduced later:

- add a clear domain module/package
- reuse the platform core
- avoid mixing unrelated domain logic into stroke modules

### Rule 4: Do not move the center of gravity into the upstream Sounio repo

This repo should consume `Sounio` as a kernel and runtime dependency.
It should not gradually turn into a branch of the language repository.

### Rule 5: K8s/HPC work belongs here

Cluster execution, job manifests, scheduler integration, run lifecycle, and
artifact collection belong in this repo because they are part of the scientific
platform, not the language implementation.

## Preferred Refactor Direction

If we continue generalizing this repo, do it in this order:

1. strengthen the platform contracts
   - jobs
   - artifacts
   - manifests
   - traces
   - agent runs
2. isolate stroke-specific logic more cleanly
3. add scheduler/K8s/HPC execution
4. add the second lab/domain
5. only then consider a package rename or repo rename

This order preserves momentum and avoids architectural theater.

## Naming Guidance

Short-term:

- keep the Python package name `sounio_stroke_lab`
- treat it as the current implementation vehicle

Medium-term:

- platform naming may evolve toward something like `darwin_lab_core` or
  `darwin_research_os`
- stroke should then become one lab on top of that core

Do not rename the package just to make the vision sound larger. Rename only
when the code structure already supports it.

## Decision Heuristics For Future Agents

When unsure where a change belongs, ask:

1. Does this concern **all future labs**?
   - Put it in the platform layer.

2. Does this concern **stroke imaging and ASPECTS specifically**?
   - Put it in the stroke layer.

3. Does this concern **compiler/runtime behavior of Sounio itself**?
   - Keep the integration surface here, but treat the upstream `sounio` repo as
     the primary home.

4. Does this concern **cluster execution, jobs, or experiment orchestration**?
   - It belongs here, because this repo is the scientific operating layer.

## Current Strategic Direction

The near-term direction of this repo is:

- keep stroke as the first serious applied lab
- evolve the platform into a reusable research OS
- add real scheduler/K8s/HPC execution
- keep `Sounio` as the scientific kernel
- prepare for future labs without erasing the stroke lab

## In One Sentence

**This repo should grow from “Sounio Stroke Lab” into a broader Darwin-style
research operating system, but it must do so by extracting reusable platform
infrastructure around a still-working stroke lab, not by drifting into the
upstream language repo or by erasing the benchmark-first scientific core.**
