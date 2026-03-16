# Source-of-Truth Matrix

| Concern | M0.5 source of truth | M1 source of truth | What is derived |
|---|---|---|---|
| Temporal workflow state | in-process `RunEngine` execution state | Temporal workflow history and activity state | any UI-facing progress summary |
| NATS JetStream events | SQLite-backed `run_events` table as a local stand-in | JetStream stream `RUN_EVENTS` with ordered subjects `runs.*` | replay views and event tails |
| PostgreSQL metadata | SQLite-backed `runs`, `run_steps`, and `run_artifacts` tables as a local stand-in | CloudNativePG-backed PostgreSQL databases `coder`, `temporal`, and `lab_runs` | API projections and operator dashboards |
| CephFS workspace and artifact files | local filesystem under `.lab/runs/<run_id>/` | external Proxmox/Ceph mounted through `ceph-csi` as CephFS and RBD | artifact indexes and preview snippets |
| ResumeSummary generation | `StrokeResearchService.get_resume_summary()` over SQLite + files | resume API/read model over PostgreSQL + JetStream + CephFS | `ResumeSummary` itself is derived and never authoritative |

## Authority rules

- CephFS/RBD authority belongs to external Proxmox/Ceph, not Kubernetes.
- Temporal is authoritative for orchestration progress, not for artifact bytes.
- JetStream is authoritative for ordered event replay, not for final metadata.
- PostgreSQL is authoritative for run metadata and step/artifact indexes.
- `ResumeSummary` is a derived convenience surface and may always be regenerated from the sources above.
