from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.ndimage import gaussian_filter

from sounio_stroke_lab.config import ATLAS_REGIONS, PIPELINE_VERSION
from sounio_stroke_lab.features import extract_feature_maps, normalize_feature, region_feature_vectors
from sounio_stroke_lab.hypercomplex import logistic
from sounio_stroke_lab.preprocessing import PreprocessedVolume
from sounio_stroke_lab.schemas import LanguageStack, ModelFamily, TrainedModelArtifact
from sounio_stroke_lab.sounio_runtime import SounioRuntime
from sounio_stroke_lab.trainable_models import load_trained_model_artifact
from sounio_stroke_lab.undersegmentation_gate import (
    apply_undersegmentation_gate,
    is_undersegmentation_gate_configured,
    undersegmentation_gate_feature_row,
    undersegmentation_support_maps,
)


def _region_score_map(preprocessed: PreprocessedVolume, region_scores: list[float]) -> np.ndarray:
    score_map = np.zeros_like(preprocessed.volume)
    for region_name, region_score in zip(ATLAS_REGIONS, region_scores, strict=True):
        score_map += preprocessed.atlas[region_name].astype(np.float32) * float(region_score)
    return np.clip(score_map, 0.0, 1.0)


def _artifact_feature_rows(
    preprocessed: PreprocessedVolume,
    feature_maps: dict[str, np.ndarray],
    artifact: TrainedModelArtifact,
) -> np.ndarray:
    vectors = region_feature_vectors(preprocessed, feature_maps)
    return _artifact_feature_rows_from_vectors(vectors, artifact)


def _artifact_feature_rows_from_vectors(
    vectors: dict[str, list[float]],
    artifact: TrainedModelArtifact,
) -> np.ndarray:
    rows = [
        [vectors[feature_name][region_index] for feature_name in artifact.feature_names]
        for region_index in range(len(ATLAS_REGIONS))
    ]
    return np.asarray(rows, dtype=np.float32)


def _burden_prior_row(rows: np.ndarray) -> np.ndarray:
    summary: list[float] = []
    for column in range(rows.shape[1]):
        values = rows[:, column].astype(np.float32)
        sorted_values = np.sort(values)
        topk = sorted_values[-min(3, len(sorted_values)) :]
        summary.extend(
            [
                float(values.mean()),
                float(values.max(initial=0.0)),
                float(topk.mean() if topk.size else 0.0),
            ]
        )
    return np.asarray(summary, dtype=np.float32)


def _predict_burden_voxels(rows: np.ndarray, artifact: TrainedModelArtifact) -> float | None:
    if (
        not artifact.burden_prior_weights
        or artifact.burden_prior_bias is None
        or not artifact.burden_prior_feature_mean
        or not artifact.burden_prior_feature_std
    ):
        return None
    summary = _burden_prior_row(rows)
    mean = np.asarray(artifact.burden_prior_feature_mean, dtype=np.float32)
    std = np.asarray(artifact.burden_prior_feature_std, dtype=np.float32)
    weights = np.asarray(artifact.burden_prior_weights, dtype=np.float32)
    standardized = (summary - mean) / std
    predicted_log_voxels = float(np.dot(standardized, weights) + float(artifact.burden_prior_bias))
    return float(np.expm1(predicted_log_voxels))


def _standardize_rows(rows: np.ndarray, artifact: TrainedModelArtifact) -> np.ndarray:
    mean = np.asarray(artifact.feature_mean, dtype=np.float32)
    std = np.asarray(artifact.feature_std, dtype=np.float32)
    return (rows - mean) / std


def _numpy_region_scores(rows: np.ndarray, artifact: TrainedModelArtifact) -> list[float]:
    standardized = _standardize_rows(rows, artifact)
    weights = np.asarray(artifact.weights, dtype=np.float32)
    bias = np.asarray(artifact.bias, dtype=np.float32)
    logits = np.sum(standardized * weights, axis=1) + bias
    return [float(value) for value in logistic(logits)]


def _local_evidence(feature_maps: dict[str, np.ndarray], feature_names: list[str]) -> np.ndarray:
    maps = [feature_maps[name] for name in feature_names if name in feature_maps]
    if not maps:
        return np.full_like(next(iter(feature_maps.values())), 0.5)
    stacked = np.stack(maps, axis=0)
    return np.clip(np.mean(stacked, axis=0), 0.0, 1.0)


