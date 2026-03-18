from pathlib import Path

import numpy as np
from PIL import Image

from sounio_stroke_lab.preprocessing import load_volume_from_paths
from sounio_stroke_lab.schemas import InputMode


def test_load_volume_from_png_stack_uses_research_mode(tmp_path: Path):
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    for index, value in enumerate((0, 127, 255)):
        Image.fromarray(np.full((6, 5), value, dtype=np.uint8)).save(stack_dir / f"{index:03d}.png")

    loaded = load_volume_from_paths(sorted(stack_dir.glob("*.png")))

    assert loaded.input_mode == InputMode.research
    assert loaded.volume.shape == (3, 6, 5)
    assert "image stack" in " ".join(loaded.warnings).lower()
    assert float(loaded.volume.min()) == 0.0
    assert 0.49 <= float(loaded.volume[1].mean()) <= 0.51
    assert float(loaded.volume.max()) == 1.0


def test_load_mask_from_png_stack_preserves_positive_labels(tmp_path: Path):
    stack_dir = tmp_path / "mask-stack"
    stack_dir.mkdir()
    for index, value in enumerate((0, 3, 0)):
        pixels = np.zeros((6, 5), dtype=np.uint8)
        if value:
            pixels[2:4, 1:3] = value
        Image.fromarray(pixels).save(stack_dir / f"{index:03d}.png")

    loaded = load_volume_from_paths(sorted(stack_dir.glob("*.png")), treat_as_mask=True)

    assert loaded.input_mode == InputMode.research
    assert loaded.volume.shape == (3, 6, 5)
    assert "mask stack" in " ".join(loaded.warnings).lower()
    assert float(loaded.volume.max()) == 1.0
    assert int((loaded.volume > 0.25).sum()) == 4
