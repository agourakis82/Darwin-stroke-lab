# Apply Slice 02: PostgreSQL

This is the second live slice for M1.

Hard gate:

- do not start this slice until [`APPLY_SLICE_01_CEPH_CSI.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/APPLY_SLICE_01_CEPH_CSI.md) has passed fully

Scope:

- CloudNativePG only
- no new service expansion
- still no Temporal external UI
- still no Slurm integration

## Prerequisites

- slice 01 passed and both storage classes are working
- namespace `postgres` exists from [`bootstrap/namespaces.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/namespaces.yaml)
- [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml) has real passwords
- `proxmox-ceph-rbd` exists and has already passed the smoke test

## Exact files that must be filled

- [`postgres/bootstrap-secrets.template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/bootstrap-secrets.template.yaml)

Files already fixed:

- [`postgres/operator.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/operator.yaml)
- [`postgres/cluster.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/cluster.yaml)
- [`postgres/databases.sql`](/Users/demetriosagourakis/Documents/New project/infra/m1/postgres/databases.sql)

## Pre-apply checks

Run these and stop on the first failure:

```bash
kubectl get sc proxmox-ceph-rbd
kubectl get pods -n storage-system
! rg -n 'REPLACE_WITH_' infra/m1/postgres/bootstrap-secrets.template.yaml
```

Expected binary result:

- `proxmox-ceph-rbd` exists
- slice 01 storage pods are healthy
- the placeholder check returns no matches

## Exact apply order

1. Apply the CloudNativePG operator chart.

```bash
kubectl apply -f infra/m1/postgres/operator.yaml
until [ "$(kubectl get deployment -n postgres -o name 2>/dev/null | wc -l | tr -d ' ')" -gt 0 ]; do sleep 5; done
kubectl wait --for=condition=Available deployment --all -n postgres --timeout=300s
```

2. Apply the PostgreSQL bootstrap secrets.

```bash
kubectl apply -f infra/m1/postgres/bootstrap-secrets.template.yaml
```

3. Apply the PostgreSQL cluster.

```bash
kubectl apply -f infra/m1/postgres/cluster.yaml
until [ "$(kubectl get pod -n postgres -l cnpg.io/cluster=lab-postgres -o name 2>/dev/null | wc -l | tr -d ' ')" -gt 0 ]; do sleep 5; done
kubectl wait --for=condition=Ready pod -n postgres -l cnpg.io/cluster=lab-postgres --timeout=900s
kubectl get pods -n postgres -l cnpg.io/cluster=lab-postgres -o wide
kubectl get pvc -n postgres -l cnpg.io/cluster=lab-postgres
```

4. Create the application databases after the cluster is healthy.

```bash
PRIMARY_POD="$(kubectl get pods -n postgres -l cnpg.io/cluster=lab-postgres,cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')"
kubectl exec -i -n postgres "$PRIMARY_POD" -- psql -v ON_ERROR_STOP=1 -U postgres -d postgres < infra/m1/postgres/databases.sql
```

## Exact validation steps

1. Validate cluster objects.

```bash
kubectl get cluster -n postgres lab-postgres
kubectl get svc -n postgres lab-postgres-rw
kubectl get pods -n postgres -l cnpg.io/cluster=lab-postgres
kubectl get pvc -n postgres -l cnpg.io/cluster=lab-postgres
```

2. Validate database presence.

```bash
PRIMARY_POD="$(kubectl get pods -n postgres -l cnpg.io/cluster=lab-postgres,cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')"
kubectl exec -n postgres "$PRIMARY_POD" -- psql -At -U postgres -d postgres -c "SELECT datname FROM pg_database WHERE datname IN ('coder','temporal','temporal_visibility','lab_runs') ORDER BY 1;"
```

Expected result:

- output contains exactly:
  - `coder`
  - `lab_runs`
  - `temporal`
  - `temporal_visibility`

3. Validate the RW endpoint.

```bash
kubectl exec -n postgres "$PRIMARY_POD" -- psql -At -U postgres -h lab-postgres-rw.postgres.svc.cluster.local -d postgres -c "SELECT 1;"
```

Expected result:

- output is `1`

## Binary validation

Success:

- the operator deployment is `Available`
- all `lab-postgres` pods are `Ready`
- all PostgreSQL PVCs are `Bound`
- `lab-postgres-rw` exists
- the four application databases exist
- the RW service returns `1` for the smoke query

Failure:

- operator deployment is not `Available`
- any `lab-postgres` pod is not `Ready`
- any PostgreSQL PVC is not `Bound`
- `lab-postgres-rw` does not exist
- any application database is missing
- the RW smoke query fails

## Rollback

Only use this rollback if:

- no downstream slice has started
- no non-test data must be preserved

Rollback order:

```bash
kubectl delete -f infra/m1/postgres/cluster.yaml --ignore-not-found
kubectl delete -f infra/m1/postgres/bootstrap-secrets.template.yaml --ignore-not-found
kubectl delete -f infra/m1/postgres/operator.yaml --ignore-not-found
```

Important caution:

- do not delete PostgreSQL PVCs unless you explicitly want a fresh bootstrap and have confirmed there is no data to keep

Stop gate after rollback:

- do not continue to `nats` until slice 02 is re-run and passes cleanly