def _artifact_modulation_evidence(
    feature_maps: dict[str, np.ndarray],
    artifact: TrainedModelArtifact,
) -> np.ndarray:
    # Keep spatial modulation tied to localization features so richer stacks do not
    # inflate the voxel map a second time after already shaping the regional logits.
    weighted_feature_names = [
        ("deficit", 0.6),
        ("asymmetry", 0.25),
        ("gradient_suppression", 0.15),
    ]
    weighted_maps = [
        (feature_maps[name], weight)
        for name, weight in weighted_feature_names
        if name in artifact.feature_names and name in feature_maps
    ]
    if not weighted_maps:
        return _local_evidence(feature_maps, artifact.feature_names)
    total_weight = sum(weight for _, weight in weighted_maps)
    evidence = sum(feature_map * weight for feature_map, weight in weighted_maps) / total_weight
    return np.clip(evidence, 0.0, 1.0)


def _base_heatmap_from_artifact(
    preprocessed: PreprocessedVolume,
    feature_maps: dict[str, np.ndarray],
    artifact: TrainedModelArtifact,
    region_scores: list[float],
) -> tuple[np.ndarray, np.ndarray]:
    region_scores_array = np.asarray(region_scores, dtype=np.float32)
    region_map = _region_score_map(preprocessed, region_scores)
    modulation = 0.9 + 0.2 * _artifact_modulation_evidence(feature_maps, artifact)
    signal = region_map * modulation
    if float(region_scores_array.max(initial=0.0)) < 0.4:
        local_support = normalize_feature(
            0.65 * feature_maps["deficit"]
            + 0.2 * feature_maps["asymmetry"]
            + 0.15 * feature_maps["gradient_suppression"]
        )
        support_gate = np.clip(region_map / (float(region_map.max()) or 1.0), 0.0, 1.0)
        signal = signal + 0.18 * local_support * support_gate
        sigma = 0.6
    else:
        sigma = 0.75
    heatmap = np.clip(gaussian_filter(signal, sigma=sigma), 0.0, 1.0).astype(np.float32)
    return heatmap, region_map


def _apply_legacy_burden_prior_gain(
    heatmap: np.ndarray,
    region_map: np.ndarray,
    feature_maps: dict[str, np.ndarray],
    artifact: TrainedModelArtifact,
    region_scores: list[float],
    rows: np.ndarray,
) -> np.ndarray:
    region_scores_array = np.asarray(region_scores, dtype=np.float32)
    burden_voxels = _predict_burden_voxels(rows, artifact)
    if burden_voxels is None:
        return heatmap
    reference_threshold = float(artifact.burden_prior_reference_threshold or 0.25)
    current_voxels = float(np.count_nonzero(heatmap >= reference_threshold))
    high_confidence = float(np.quantile(heatmap, 0.995)) >= 0.9
    if current_voxels >= 2500.0 and high_confidence:
        return heatmap
    current_voxels = max(current_voxels, 1.0)
    max_voxels = float(heatmap.size)
    target_voxels = float(np.clip(burden_voxels, 1.0, max_voxels * 0.2))
    ratio = target_voxels / current_voxels
    if ratio <= 1.15:
        return heatmap
    local_support = normalize_feature(
        0.65 * feature_maps["deficit"]
        + 0.2 * feature_maps["asymmetry"]
        + 0.15 * feature_maps["gradient_suppression"]
    )
    support_gate = np.clip(region_map / (float(region_map.max()) or 1.0), 0.0, 1.0)
    underseg_support = local_support * support_gate
    broad_support_fraction = float(np.mean(underseg_support >= 0.35))
    weak_regional_signal = float(region_scores_array.max(initial=0.0)) < 0.5
    weak_heatmap_peak = float(np.quantile(heatmap, 0.995)) < 0.72
    clearly_undersegmented = (
        ratio >= 1.5
        and current_voxels < 2500.0
        and broad_support_fraction >= 0.01
        and weak_regional_signal
        and weak_heatmap_peak
    )
    if not clearly_undersegmented:
        return heatmap
    gain_strength = (
        float(artifact.burden_prior_gain_strength)
        if artifact.burden_prior_gain_strength is not None
        else 0.3
    )
    gain = float(np.clip(ratio ** gain_strength, 1.0, 1.18))
    return np.clip(heatmap * gain, 0.0, 1.0).astype(np.float32)


