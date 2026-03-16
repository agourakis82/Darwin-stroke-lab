# Post-v0.2.0 Notes: M1 Infrastructure Bootstrap

## Context

- Release [`v0.2.0`](https://github.com/agourakis82/Darwin-stroke-lab/releases/tag/v0.2.0) tagged the local durable run engine, `labctl`, and the `/runs/*` HTTP surface.
- Commit `bb79cfe` landed immediately after that tag and adds the first versioned `M1` infrastructure tree under `infra/m1/`.
- These notes document that follow-up infrastructure cut without retroactively redefining the `v0.2.0` package release.

## What Landed In `bb79cfe`

The `M1` tree now contains the bootstrap and contract material for the distributed, resume-capable lab runtime:

- architecture and sequencing docs
- source-of-truth and operator worksheets
- first live slice and acceptance drill
- external Ceph + `ceph-csi` bootstrap
- CloudNativePG bootstrap and database layout
- NATS JetStream bootstrap
- Temporal bootstrap and worker manifests
- Coder bootstrap and workspace template
- registry and workspace image scaffolding
- K3s HA reference material
- resume contract and OpenAPI contract snapshots
- preflight scripts for the first two deployment slices

Representative anchors:

- [`ARCHITECTURE.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/ARCHITECTURE.md)
- [`FIRST_LIVE_SLICE.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/FIRST_LIVE_SLICE.md)
- [`contracts/openapi.yaml`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/contracts/openapi.yaml)
- [`coder/template-sounio/main.tf`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/coder/template-sounio/main.tf)
- [`PRE_FLIGHT_SLICE_01.sh`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/PRE_FLIGHT_SLICE_01.sh)
- [`PRE_FLIGHT_SLICE_02.sh`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/PRE_FLIGHT_SLICE_02.sh)

## What Was Explicitly Excluded

This commit intentionally does **not** version local audit exhaust or machine-specific residue:

- `infra/m1/audit/`
- `.DS_Store`
- `*.orig`

Template placeholders remain placeholders by design:

- `REPLACE_WITH_*`
- `.template.yaml`
- `.input.example.yaml`

No live secrets were committed as part of this cut.

## Validation Performed

Focused validation for this infrastructure cut was limited to repository integrity and script sanity:

- `bash -n infra/m1/PRE_FLIGHT_SLICE_01.sh`
- `bash -n infra/m1/PRE_FLIGHT_SLICE_02.sh`

Both passed.

This commit does **not** claim a full live deployment of the M1 stack by itself. It provides the versioned bootstrap material needed for that work.

## Recommended Next Moves

1. Run the preflight scripts against the intended target environment and record outputs outside the source tree.
2. Freeze the remaining environment-specific inputs referenced in:
   - [`FILL_INPUTS_SLICE_01.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/FILL_INPUTS_SLICE_01.md)
   - [`FILL_INPUTS_SLICE_02.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/FILL_INPUTS_SLICE_02.md)
3. Execute the first live slice in the order described by:
   - [`FIRST_LIVE_EXECUTION_SEQUENCE.md`](/Users/demetriosagourakis/Documents/New%20project/infra/m1/FIRST_LIVE_EXECUTION_SEQUENCE.md)
4. Capture live apply evidence outside the repo and promote only sanitized artifacts back into version control when they become reusable.

## Release Position

- `v0.2.0`: local durable run engine release
- `bb79cfe`: post-release infrastructure bootstrap and contracts

That split is intentional. It keeps the semver package release clean while still versioning the much larger M1 infrastructure substrate immediately afterward.
