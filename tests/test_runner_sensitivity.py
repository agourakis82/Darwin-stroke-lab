from pathlib import Path

from sounio_stroke_lab.atlas import aspects_from_probability_map
from sounio_stroke_lab.dataset_manifest import load_manifest_cases
from sounio_stroke_lab.preprocessing import load_volume_from_paths, prepare_volume
from sounio_stroke_lab.runners import SounioHypercomplexRunner
from sounio_stroke_lab.sounio_runtime import SounioRuntime
from tests.support import create_benchmark_manifest, create_small_benchmark_manifest


def _analyze_case(case, monkeypatch):
    monkeypatch.setattr(SounioRuntime, "auto", staticmethod(lambda: None))
    loaded = load_volume_from_paths([Path(case.volume_path)])
    prepared = prepare_volume(loaded.volume, warnings=loaded.warnings)
    output = SounioHypercomplexRunner().run(prepared)
    aspects_score, region_scores = aspects_from_probability_map(output.heatmap, prepared.hemisphere)
    return prepared.hemisphere, aspects_score, {item.region for item in region_scores if item.affected}


def test_estimate_hemisphere_tracks_fixture_laterality(tmp_path, monkeypatch):
    benchmark_manifest = create_benchmark_manifest(tmp_path)
    _, benchmark_cases = load_manifest_cases(benchmark_manifest, split="test")
    small_manifest = create_small_benchmark_manifest(tmp_path)
    _, small_cases = load_manifest_cases(small_manifest, split="train")
    lookup = {case.case_id: case for case in benchmark_cases + small_cases}

    for case_id, expected in {
        "mini-001": "right",
        "mini-002": "left",
        "case-010": "left",
        "case-011": "right",
        "case-012": "left",
    }.items():
        hemisphere, _, _ = _analyze_case(lookup[case_id], monkeypatch)
        assert hemisphere == expected


def test_sounio_fallback_recovers_synthetic_fixture_lesions(tmp_path, monkeypatch):
    benchmark_manifest = create_benchmark_manifest(tmp_path)
    _, benchmark_cases = load_manifest_cases(benchmark_manifest, split="test")
    small_manifest = create_small_benchmark_manifest(tmp_path)
    _, small_cases = load_manifest_cases(small_manifest, split="train")
    lookup = {case.case_id: case for case in benchmark_cases + small_cases}

    healthy_hemi, healthy_score, healthy_regions = _analyze_case(lookup["mini-001"], monkeypatch)
    assert healthy_hemi == "right"
    assert healthy_score == 10
    assert healthy_regions == set()

    lesion_hemi, lesion_score, lesion_regions = _analyze_case(lookup["mini-002"], monkeypatch)
    assert lesion_hemi == "left"
    assert lesion_score == 8
    assert {"insula", "m2"} <= lesion_regions

    stronger_hemi, stronger_score, stronger_regions = _analyze_case(lookup["case-010"], monkeypatch)
    assert stronger_hemi == "left"
    assert stronger_score == 7
    assert {"insula", "m2", "m5"} <= stronger_regions