def _feature_value_map(rows: np.ndarray, artifact: TrainedModelArtifact) -> dict[str, np.ndarray]:
    return {
        feature_name: rows[:, index].astype(np.float32)
        for index, feature_name in enumerate(artifact.feature_names)
    }


def _weighted_feature_average(
    feature_values: dict[str, np.ndarray],
    feature_weights: list[tuple[str, float]],
) -> np.ndarray | None:
    selected = [
        (feature_values[name], weight)
        for name, weight in feature_weights
        if name in feature_values
    ]
    if not selected:
        return None
    total_weight = sum(weight for _, weight in selected)
    return sum(values * weight for values, weight in selected) / total_weight


def _calibrate_hypercomplex_artifact_scores(
    rows: np.ndarray,
    artifact: TrainedModelArtifact,
    region_scores: list[float],
) -> list[float]:
    feature_values = _feature_value_map(rows, artifact)
    region_scores_array = np.asarray(region_scores, dtype=np.float32)
    localization_support = _weighted_feature_average(
        feature_values,
        [("deficit", 0.3), ("asymmetry", 0.7)],
    )
    burden_support = _weighted_feature_average(
        feature_values,
        [("deficit", 0.55), ("asymmetry", 0.2), ("gradient_suppression", 0.25)],
    )
    expansive_drive = _weighted_feature_average(
        feature_values,
        [("energy", 0.5), ("coupling", 0.3), ("smoothness", 0.2)],
    )
    if localization_support is None or expansive_drive is None:
        return region_scores_array.tolist()
    excess_drive = np.clip(expansive_drive - localization_support - 0.05, 0.0, None)
    attenuations = np.where(
        region_scores_array >= 0.75,
        np.clip(1.0 - 0.4 * excess_drive, 0.82, 1.0),
        1.0,
    )
    calibrated = np.clip(region_scores_array * attenuations, 0.0, 1.0)

    # When burden support is broad but every regional probability stays too small,
    # the decoder collapses to near-empty masks on large lesions. Apply a modest
    # region-wise uplift only in that weak-signal regime.
    if burden_support is not None:
        weak_signal = float(calibrated.max(initial=0.0)) < 0.4
        broad_support = float(np.mean(burden_support >= 0.55))
        if weak_signal and broad_support >= 0.3:
            support_gate = np.clip((burden_support - 0.45) / 0.35, 0.0, 1.0)
            uplift = 1.0 + 1.15 * broad_support
            local_bonus = 0.12 * support_gate
            calibrated = np.maximum(calibrated, 0.025 * support_gate)
            calibrated = np.clip(calibrated * uplift + local_bonus, 0.0, 1.0)

    return [float(value) for value in calibrated]


@dataclass
class RunnerOutput:
    heatmap: np.ndarray
    language_stack: LanguageStack
    model_family: ModelFamily
    elapsed_ms: float
    compute_budget: dict[str, object]
    notes: list[str]

    @property
    def global_confidence(self) -> float:
        return float(np.quantile(self.heatmap, 0.995))


