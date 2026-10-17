import numpy as np
import pytest

from src.masked_edit_lock import composite_mask_only, verify_mask_lock


def _fixture():
    original = np.zeros((100, 100, 3), dtype=np.uint8)
    original[..., 0] = 255

    generated = np.zeros_like(original)
    generated[..., 1] = 255

    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[35:65, 35:65] = 255
    return original, generated, mask


def test_mask_only_composite_is_bit_exact_outside_mask():
    original, generated, mask = _fixture()

    output = composite_mask_only(original, generated, mask)
    report = verify_mask_lock(original, output, mask)

    assert report["dimensions_match"] is True
    assert report["outside_changed_pixels"] == 0
    assert report["outside_max_channel_diff"] == 0
    assert report["inside_changed_pixels"] == 900
    assert report["inside_max_channel_diff"] == 255
    assert report["hard_lock_pass"] is True


def test_validator_detects_single_pixel_drift_outside_mask():
    original, generated, mask = _fixture()
    output = composite_mask_only(original, generated, mask)
    output[0, 0, 2] = 1

    report = verify_mask_lock(original, output, mask)

    assert report["outside_changed_pixels"] == 1
    assert report["outside_max_channel_diff"] == 1
    assert report["hard_lock_pass"] is False


def test_shape_mismatch_fails_closed():
    original, generated, mask = _fixture()

    with pytest.raises(ValueError, match="image shape mismatch"):
        composite_mask_only(original, generated[:-1], mask)
