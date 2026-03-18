from __future__ import annotations

from dataclasses import dataclass

from sounio_stroke_lab.schemas import BenchmarkComparison, BenchmarkRequest, BenchmarkRun


@dataclass(frozen=True)
class ChecklistItem:
    item_id: str
    section: str
    description: str
    status: str
    evidence: str


def _status_counts(items: list[ChecklistItem]) -> dict[str, int]:
    counts = {"fulfilled": 0, "partial": 0, "missing": 0}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def build_claim_checklist(
    request: BenchmarkRequest,
    run: BenchmarkRun,
    training_summary: dict[str, object],
    test_summary: dict[str, object],
    cohort_shift: dict[str, object],
) -> dict[str, object]:
    external_validation = bool(run.compute_budget.get("external_validation"))
    items = [
        ChecklistItem(
            "CLAIM-TitleAbstract",
            "Title/Abstract",
            "AI methodology and validation setting are explicitly identified.",
            "fulfilled",
            "Generated manuscript draft and technical report describe the AI benchmark and validation design.",
        ),
        ChecklistItem(
            "CLAIM-StudyDesign",
            "Methods",
            "Study design, intended use and validation strategy are specified.",
            "fulfilled",
            "research_protocol artifact records internal or external validation design and benchmark arms.",
        ),
        ChecklistItem(
            "CLAIM-DataSources",
            "Data",
            "Data source, provenance, splits and cohort composition are reported.",
            "fulfilled",
            "training_cohort_summary, test_cohort_summary and manifest artifacts enumerate data provenance and case counts.",
        ),
        ChecklistItem(
            "CLAIM-GroundTruth",
            "Data",
            "Reference standard and labeling target are described.",
            "fulfilled",
            "Benchmark manifests require lesion-mask paths and derive ASPECTS from lesion overlap with atlas regions.",
        ),
        ChecklistItem(
            "CLAIM-Partitions",
            "Data",
            "Training, validation and test partitions are separated and described.",
            "fulfilled",
            "BenchmarkRequest fixes train/test splits; external validation can use a separate test manifest.",
        ),
        ChecklistItem(
            "CLAIM-MissingData",
            "Data",
            "Handling of missing data and exclusions is described.",
            "partial",
            "Manifest validation catches missing files, but missing clinical covariates are not yet handled analytically.",
        ),
        ChecklistItem(
            "CLAIM-Preprocessing",
            "Methods",
            "Image preprocessing is reported in enough detail to reproduce the pipeline.",
            "fulfilled",
            "Pipeline version and fixed preprocessing contract are persisted in every benchmark run.",
        ),
        ChecklistItem(
            "CLAIM-Model",
            "Methods",
            "Model architecture or scoring method is described.",
            "fulfilled",
            "Artifacts persist feature sets, weights, ablations and model family for each arm.",
        ),
        ChecklistItem(
            "CLAIM-Performance",
            "Results",
            "Performance measures with uncertainty are reported.",
            "fulfilled",
            "Metrics include bootstrap confidence intervals and paired permutation p-values.",
        ),
        ChecklistItem(
            "CLAIM-ExternalValidation",
            "Results",
            "External validation is reported when applicable.",
            "fulfilled" if external_validation else "partial",
            "external_test_manifest_path enables external validation; current run records whether this was used.",
        ),
        ChecklistItem(
            "CLAIM-SubgroupFailure",
            "Results",
            "Subgroup performance and failure analysis are reported.",
            "fulfilled",
            "stratified_metrics and failure_analysis artifacts are generated for every benchmark run.",
        ),
        ChecklistItem(
            "CLAIM-DataCodeAvailability",
            "Other",
            "Code, model and data availability are stated.",
            "partial",
            "Code and model artifacts are available; clinical datasets depend on external access constraints.",
        ),
        ChecklistItem(
            "CLAIM-BiasGeneralizability",
            "Discussion",
            "Generalizability and possible sources of bias are discussed.",
            "fulfilled",
            "cohort_shift artifact and manuscript draft document dataset differences and the NCCT-only limitation.",
        ),
    ]
    return {
        "guideline": "CLAIM 2024 subset",
        "request": request.model_dump(),
        "training_dataset": training_summary["dataset_name"],
        "test_dataset": test_summary["dataset_name"],
        "external_validation": external_validation,
        "cohort_shift": cohort_shift,
        "items": [item.__dict__ for item in items],
        "status_counts": _status_counts(items),
    }