class BaseRunner:
    language_stack: LanguageStack
    model_family: ModelFamily
    pipeline_version = PIPELINE_VERSION

    def __init__(
        self,
        removed_component: str | None = None,
        model_artifact: TrainedModelArtifact | None = None,
    ):
        self.removed_component = removed_component
        self.model_artifact = model_artifact
        self._last_notes: list[str] = []

    def run(self, preprocessed: PreprocessedVolume) -> RunnerOutput:
        start = perf_counter()
        self._last_notes = []
        heatmap = self._predict(preprocessed)
        elapsed_ms = (perf_counter() - start) * 1000.0
        compute_budget = {
            "target_shape": list(preprocessed.volume.shape),
            "budget_profile": "matched-v1",
            "device": "cpu",
            "trained_artifact": self.model_artifact is not None,
        }
        if self.model_artifact is not None:
            compute_budget.update(
                {
                    "artifact_version": self.model_artifact.artifact_version,
                    "trained_on_split": self.model_artifact.trained_on_split,
                }
            )
        return RunnerOutput(
            heatmap=np.clip(heatmap, 0.0, 1.0).astype(np.float32),
            language_stack=self.language_stack,
            model_family=self.model_family,
            elapsed_ms=round(elapsed_ms, 3),
            compute_budget=compute_budget,
            notes=self._notes(),
        )

    def _predict(self, preprocessed: PreprocessedVolume) -> np.ndarray:
        if self.model_artifact is not None:
            return self._predict_with_artifact(preprocessed)
        return self._predict_without_artifact(preprocessed)

    def _predict_without_artifact(self, preprocessed: PreprocessedVolume) -> np.ndarray:
        raise NotImplementedError

    def _predict_with_artifact(self, preprocessed: PreprocessedVolume) -> np.ndarray:
        feature_maps = extract_feature_maps(preprocessed)
        region_vectors = region_feature_vectors(preprocessed, feature_maps)
        rows = _artifact_feature_rows_from_vectors(region_vectors, self.model_artifact)
        region_scores = self._artifact_region_scores(rows)
        heatmap, region_map = _base_heatmap_from_artifact(
            preprocessed,
            feature_maps,
            self.model_artifact,
            region_scores,
        )
        if (
            self.model_artifact.model_family == ModelFamily.sounio_hypercomplex
            and self.model_artifact.undersegmentation_gate_policy is not None
        ):
            if not is_undersegmentation_gate_configured(self.model_artifact):
                self._last_notes.append(
                    "Sounio undersegmentation gate remained no-op "
                    f"({self.model_artifact.undersegmentation_gate_policy})."
                )
                return heatmap
            burden_voxels = _predict_burden_voxels(rows, self.model_artifact)
            underseg_support, support_gate = undersegmentation_support_maps(feature_maps, region_map)
            feature_row = undersegmentation_gate_feature_row(
                heatmap=heatmap,
                region_scores=region_scores,
                region_feature_vectors=region_vectors,
                burden_voxels=burden_voxels,
                reference_threshold=float(self.model_artifact.burden_prior_reference_threshold or 0.25),
                underseg_support=underseg_support,
                support_gate=support_gate,
            )
            gated_heatmap, active, gain, probability = apply_undersegmentation_gate(
                heatmap=heatmap,
                underseg_support=underseg_support,
                support_gate=support_gate,
                feature_row=feature_row,
                artifact=self.model_artifact,
            )
            if active:
                self._last_notes.append(
                    "Sounio undersegmentation gate expanded a spatially eligible mask "
                    f"(gain={gain:.3f}, p={float(probability or 0.0):.3f})."
                )
            else:
                self._last_notes.append(
                    "Sounio undersegmentation gate kept the frozen heatmap unchanged "
                    f"(policy={self.model_artifact.undersegmentation_gate_policy}, p={float(probability or 0.0):.3f})."
                )
            return gated_heatmap
        return _apply_legacy_burden_prior_gain(
            heatmap,
            region_map,
            feature_maps,
            self.model_artifact,
            region_scores,
            rows,
        )

    def _artifact_region_scores(self, rows: np.ndarray) -> list[float]:
        return _numpy_region_scores(rows, self.model_artifact)

    def _notes(self) -> list[str]:
        notes = list(self._last_notes)
        if self.model_artifact is not None:
            notes.append(
                f"Using trained artifact {self.model_artifact.artifact_version} from split "
                f"'{self.model_artifact.trained_on_split}'."
            )
            notes.extend(self.model_artifact.notes)
        return list(dict.fromkeys(notes))


