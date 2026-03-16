# Top 5 Apply-Time Risks

1. Placeholder control-plane identity leaks into apply.
   If the real VIP, DNS, or TLS SANs are not substituted first, K3s and ingress endpoints may come up with unusable names and require cleanup.

2. External Ceph reachability is incomplete from one or more node planes.
   If workspace or service nodes cannot reach all Ceph monitors, PVCs may bind inconsistently or mount failures may only appear after workload scheduling.

3. Secret format mismatches across charts and deployments.
   CloudNativePG, Coder, Temporal, and `ceph-csi` all have specific secret expectations; a wrong key name or URL shape can make apply succeed while workloads fail at startup.

4. Registry pull path is not actually reachable from cluster nodes.
   The workspace image and worker image may be correctly named in manifests but still fail at runtime because nodes cannot resolve, trust, or authenticate to the registry.

5. Wildcard workspace routing is not truly functional.
   Coder may install cleanly while reattachment still fails if `*.coder.lab.internal` does not resolve and terminate TLS correctly through ingress.
