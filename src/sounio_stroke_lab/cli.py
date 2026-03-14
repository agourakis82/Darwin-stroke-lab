from __future__ import annotations

import argparse
import json
from pathlib import Path

from sounio_stroke_lab.cluster_ops import (
    build_cutover_plan,
    render_dataset_copy_job_manifest,
    run_cluster_preflight,
)
from sounio_stroke_lab.job_backend import BenchmarkWorker, KubernetesJobBackend
from sounio_stroke_lab.config import get_job_backend_name
from sounio_stroke_lab.mcp_server import main as mcp_server_main
from sounio_stroke_lab.dataset_manifest import (
    build_aisd_manifest,
    build_dicom_cohort_manifest,
    build_index_manifest,
    build_isles24_manifest,
    validate_benchmark_manifest,
)
from sounio_stroke_lab.schemas import AgentRunRequest, BenchmarkRequest
from sounio_stroke_lab.service import StrokeResearchService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sounio-stroke-lab", description="Benchmark tooling for Sounio Stroke Lab.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    isles = subparsers.add_parser("build-isles24-manifest", help="Build a benchmark manifest from an ISLES24 dataset root.")
    isles.add_argument("--dataset-root", required=True)
    isles.add_argument("--output", required=True)

    dicom = subparsers.add_parser("build-dicom-cohort-manifest", help="Build a manifest from a local DICOM cohort directory.")
    dicom.add_argument("--dataset-root", required=True)
    dicom.add_argument("--output", required=True)
    dicom.add_argument("--dataset-name", default="local-ncct-dicom")
    dicom.add_argument("--dataset-version", default="local-dicom-manifest-v1")
    dicom.add_argument("--source", default="local filesystem")

    aisd = subparsers.add_parser("build-aisd-manifest", help="Build a manifest from an AISD image/mask export.")
    aisd.add_argument("--image-root", required=True)
    aisd.add_argument("--mask-root", required=True)
    aisd.add_argument("--output", required=True)
    aisd.add_argument("--dataset-name", default="AISD")
    aisd.add_argument("--dataset-version", default="aisd-manifest-v1")
    aisd.add_argument("--source", default="https://github.com/GriffinLiang/AISD")

    index = subparsers.add_parser("build-index-manifest", help="Build a manifest from a CSV or JSONL index file.")
    index.add_argument("--index", required=True)
    index.add_argument("--output", required=True)
    index.add_argument("--dataset-name", required=True)
    index.add_argument("--dataset-version", required=True)
    index.add_argument("--source", default="local index")

    validate = subparsers.add_parser("validate-manifest", help="Validate manifest structure and file existence.")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--required-split", action="append", dest="required_splits")

    benchmark = subparsers.add_parser("run-benchmark", help="Run the benchmark suite from a manifest.")
    benchmark.add_argument("--manifest", required=True)
    benchmark.add_argument("--external-test-manifest")
    benchmark.add_argument("--storage-root")
    benchmark.add_argument("--train-split", default="train")
    benchmark.add_argument("--test-split", default="test")
    benchmark.add_argument("--seed", type=int, default=13)

    worker = subparsers.add_parser("run-benchmark-worker", help="Run the local benchmark worker loop.")
    worker.add_argument("--storage-root")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--job-id")
    worker.add_argument("--poll-interval", type=float, default=0.1)

    render = subparsers.add_parser("render-benchmark-job", help="Render a Kubernetes-shaped benchmark job spec for a persisted job.")
    render.add_argument("--job-id", required=True)
    render.add_argument("--storage-root")

    preflight = subparsers.add_parser("k8s-preflight", help="Run Kubernetes/Ceph readiness checks for the cluster-backed deployment.")
    preflight.add_argument("--namespace", default="darwin-genomics")
    preflight.add_argument("--pvc-name", default="sounio-stroke-datasets")
    preflight.add_argument("--target-storage-class", required=True)
    preflight.add_argument("--overlay-mode", default="cephfs", choices=["cephfs", "ceph-rbd", "rbd"])
    preflight.add_argument("--json", action="store_true")

    cutover = subparsers.add_parser("k8s-cutover-plan", help="Print the recommended Ceph cutover plan from the current cluster state.")
    cutover.add_argument("--namespace", default="darwin-genomics")
    cutover.add_argument("--pvc-name", default="sounio-stroke-datasets")
    cutover.add_argument("--target-storage-class", required=True)
    cutover.add_argument("--overlay-mode", default="cephfs", choices=["cephfs", "ceph-rbd", "rbd"])
    cutover.add_argument("--target-pvc-name")
    cutover.add_argument("--discard-existing-data", action="store_true")
    cutover.add_argument("--json", action="store_true")

    copy_job = subparsers.add_parser("render-dataset-copy-job", help="Render a Kubernetes Job that copies datasets between two PVCs.")
    copy_job.add_argument("--namespace", default="darwin-genomics")
    copy_job.add_argument("--source-pvc", required=True)
    copy_job.add_argument("--target-pvc", required=True)
    copy_job.add_argument("--job-name", default="sounio-stroke-dataset-copy")
    copy_job.add_argument("--image", default="alpine:3.21")

    agent = subparsers.add_parser("run-agent", help="Queue an agentic lab run.")
    agent.add_argument("--objective", required=True)
    agent.add_argument("--surface", default="dual", choices=["research", "clinical", "dual"])
    agent.add_argument("--manifest")
    agent.add_argument("--external-test-manifest")
    agent.add_argument("--study-id")
    agent.add_argument("--study-file", action="append", dest="study_files")
    agent.add_argument("--storage-root")
    agent.add_argument("--seed", type=int, default=13)

    agent_worker = subparsers.add_parser("run-agent-worker", help="Execute one persisted agent run.")
    agent_worker.add_argument("--run-id", required=True)
    agent_worker.add_argument("--job-id")
    agent_worker.add_argument("--storage-root")

    mcp = subparsers.add_parser("run-mcp-server", help="Run one local MCP server over stdio.")
    mcp.add_argument("--name", required=True)
    mcp.add_argument("--storage-root")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "build-isles24-manifest":
        output = build_isles24_manifest(Path(args.dataset_root).expanduser().resolve(), Path(args.output).expanduser().resolve())
        print(output)
        return 0

    if args.command == "build-dicom-cohort-manifest":
        output = build_dicom_cohort_manifest(
            Path(args.dataset_root).expanduser().resolve(),
            Path(args.output).expanduser().resolve(),
            dataset_name=args.dataset_name,
            dataset_version=args.dataset_version,
            source=args.source,
        )
        print(output)
        return 0

    if args.command == "build-aisd-manifest":
        output = build_aisd_manifest(
            image_root=Path(args.image_root).expanduser().resolve(),
            mask_root=Path(args.mask_root).expanduser().resolve(),
            output_path=Path(args.output).expanduser().resolve(),
            dataset_name=args.dataset_name,
            dataset_version=args.dataset_version,
            source=args.source,
        )
        print(output)
        return 0

    if args.command == "build-index-manifest":
        output = build_index_manifest(
            Path(args.index).expanduser().resolve(),
            Path(args.output).expanduser().resolve(),
            dataset_name=args.dataset_name,
            dataset_version=args.dataset_version,
            source=args.source,
        )
        print(output)
        return 0

    if args.command == "validate-manifest":
        manifest_path = Path(args.manifest).expanduser().resolve()
        manifest, issues = validate_benchmark_manifest(
            manifest_path,
            required_splits=set(args.required_splits) if args.required_splits else None,
        )
        payload = {
            "dataset_name": manifest.dataset_name,
            "dataset_version": manifest.dataset_version,
            "case_count": len(manifest.cases),
            "issues": issues,
            "valid": not issues,
        }
        print(json.dumps(payload, indent=2))
        return 0 if not issues else 1

    if args.command == "run-benchmark":
        storage_root = Path(args.storage_root).expanduser().resolve() if args.storage_root else None
        service = StrokeResearchService(
            storage_root=storage_root,
            start_benchmark_worker=(get_job_backend_name() == "local"),
        )
        try:
            run = service.run_benchmark(
                BenchmarkRequest(
                    dataset_manifest_path=str(Path(args.manifest).expanduser().resolve()),
                    external_test_manifest_path=(
                        str(Path(args.external_test_manifest).expanduser().resolve())
                        if args.external_test_manifest
                        else None
                    ),
                    train_split=args.train_split,
                    test_split=args.test_split,
                    seed=args.seed,
                )
            )
            print(json.dumps(run.model_dump(mode="json"), indent=2))
            return 0
        finally:
            service.shutdown()

    if args.command == "run-benchmark-worker":
        storage_root = Path(args.storage_root).expanduser().resolve() if args.storage_root else None
        service = StrokeResearchService(storage_root=storage_root, start_benchmark_worker=False)
        worker = BenchmarkWorker(
            service.storage,
            service.benchmark,
            poll_interval=args.poll_interval,
            backend_name=get_job_backend_name(),
        )
        try:
            if args.job_id:
                worker.run_job(args.job_id)
                return 0
            if args.once:
                processed = worker.run_once()
                return 0 if processed else 1
            worker.run_forever()
            return 0
        finally:
            service.shutdown()

    if args.command == "render-benchmark-job":
        storage_root = Path(args.storage_root).expanduser().resolve() if args.storage_root else None
        service = StrokeResearchService(storage_root=storage_root, start_benchmark_worker=False)
        try:
            backend = KubernetesJobBackend(service.storage)
            print(json.dumps(backend.render_job_manifest(args.job_id), indent=2))
            return 0
        finally:
            service.shutdown()

    if args.command == "k8s-preflight":
        result = run_cluster_preflight(
            namespace=args.namespace,
            pvc_name=args.pvc_name,
            target_storage_class=args.target_storage_class,
            overlay_mode=args.overlay_mode,
        )
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"overlay: {result.overlay}")
            print(f"ready_for_cutover: {result.ready_for_cutover}")
            print(
                "available_storage_classes: "
                + (", ".join(result.available_storage_classes) if result.available_storage_classes else "<none>")
            )
            for check in result.checks:
                status = "PASS" if check.ok else "FAIL"
                suffix = f" ({check.value})" if check.value else ""
                print(f"- [{status}] {check.name}{suffix}: {check.details}")
            if result.warnings:
                print("warnings:")
                for item in result.warnings:
                    print(f"  - {item}")
        return 0 if result.ready_for_cutover else 1

    if args.command == "k8s-cutover-plan":
        result = run_cluster_preflight(
            namespace=args.namespace,
            pvc_name=args.pvc_name,
            target_storage_class=args.target_storage_class,
            overlay_mode=args.overlay_mode,
        )
        plan = build_cutover_plan(
            result,
            preserve_existing_data=not args.discard_existing_data,
            target_pvc_name=args.target_pvc_name,
        )
        payload = {
            "preflight": result.to_dict(),
            "plan": plan,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"overlay: {result.overlay}")
            print(f"ready_for_cutover: {result.ready_for_cutover}")
            print("plan:")
            for index, step in enumerate(plan, start=1):
                print(f"  {index}. {step}")
        return 0 if result.ready_for_cutover else 1

    if args.command == "render-dataset-copy-job":
        manifest = render_dataset_copy_job_manifest(
            namespace=args.namespace,
            source_pvc=args.source_pvc,
            target_pvc=args.target_pvc,
            job_name=args.job_name,
            image=args.image,
        )
        print(json.dumps(manifest, indent=2))
        return 0

    if args.command == "run-agent":
        storage_root = Path(args.storage_root).expanduser().resolve() if args.storage_root else None
        service = StrokeResearchService(
            storage_root=storage_root,
            start_benchmark_worker=(get_job_backend_name() == "local"),
        )
        try:
            run = service.create_agent_run(
                AgentRunRequest(
                    objective=args.objective,
                    surface=args.surface,
                    dataset_manifest_path=(
                        str(Path(args.manifest).expanduser().resolve()) if args.manifest else None
                    ),
                    external_test_manifest_path=(
                        str(Path(args.external_test_manifest).expanduser().resolve())
                        if args.external_test_manifest
                        else None
                    ),
                    study_id=args.study_id,
                    study_file_paths=[
                        str(Path(item).expanduser().resolve()) for item in (args.study_files or [])
                    ],
                    seed=args.seed,
                )
            )
            print(json.dumps(run.model_dump(mode="json"), indent=2))
            return 0
        finally:
            service.shutdown()

    if args.command == "run-agent-worker":
        storage_root = Path(args.storage_root).expanduser().resolve() if args.storage_root else None
        service = StrokeResearchService(
            storage_root=storage_root,
            start_benchmark_worker=(get_job_backend_name() == "local"),
        )
        try:
            if service.platform_core.agent_worker is None:
                from sounio_stroke_lab.agentic import AgentRunWorker

                worker = AgentRunWorker(service)
            else:
                worker = service.platform_core.agent_worker
            worker.run_run(args.run_id, job_id=args.job_id)
            return 0
        finally:
            service.shutdown()

    if args.command == "run-mcp-server":
        mcp_argv = ["--name", args.name]
        if args.storage_root:
            mcp_argv.extend(["--storage-root", args.storage_root])
        mcp_server_main(mcp_argv)
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