class SounioHypercomplexRunner(BaseRunner):
    language_stack = LanguageStack.sounio
    model_family = ModelFamily.sounio_hypercomplex

    def _predict_without_artifact(self, preprocessed: PreprocessedVolume) -> np.ndarray:
        feature_maps = extract_feature_maps(preprocessed)
        deficit = feature_maps["deficit"]
        asymmetry = feature_maps["asymmetry"]
        smoothness = feature_maps["smoothness"]
        gradient_suppression = feature_maps["gradient_suppression"]
        energy = feature_maps["energy"]
        coupling = feature_maps["coupling"]
        atlas_prior = np.zeros_like(preprocessed.volume)
        for mask in preprocessed.atlas.values():
            atlas_prior += mask.astype(np.float32)
        atlas_prior = np.clip(atlas_prior, 0.0, 1.0)

        if self.removed_component == "hypercomplex_phase":
            coupling = np.zeros_like(coupling)
        if self.removed_component == "asymmetry_channel":
            asymmetry = np.zeros_like(asymmetry)
        if self.removed_component == "hypercomplex_energy":
            energy = np.zeros_like(energy)

        region_scores = self._runtime_scores(preprocessed, deficit, asymmetry, smoothness, gradient_suppression)
        if region_scores is None:
            core_signal = (
                2.4 * deficit * (0.2 + asymmetry)
                + 1.0 * energy * (0.15 + asymmetry)
                + 0.4 * coupling
                + 0.2 * smoothness * asymmetry
            )
            signal = atlas_prior * core_signal + 0.08 * gradient_suppression * asymmetry
            smoothed = gaussian_filter(signal, sigma=0.9)
            return logistic(5.0 * (smoothed - 0.85))

        region_map = _region_score_map(preprocessed, region_scores)
        local_evidence = normalize_feature(
            1.8 * deficit * (0.2 + asymmetry)
            + 0.9 * energy * (0.15 + asymmetry)
            + 0.35 * coupling
            + 0.15 * smoothness * asymmetry
        )
        signal = region_map * (0.25 + local_evidence) + 0.06 * gradient_suppression * asymmetry
        smoothed = gaussian_filter(signal, sigma=0.9)
        return logistic(5.0 * (smoothed - 0.42))

    def _artifact_region_scores(self, rows: np.ndarray) -> list[float]:
        runtime = SounioRuntime.auto()
        if runtime is None:
            self._last_notes.append(
                "Official GitHub Sounio runtime not detected or not at GitHub HEAD; using Python artifact scorer."
            )
            return _calibrate_hypercomplex_artifact_scores(rows, self.model_artifact, super()._artifact_region_scores(rows))
        try:
            standardized = _standardize_rows(rows, self.model_artifact)
            scores = runtime.score_linear_regions(
                self.model_artifact.feature_names,
                standardized.tolist(),
                self.model_artifact.weights,
                self.model_artifact.bias,
            )
            self._last_notes.append(
                f"Sounio runtime artifact scorer active via {runtime.souc_path} ({runtime.source})."
            )
            return _calibrate_hypercomplex_artifact_scores(rows, self.model_artifact, scores)
        except Exception as exc:
            self._last_notes.append(f"Sounio runtime artifact scoring failed ({exc}); using Python fallback scorer.")
            return _calibrate_hypercomplex_artifact_scores(rows, self.model_artifact, super()._artifact_region_scores(rows))

    def _runtime_scores(
        self,
        preprocessed: PreprocessedVolume,
        deficit: np.ndarray,
        asymmetry: np.ndarray,
        smoothness: np.ndarray,
        gradient_suppression: np.ndarray,
    ) -> list[float] | None:
        runtime = SounioRuntime.auto()
        if runtime is None:
            self._last_notes.append(
                "Official GitHub Sounio runtime not detected or not at GitHub HEAD; using Python fallback scorer."
            )
            return None
        try:
            region_deficit = []
            region_asymmetry = []
            region_smoothness = []
            region_gradient = []
            for region_name in ATLAS_REGIONS:
                mask = preprocessed.atlas[region_name]
                region_deficit.append(float(deficit[mask].mean()))
                region_asymmetry.append(float(asymmetry[mask].mean()))
                region_smoothness.append(float(smoothness[mask].mean()))
                region_gradient.append(float(gradient_suppression[mask].mean()))
            scores = runtime.score_regions(region_deficit, region_asymmetry, region_smoothness, region_gradient)
            self._last_notes.append(f"Sounio runtime scorer active via {runtime.souc_path} ({runtime.source}).")
            return scores
        except Exception as exc:
            self._last_notes.append(f"Sounio runtime failed ({exc}); using Python fallback scorer.")
            return None

    def _notes(self) -> list[str]:
        notes = super()._notes()
        if self.removed_component:
            notes.append(f"Ablation removed component: {self.removed_component}.")
        return list(dict.fromkeys(notes))


