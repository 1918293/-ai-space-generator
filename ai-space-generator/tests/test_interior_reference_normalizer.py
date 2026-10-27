from pathlib import Path

import numpy as np
from PIL import Image

from src.interior_reference_normalizer import (
    NormalizationSpec,
    normalize_file,
    normalize_image,
    normalize_set,
)


def _synthetic_image(width=640, height=480):
    x = np.linspace(20, 235, width, dtype=np.uint8)
    y = np.linspace(15, 220, height, dtype=np.uint8)[:, None]
    r = np.tile(x, (height, 1))
    g = np.tile(y, (1, width))
    b = ((r.astype(np.uint16) + g.astype(np.uint16)) // 2).astype(np.uint8)
    return Image.fromarray(np.stack([r, g, b], axis=-1), "RGB")


def test_normalize_image_has_exact_target_size_and_is_deterministic():
    image = _synthetic_image()
    spec = NormalizationSpec(width=320, height=240, roll_degrees=0.6)
    a = np.asarray(normalize_image(image, spec))
    b = np.asarray(normalize_image(image, spec))
    assert a.shape == (240, 320, 3)
    assert np.array_equal(a, b)


def test_bounds_reject_aggressive_photometric_or_geometry_changes():
    for bad in (
        NormalizationSpec(roll_degrees=8.0),
        NormalizationSpec(exposure_ev=1.5),
        NormalizationSpec(saturation=1.5),
        NormalizationSpec(contrast=1.4),
    ):
        try:
            bad.validate()
        except ValueError:
            pass
        else:
            raise AssertionError("expected bounded spec validation to fail")


def test_normalize_file_preserves_icc_profile(tmp_path: Path):
    src = tmp_path / "source.jpg"
    dst = tmp_path / "out.jpg"
    icc = b"test-icc-profile-bytes"
    _synthetic_image().save(src, quality=95, icc_profile=icc)

    normalize_file(src, dst, NormalizationSpec(width=320, height=240))
    with Image.open(dst) as result:
        assert result.size == (320, 240)
        assert result.info.get("icc_profile") == icc


def test_normalize_set_uses_stable_generic_names(tmp_path: Path):
    sources = []
    for index in range(3):
        p = tmp_path / f"source_{index}.jpg"
        _synthetic_image().save(p, quality=95)
        sources.append(p)

    outputs = normalize_set(
        sources,
        tmp_path / "normalized",
        [NormalizationSpec(width=320, height=240)] * 3,
    )
    assert [p.name for p in outputs] == [
        "view_01_normalized.jpg",
        "view_02_normalized.jpg",
        "view_03_normalized.jpg",
    ]
    assert all(p.exists() for p in outputs)
