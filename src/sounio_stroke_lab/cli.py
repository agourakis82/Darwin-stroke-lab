from __future__ import annotations

import argparse
import json
from pathlib import Path

from sounio_stroke_lab.dataset_manifest import (
    build_aisd_manifest,
    build_dicom_cohort_manifest,
    build_index_manifest,
    build_isles24_manifest,
    validate_benchmark_manifest,
)
from sounio_stroke_lab.schemas import BenchmarkRequest, RunSubmitRequest
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
    benchmark.add_argument("--target-shape", nargs=3, type=int, metavar=("Z", "Y", "X"))
    benchmark.add_argument("--segmentation-threshold", type=float)

    run_submit = subparsers.add_parser("run-submit", help="Submit a generic agent run to the local reference backend.")
    run_submit.add_argument("--storage-root")
    run_submit.add_argument("--workspace-id")
    run_submit.add_argument("--user-id", default="workspace-user")
    run_submit.add_argument("--workspace-path", required=True)
    run_submit.add_argument("--repo-path", required=True)
    run_submit.add_argument("--task-name", default="inventory-workspace")
    run_submit.add_argument("--parameter", action="append", dest="parameters")

    run_resume = subparsers.add_parser("run-resume", help="Fetch the latest resume summary for a local run.")
    run_resume.add_argument("run_id")
    run_resume.add_argument("--storage-root")

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
        service = StrokeResearchService(storage_root=storage_root)
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
                target_shape=tuple(args.target_shape) if args.target_shape else None,
                segmentation_threshold=args.segmentation_threshold,
            )
        )
        print(json.dumps(run.model_dump(mode="json"), indent=2))
        return 0

    if args.command == "run-submit":
        storage_root = Path(args.storage_root).expanduser().resolve() if args.storage_root else None
        service = StrokeResearchService(storage_root=storage_root)
        parameters = {}
        for item in args.parameters or []:
            key, _, value = item.partition("=")
            if not key:
                parser.error(f"Invalid --parameter value: {item}")
            parameters[key] = value if value else "true"
        run = service.submit_run(
            RunSubmitRequest(
                workspace_id=args.workspace_id,
                user_id=args.user_id,
                workspace_path=str(Path(args.workspace_path).expanduser().resolve()),
                repo_path=str(Path(args.repo_path).expanduser().resolve()),
                task_name=args.task_name,
                parameters=parameters,
            )
        )
        print(json.dumps(run.model_dump(mode="json"), indent=2))
        return 0

    if args.command == "run-resume":
        storage_root = Path(args.storage_root).expanduser().resolve() if args.storage_root else None
        service = StrokeResearchService(storage_root=storage_root)
        summary = service.get_resume_summary(args.run_id)
        print(json.dumps(summary.model_dump(mode="json"), indent=2))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
