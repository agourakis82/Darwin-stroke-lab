# Top 5 Remaining Blockers

1. External Ceph connection material is still placeholder-only.
   We still need real monitor endpoints, fsid, and CSI credentials from the Proxmox/Ceph substrate.

2. Cluster ingress identity is not pinned.
   API VIP, DNS records, TLS issuers, and service hostnames for Coder and Temporal are still examples.

3. Database bootstrap is not yet automated end to end.
   CloudNativePG is scaffolded, but secret delivery and `coder`/`temporal`/`lab_runs` initialization still need cluster bootstrap wiring.

4. The real M1 worker image is not published yet.
   The worker deployment is scaffolded, but it still depends on a published image and actual Temporal queue handlers.

5. Coder still depends on the `t560` seed image pipeline.
   The Podman image is correctly demoted to fallback/template seed only, but the registry publication path for the canonical workspace image still has to be operationalized.