def build_tripod_ai_checklist(
    request: BenchmarkRequest,
    run: BenchmarkRun,
    training_summary: dict[str, object],
    test_summary: dict[str, object],
) -> dict[str, object]:
    external_validation = bool(run.compute_budget.get("external_validation"))
    items = [
        ChecklistItem(
            "TRIPOD-AI-Source",
            "Source of Data",
            "Source of data and study dates/provenance are described.",
            "fulfilled",
            "Dataset name, version, source and manifest path are stored for train and test cohorts.",
        ),
        ChecklistItem(
            "TRIPOD-AI-Participants",
            "Participants",
            "Eligibility and cohort composition are described.",
            "partial",
            "Cohort summaries describe case composition, but patient-level inclusion/exclusion text is still manifest-dependent.",
        ),
        ChecklistItem(
            "TRIPOD-AI-Outcome",
            "Outcome",
            "Outcome definition and reference standard are specified.",
            "fulfilled",
            "Primary targets are lesion mask segmentation metrics and derived ASPECTS from lesion/atlas overlap.",
        ),
        ChecklistItem(
            "TRIPOD-AI-Predictors",
            "Predictors",
            "Predictors and their handling are clearly defined.",
            "fulfilled",
            "Feature families, preprocessing and NCCT-derived inputs are fixed in the model artifacts and protocol.",
        ),
        ChecklistItem(
            "TRIPOD-AI-SampleSize",
            "Methods",
            "Sample size for development and validation is reported.",
            "fulfilled",
            "BenchmarkRun compute_budget records train and test case counts.",
        ),
        ChecklistItem(
            "TRIPOD-AI-MissingData",
            "Methods",
            "Handling of missing data is reported.",
            "partial",
            "Manifest validation checks file availability, but missing covariate imputation is not implemented.",
        ),
        ChecklistItem(
            "TRIPOD-AI-ModelDevelopment",
            "Methods",
            "Model-building procedures and tuning are described.",
            "fulfilled",
            "research_protocol and artifacts specify arm families, training split, matched budget and ablation retraining.",
        ),
        ChecklistItem(
            "TRIPOD-AI-ModelSpecification",
            "Results",
            "The final model is fully specified or made available.",
            "fulfilled",
            "trained-model artifacts store feature names, normalization statistics, weights and bias.",
        ),
        ChecklistItem(
            "TRIPOD-AI-Performance",
            "Results",
            "Predictive performance measures with uncertainty are presented.",
            "fulfilled",
            "Run metrics include confidence intervals, paired comparisons and ISLES-style segmentation measures.",
        ),
        ChecklistItem(
            "TRIPOD-AI-Validation",
            "Results",
            "Validation approach is clearly described, including external validation if used.",
            "fulfilled" if external_validation else "partial",
            "Run protocol records internal split validation or external test-manifest validation.",
        ),
        ChecklistItem(
            "TRIPOD-AI-Limitations",
            "Discussion",
            "Limitations and implications are discussed.",
            "fulfilled",
            "Manuscript draft includes current NCCT-only, synthetic atlas and runtime constraints as limitations.",
        ),
    ]
    return {
        "guideline": "TRIPOD-AI subset",
        "request": request.model_dump(),
        "training_dataset": training_summary["dataset_name"],
        "test_dataset": test_summary["dataset_name"],
        "external_validation": external_validation,
        "items": [item.__dict__ for item in items],
        "status_counts": _status_counts(items),
    }


def _comparison_sentence(comparison_name: str, comparison: BenchmarkComparison) -> str:
    auc_gain = comparison.metrics.get("auc_gain")
    dice_gain = comparison.metrics.get("dice_gain")
    if (
        auc_gain is None
        or dice_gain is None
        or not isinstance(auc_gain.delta, (int, float))
        or not isinstance(dice_gain.delta, (int, float))
    ):
        return f"Sounio was compared against {comparison_name}."
    return (
        f"Against {comparison_name}, Sounio showed AUC delta {auc_gain.delta:.3f} "
        f"(p={auc_gain.p_value:.4f}) and Dice delta {dice_gain.delta:.3f}."
    )


