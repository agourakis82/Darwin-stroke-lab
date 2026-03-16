# Apply Slice 01: Ceph CSI

This is the first live storage slice for M1.

Scope:

- external Proxmox/Ceph remains the storage authority
- no in-cluster Ceph is introduced
- no other service slice is included here
- do not continue to PostgreSQL until this slice passes validation

## Prerequisites

- K3s is reachable with cluster-admin access
- [`PRE_APPLY_CHECKLIST.md`](/Users/demetriosagourakis/Documents/New project/infra/m1/PRE_APPLY_CHECKLIST.md) is green for slice 01 inputs
- namespace `storage-system` exists from [`bootstrap/namespaces.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/bootstrap/namespaces.yaml)
- CephX identities already exist on the external Ceph cluster:
  - `client.csi-cephfs`
  - `client.csi-rbd`
- the CephFS subvolume group `csi` exists or is ready to be created before CSI use
- [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml) has real keys

## Abort conditions

Abort immediately and do not apply anything if any one is true:

- [`PRE_FLIGHT_SLICE_01.sh`](/Users/demetriosagourakis/Documents/New project/infra/m1/PRE_FLIGHT_SLICE_01.sh) does not exit `0`
- `kubectl` does not point at the intended M1 cluster
- `storage-system` namespace is missing
- `client.csi-cephfs` or `client.csi-rbd` is not available on the external Ceph cluster
- the CephFS subvolume group `csi` is not ready
- any `REPLACE_WITH_` marker remains in [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml)
- any Ceph key field in [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml) is empty
- the frozen Ceph FSID, monitors, filesystem, pool, or secret names no longer match across the manifests
- `proxmox-cephfs` or `proxmox-ceph-rbd` already exists in the cluster with conflicting configuration and that drift has not been resolved first

## Exact files that must be filled

- [`ceph-csi/secret-template.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/secret-template.yaml)

Files already fixed by audit and freeze:

- [`ceph-csi/ceph-csi-configmap.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/ceph-csi-configmap.yaml)
- [`ceph-csi/cephfs-values.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/cephfs-values.yaml)
- [`ceph-csi/rbd-values.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/rbd-values.yaml)
- [`ceph-csi/storageclasses.yaml`](/Users/demetriosagourakis/Documents/New project/infra/m1/ceph-csi/storageclasses.yaml)

## Pre-apply checks

Run these and stop on the first failure:

```bash
kubectl get ns storage-system
kubectl get nodes
kubectl get pods -A
! rg -n 'REPLACE_WITH_' infra/m1/ceph-csi/secret-template.yaml
```

Expected binary result:

- the namespace exists
- nodes are reachable
- the placeholder check returns no matches

## Exact apply order

1. Apply the Ceph connection config and secrets.

```bash
kubectl apply -f infra/m1/ceph-csi/ceph-csi-configmap.yaml
kubectl apply -f infra/m1/ceph-csi/secret-template.yaml
```

2. Install the CephFS CSI driver.

```bash
helm repo add ceph-csi https://ceph.github.io/csi-charts
helm repo update
helm upgrade --install ceph-csi-cephfs ceph-csi/ceph-csi-cephfs \
  --namespace storage-system \
  -f infra/m1/ceph-csi/cephfs-values.yaml
```

3. Install the RBD CSI driver.

```bash
helm upgrade --install ceph-csi-rbd ceph-csi/ceph-csi-rbd \
  --namespace storage-system \
  -f infra/m1/ceph-csi/rbd-values.yaml
```

4. Apply the storage classes.

```bash
kubectl apply -f infra/m1/ceph-csi/storageclasses.yaml
```

## Exact validation steps

1. Wait for CSI pods.

```bash
kubectl wait --for=condition=Ready pod -n storage-system --all --timeout=300s
kubectl get pods -n storage-system -o wide
kubectl get sc proxmox-cephfs proxmox-ceph-rbd
```

2. Create a CephFS smoke PVC and pod.

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: m1-cephfs-smoke
  namespace: workspace-plane
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: proxmox-cephfs
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: m1-cephfs-smoke
  namespace: workspace-plane
spec:
  restartPolicy: Never
  containers:
    - name: smoke
      image: busybox:1.36
      command: ["sh", "-lc", "echo cephfs-ok > /workspace/ok && sleep 30"]
      volumeMounts:
        - name: workspace
          mountPath: /workspace
  volumes:
    - name: workspace
      persistentVolumeClaim:
        claimName: m1-cephfs-smoke
EOF

kubectl wait --for=jsonpath='{.status.phase}'=Bound pvc/m1-cephfs-smoke -n workspace-plane --timeout=180s
kubectl wait --for=condition=Ready pod/m1-cephfs-smoke -n workspace-plane --timeout=180s
kubectl exec -n workspace-plane m1-cephfs-smoke -- cat /workspace/ok
```

3. Create an RBD smoke PVC and pod.

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: m1-rbd-smoke
  namespace: lab-system
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: proxmox-ceph-rbd
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: m1-rbd-smoke
  namespace: lab-system
spec:
  restartPolicy: Never
  containers:
    - name: smoke
      image: busybox:1.36
      command: ["sh", "-lc", "echo rbd-ok > /data/ok && sleep 30"]
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: m1-rbd-smoke
EOF

kubectl wait --for=jsonpath='{.status.phase}'=Bound pvc/m1-rbd-smoke -n lab-system --timeout=180s
kubectl wait --for=condition=Ready pod/m1-rbd-smoke -n lab-system --timeout=180s
kubectl exec -n lab-system m1-rbd-smoke -- cat /data/ok
```

4. Clean up the smoke objects after success.

```bash
kubectl delete pod m1-cephfs-smoke -n workspace-plane --ignore-not-found
kubectl delete pvc m1-cephfs-smoke -n workspace-plane --ignore-not-found
kubectl delete pod m1-rbd-smoke -n lab-system --ignore-not-found
kubectl delete pvc m1-rbd-smoke -n lab-system --ignore-not-found
```

## Binary validation

Success:

- all pods in `storage-system` are `Ready`
- both storage classes exist
- both smoke PVCs become `Bound`
- both smoke pods become `Ready`
- both smoke files are readable from the mounted volumes

Failure:

- any CSI pod is not `Ready`
- either storage class is missing
- either smoke PVC does not bind
- either smoke pod does not become `Ready`
- either file check fails

## Rollback

Only use this rollback if:

- no real workloads have been created on these storage classes
- only the smoke objects were used

Rollback order:

```bash
kubectl delete pod m1-cephfs-smoke -n workspace-plane --ignore-not-found
kubectl delete pvc m1-cephfs-smoke -n workspace-plane --ignore-not-found
kubectl delete pod m1-rbd-smoke -n lab-system --ignore-not-found
kubectl delete pvc m1-rbd-smoke -n lab-system --ignore-not-found

kubectl delete -f infra/m1/ceph-csi/storageclasses.yaml --ignore-not-found
helm uninstall ceph-csi-rbd -n storage-system || true
helm uninstall ceph-csi-cephfs -n storage-system || true
kubectl delete -f infra/m1/ceph-csi/secret-template.yaml --ignore-not-found
kubectl delete -f infra/m1/ceph-csi/ceph-csi-configmap.yaml --ignore-not-found
```

Stop gate after rollback:

- do not move to slice 02 until slice 01 is re-run and passes cleanly
