# Contracts Freeze

This directory is the frozen Python source of truth for the rewrite to `Sounio + F#`.

It contains four kinds of artifacts:

- `openapi/openapi.json`: canonical HTTP contract exported from the current FastAPI app.
- `json_schema/*.schema.json`: canonical JSON schemas for the records that must reach parity in F#.
- `golden/*.json`: example payloads captured from the current running Python system after canonicalization.
- `registry/comparison_registry.json`: role-based language comparison registry used by the SOTA/SOTT dossier.

Regenerate everything with:

```bash
python3 scripts/freeze_contracts.py --output-root contracts
```

These artifacts are intentionally benchmark-first. They freeze the current semantics of:

`job -> benchmark_run -> campaign -> experiment_plan -> plan_report -> program -> portfolio`