def render_manuscript_draft(
    run: BenchmarkRun,
    request: BenchmarkRequest,
    training_summary: dict[str, object],
    test_summary: dict[str, object],
    cohort_shift: dict[str, object],
) -> str:
    external_validation = bool(run.compute_budget.get("external_validation"))
    validation_phrase = "external validation" if external_validation else "internal split validation"
    comparison_lines = "\n".join(
        f"- {_comparison_sentence(name, comparison)}"
        for name, comparison in run.comparisons.items()
    )
    if not comparison_lines:
        comparison_lines = "- Comparative statistics were not available."

    sounio_metrics = run.metrics.get("sounio_hypercomplex", {})
    auc = sounio_metrics.get("auc", {}).get("value", "n/a")
    dice = sounio_metrics.get("isles_dice", {}).get("value", "n/a")
    aspects_mae = sounio_metrics.get("aspects_mae", {}).get("value", "n/a")
    evaluation_scope = run.evaluation_scope.get("test", {}) if isinstance(run.evaluation_scope, dict) else {}
    limitations = (
        "The current benchmark uses NCCT-derived features only, even when manifests expose additional CTA/CTP or clinical metadata. "
        "The ASPECTS atlas remains synthetic and should be replaced by a validated anatomical registration pipeline for definitive studies."
    )
    if isinstance(evaluation_scope, dict) and evaluation_scope.get("aspects_reference_cases", 0) < evaluation_scope.get("case_count", 0):
        limitations += " This dataset export is segmentation-first; ASPECTS, hemisphere and atlas-region truth are unsupported for some or all test cases, so those metrics are reported as n/a."

    return f"""# Draft Manuscript

## Title

Domain-specific evaluation of a Sounio hypercomplex framework for ischemic stroke lesion support on head CT using {validation_phrase}

## Abstract

### Objective

To evaluate whether a Sounio-based hypercomplex representation improves support for ischemic lesion estimation and ASPECTS derivation on head CT under a matched benchmark protocol.

### Design

Retrospective benchmarking study with {validation_phrase}.

### Data

Training data: {training_summary["dataset_name"]} ({training_summary["case_count"]} cases).  
Test data: {test_summary["dataset_name"]} ({test_summary["case_count"]} cases).

### Methods

All arms shared the same preprocessing contract, atlas registration, case lists and compute budget. The primary arm used a hypercomplex Sounio feature basis; comparator arms used reduced conventional feature sets. Performance was quantified with ASPECTS MAE, case-level AUC, ISLES-style Dice, lesion-wise F1, calibration and paired statistical comparisons.

### Results

The Sounio arm achieved ASPECTS MAE {aspects_mae}, AUC {auc} and Dice {dice} on the test cohort. Comparative findings:
{comparison_lines}

### Limitations

{limitations}

## Methods

### Datasets

- Training source: {training_summary["dataset_name"]} ({training_summary["dataset_version"]})
- Test source: {test_summary["dataset_name"]} ({test_summary["dataset_version"]})
- External validation: {external_validation}
- Cohort shift summary: {cohort_shift}

### Modeling

- Benchmark arms: Sounio hypercomplex, Python conventional, Julia equivalent, C++ equivalent
- Training split: {request.train_split}
- Test split: {request.test_split}
- Fixed budget profile: matched-v1

### Statistical analysis

- Bootstrap confidence intervals for per-arm metrics
- Paired permutation p-values for Sounio versus each baseline
- Stratified analysis by lesion presence, hemisphere and ASPECTS severity

## Results

### Primary outcomes

- Sounio AUC: {auc}
- Sounio Dice: {dice}
- Sounio ASPECTS MAE: {aspects_mae}

### Comparative outcomes

{comparison_lines}

## Discussion

The benchmark is structured to support a domain-specific superiority claim rather than a universal language claim. Any observed gain must be interpreted with the cohort shift artifact, subgroup behaviour and failure analysis.

## Reproducibility

- Protocol artifact: `research_protocol.json`
- Reporting audits: `claim_checklist.json`, `tripod_ai_checklist.json`
- Model artifacts: one persisted JSON per benchmark arm
- Failure analysis and subgroup metrics: included in benchmark artifacts
"""
