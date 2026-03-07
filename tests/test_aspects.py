import numpy as np

from sounio_stroke_lab.atlas import aspects_from_probability_map, build_aspects_atlas


def test_aspects_score_matches_affected_regions():
    shape = (32, 64, 64)
    atlas = build_aspects_atlas(shape)["right"]
    probability = np.zeros(shape, dtype=np.float32)
    probability[atlas["insula"]] = 0.95
    probability[atlas["m2"]] = 0.8
    probability[atlas["caudate"]] = 0.1

    score, region_scores = aspects_from_probability_map(probability, "right")

    affected = {item.region for item in region_scores if item.affected}
    assert score == 8
    assert affected == {"insula", "m2"}
