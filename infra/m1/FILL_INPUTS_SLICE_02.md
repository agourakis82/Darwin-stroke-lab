# Fill Inputs: Slice 02

Do not use this helper until slice 01 has passed.

## Values to enter

| File | Field | Enter this exact value |
|---|---|---|
| [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml) | `password` under `lab-postgres-app` | bootstrap owner password for `app` |
| [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml) | `url` and `password` under `coder-db-url` | password for role `coder` |
| [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml) | `url`, `visibility_url`, and `password` under `temporal-db-url` | password for role `temporal` |
| [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml) | `url` and `password` under `lab-runs-db-url` | password for role `lab_runs` |

Do not change these frozen values:

- secret names
- host `lab-postgres-rw.postgres.svc.cluster.local`
- port `5432`
- database names `coder`, `temporal`, `temporal_visibility`, `lab_runs`
- storage class `proxmox-ceph-rbd`

## Validate the edits

Run:

```bash
./infra/m1/PRE_FLIGHT_SLICE_02.sh
```

Binary pass criteria:

- script exits `0`
- slice 01 success is visible by read-only cluster checks
- no `REPLACE_WITH_` markers remain in [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml)
- every required password field is non-empty
- each URL embeds the same password as its paired `password:` field
- manifest references are internally consistent