class PythonConventionalRunner(BaseRunner):
    language_stack = LanguageStack.python
    model_family = ModelFamily.python_3d_conventional

    def _predict_without_artifact(self, preprocessed: PreprocessedVolume) -> np.ndarray:
        feature_maps = extract_feature_maps(preprocessed)
        deficit = feature_maps["deficit"]
        asymmetry = feature_maps["asymmetry"]
        signal = 1.3 * deficit + 0.9 * asymmetry + 0.12 * feature_maps["gradient_suppression"]
        return logistic(3.1 * (gaussian_filter(signal, sigma=1.2) - 1.05))


class JuliaEquivalentRunner(BaseRunner):
    language_stack = LanguageStack.julia
    model_family = ModelFamily.julia_equivalent

    def _predict_without_artifact(self, preprocessed: PreprocessedVolume) -> np.ndarray:
        feature_maps = extract_feature_maps(preprocessed)
        smoothed_deficit = gaussian_filter(feature_maps["deficit"], sigma=1.5)
        signal = 1.15 * smoothed_deficit + 0.8 * feature_maps["asymmetry"] + 0.22 * feature_maps["smoothness"]
        return logistic(3.0 * (signal - 0.98))


class CppEquivalentRunner(BaseRunner):
    language_stack = LanguageStack.cpp
    model_family = ModelFamily.cpp_equivalent

    def _predict_without_artifact(self, preprocessed: PreprocessedVolume) -> np.ndarray:
        feature_maps = extract_feature_maps(preprocessed)
        thresholded = np.where(feature_maps["deficit"] > 0.38, feature_maps["deficit"], 0.0)
        signal = 1.05 * gaussian_filter(thresholded, sigma=0.75) + 0.55 * feature_maps["asymmetry"]
        return logistic(3.2 * (signal - 0.92))


def build_runner(
    model_family: ModelFamily,
    removed_component: str | None = None,
    model_artifact_path: str | None = None,
    model_artifact: TrainedModelArtifact | None = None,
) -> BaseRunner:
    if model_artifact_path is not None and model_artifact is not None:
        raise ValueError("Specify either model_artifact_path or model_artifact, not both.")
    if model_artifact is None and model_artifact_path is not None:
        model_artifact = load_trained_model_artifact(Path(model_artifact_path).expanduser().resolve())
    if model_artifact is not None and model_artifact.model_family != model_family:
        raise ValueError(
            f"Artifact model family mismatch: expected {model_family.value}, got {model_artifact.model_family.value}."
        )

    mapping = {
        ModelFamily.sounio_hypercomplex: SounioHypercomplexRunner,
        ModelFamily.python_3d_conventional: PythonConventionalRunner,
        ModelFamily.julia_equivalent: JuliaEquivalentRunner,
        ModelFamily.cpp_equivalent: CppEquivalentRunner,
    }
    runner_cls = mapping[model_family]
    return runner_cls(removed_component=removed_component, model_artifact=model_artifact)


def comparative_runners(
    model_artifacts: dict[str, TrainedModelArtifact] | None = None,
) -> dict[str, BaseRunner]:
    model_artifacts = model_artifacts or {}
    return {
        ModelFamily.sounio_hypercomplex.value: build_runner(
            ModelFamily.sounio_hypercomplex,
            model_artifact=model_artifacts.get(ModelFamily.sounio_hypercomplex.value),
        ),
        ModelFamily.python_3d_conventional.value: build_runner(
            ModelFamily.python_3d_conventional,
            model_artifact=model_artifacts.get(ModelFamily.python_3d_conventional.value),
        ),
        ModelFamily.julia_equivalent.value: build_runner(
            ModelFamily.julia_equivalent,
            model_artifact=model_artifacts.get(ModelFamily.julia_equivalent.value),
        ),
        ModelFamily.cpp_equivalent.value: build_runner(
            ModelFamily.cpp_equivalent,
            model_artifact=model_artifacts.get(ModelFamily.cpp_equivalent.value),
        ),
    }
