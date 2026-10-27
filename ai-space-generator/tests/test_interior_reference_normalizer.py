from pathlib import Path

import pytest

from src.interior_reference_normalizer import NormalizationSpec


def test_bounds_reject_aggressive_photometric_or_geometry_changes():
    for bad in (
        NormalizationSpec(roll_degrees=8.0),
        NormalizationSpec(exposure_ev=1.5),
        NormalizationSpec(saturation=1.5),
        NormalizationSpec(contrast=1.4),
    ):
        with pytest.raises(ValueError):
            bad.validate()


def _image_stack():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    Image = pytest.importorskip("PIL.Image")
    return np, Image


def _synthetic_image(width=640, height=480):
    np, Image = _image_stack()
    x = np.linspace(20, 235, width, dtype=np.uint8)
    y = np.linspace(15, 220, height, dtype=np.uint8)[:, None]
    r = np.tile(x, (height, 1))
    g = np.tile(y, (1, width))
    b = ((r.astype(np.uint16) + g.astype(np.uint16)) // 2).astype(np.uint8)
    return Image.fromarray(np.stack([r, g, b], axis=-1), "RGB")


def test_normalize_image_has_exact_target_size_and_is_deterministic():
    np, _ = _image_stack()
    from src.interior_reference_normalizer import normalize_image

    image = _synthetic_image()
    spec = NormalizationSpec(width=320, height=240, roll_degrees=0.6)
    a = np.asarray(normalize_image(image, spec))
    b = np.asarray(normalize_image(image, spec))
    assert a.shape == (240, 320, 3)
    assert np.array_equal(a, b)
    edges = np.concatenate((a[0], a[-1], a[:, 0], a[:, -1]), axis=0)
    assert int(edges.min()) > 0


def test_normalize_file_preserves_icc_profile(tmp_path: Path):
    _, Image = _image_stack()
    from src.interior_reference_normalizer import normalize_file

    src = tmp_path / "source.jpg"
    dst = tmp_path / "out.jpg"
    icc = b"test-icc-profile-bytes"
    _synthetic_image().save(src, quality=95, icc_profile=icc)

    normalize_file(src, dst, NormalizationSpec(width=320, height=240))
    with Image.open(dst) as result:
        assert result.size == (320, 240)
        assert result.info.get("icc_profile") == icc


def test_normalize_set_uses_stable_generic_names(tmp_path: Path):
    _image_stack()
    from src.interior_reference_normalizer import normalize_set

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
