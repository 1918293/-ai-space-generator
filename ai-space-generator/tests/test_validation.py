import pytest
import numpy as np
from PIL import Image, ImageDraw

from src.validation import (
    composite_mask_only,
    target_modified_ratio,
    validate_object_role_contract,
    validate_remove_target_contract,
    verify_mask_lock,
)


def _mask(box):
    image = Image.new("L", (40, 40), 0)
    ImageDraw.Draw(image).rectangle(box, fill=255)
    return image


def test_remove_target_cannot_be_protected():
    target = _mask((10, 10, 20, 20))
    edit = Image.new("L", (40, 40), 0)
    protected = target.copy()
    qa = validate_remove_target_contract(edit, protected, target)
    assert qa["hard_gate_pass"] is False
    assert qa["remove_target_in_edit_pixels"] == 0
    assert qa["remove_target_in_protected_pixels"] == qa["remove_target_pixels"]


def test_remove_target_must_be_fully_editable():
    target = _mask((10, 10, 20, 20))
    edit = target.copy()
    protected = Image.new("L", (40, 40), 255)
    protected_array = np.asarray(protected).copy()
    protected_array[np.asarray(target) > 8] = 0
    protected = Image.fromarray(protected_array)
    qa = validate_remove_target_contract(edit, protected, target)
    assert qa["hard_gate_pass"] is True
    assert qa["remove_target_edit_coverage_ratio"] == 1.0
    assert qa["remove_target_in_protected_pixels"] == 0
    assert qa["edit_protected_overlap_pixels"] == 0


def test_preserve_occluder_must_be_protected_not_editable():
    target = _mask((10, 10, 20, 20))
    edit = Image.new("L", (40, 40), 0)
    protected = target.copy()
    qa = validate_object_role_contract(edit, protected, target, object_role="PRESERVE_OCCLUDER")
    assert qa["hard_gate_pass"] is True
    assert qa["object_in_edit_pixels"] == 0
    assert qa["object_in_protected_pixels"] == qa["object_pixels"]
    assert qa["expected_mask_coverage_ratio"] == 1.0


def test_preserve_occluder_cannot_be_treated_as_remove_target():
    target = _mask((10, 10, 20, 20))
    edit = Image.new("L", (40, 40), 0)
    protected = target.copy()
    preserve_qa = validate_object_role_contract(edit, protected, target, object_role="PRESERVE_OCCLUDER")
    remove_qa = validate_object_role_contract(edit, protected, target, object_role="REMOVE")
    assert preserve_qa["hard_gate_pass"] is True
    assert remove_qa["hard_gate_pass"] is False


def test_unchanged_target_is_not_removal_evidence():
    source = Image.new("RGB", (40, 40), "white")
    result = source.copy()
    target = _mask((10, 10, 20, 20))
    qa = target_modified_ratio(source, result, target)
    assert qa["target_modified_ratio"] == 0.0
    assert qa["semantic_removal_verified"] is False


def test_boolean_array_mask_preserves_foreground_pixels():
    target = np.zeros((40, 40), dtype=bool)
    target[10:21, 10:21] = True
    edit = target.copy()
    protected = np.zeros((40, 40), dtype=bool)
    qa = validate_remove_target_contract(edit, protected, target)
    assert qa["hard_gate_pass"] is True
    assert qa["remove_target_pixels"] == int(np.count_nonzero(target))


def test_target_modified_ratio_supports_grayscale_arrays():
    target = np.zeros((40, 40), dtype=bool)
    target[10:21, 10:21] = True
    source = np.zeros((40, 40), dtype=np.uint8)
    result = source.copy()
    result[target] = 32
    qa = target_modified_ratio(source, result, target, threshold=8)
    assert qa["target_pixels_changed"] == int(np.count_nonzero(target))
    assert qa["target_modified_ratio"] == 1.0
    assert qa["semantic_removal_verified"] is False

def _pixel_lock_fixture():
    source = np.zeros((100, 100, 3), dtype=np.uint8)
    source[..., 0] = 255
    candidate = np.zeros_like(source)
    candidate[..., 1] = 255
    edit = np.zeros((100, 100), dtype=bool)
    edit[35:65, 35:65] = True
    protected = np.zeros((100, 100), dtype=bool)
    protected[10:20, 10:20] = True
    return source, candidate, edit, protected


def test_mask_only_composite_is_bit_exact_outside_edit_mask():
    source, candidate, edit, protected = _pixel_lock_fixture()
    result = composite_mask_only(source, candidate, edit)
    qa = verify_mask_lock(source, result, edit, protected)

    assert qa["dimensions_match"] is True
    assert qa["edit_protected_overlap_pixels"] == 0
    assert qa["outside_changed_pixels"] == 0
    assert qa["outside_max_channel_diff"] == 0
    assert qa["protected_changed_pixels"] == 0
    assert qa["protected_max_channel_diff"] == 0
    assert qa["inside_changed_pixels"] == 900
    assert qa["inside_max_channel_diff"] == 255
    assert qa["hard_lock_pass"] is True


def test_mask_lock_rejects_single_outside_pixel_drift():
    source, candidate, edit, protected = _pixel_lock_fixture()
    result = composite_mask_only(source, candidate, edit)
    result[0, 0, 2] = 1

    qa = verify_mask_lock(source, result, edit, protected)

    assert qa["outside_changed_pixels"] == 1
    assert qa["outside_max_channel_diff"] == 1
    assert qa["hard_lock_pass"] is False


def test_mask_lock_rejects_protected_region_change():
    source, candidate, edit, protected = _pixel_lock_fixture()
    overlapping_protected = protected.copy()
    overlapping_protected[40, 40] = True
    result = composite_mask_only(source, candidate, edit)

    qa = verify_mask_lock(source, result, edit, overlapping_protected)

    assert qa["protected_changed_pixels"] == 1
    assert qa["protected_max_channel_diff"] == 255
    assert qa["hard_lock_pass"] is False


def test_mask_lock_shape_mismatch_fails_closed():
    source, candidate, edit, protected = _pixel_lock_fixture()

    with pytest.raises(ValueError, match="matching dimensions"):
        verify_mask_lock(source, candidate[:-1], edit, protected)


def test_mask_lock_rejects_protected_edit_overlap_even_when_pixel_unchanged():
    source, candidate, edit, protected = _pixel_lock_fixture()

    # Authorize one protected pixel inside the edit mask, but keep the
    # resulting pixel identical to the canonical source at that location.
    protected = protected.copy()
    protected[40, 40] = True
    candidate = candidate.copy()
    candidate[40, 40] = source[40, 40]
    result = composite_mask_only(source, candidate, edit)

    qa = verify_mask_lock(source, result, edit, protected)

    assert qa["edit_protected_overlap_pixels"] == 1
    assert qa["protected_changed_pixels"] == 0
    assert qa["protected_max_channel_diff"] == 0
    assert qa["hard_lock_pass"] is False


@pytest.mark.parametrize("shape", [(1, 7), (7, 1), (17, 31), (31, 17)])
@pytest.mark.parametrize("channels", [None, 3, 4])
def test_mask_lock_generalizes_to_shapes_channels_and_transforms(shape, channels):
    """Check preservation and equivariance against independent direct indexing."""
    rng = np.random.default_rng(385)
    image_shape = shape if channels is None else (*shape, channels)
    source = rng.integers(0, 256, image_shape, dtype=np.uint8)
    candidate = 255 - source
    edit = np.zeros(shape, dtype=bool)
    edit[::2, ::2] = True
    protected = ~edit
    source_before = source.copy()
    candidate_before = candidate.copy()
    edit_before = edit.copy()

    result = composite_mask_only(source, candidate, edit)
    assert np.array_equal(result[~edit], source[~edit])
    assert np.array_equal(result[edit], candidate[edit])
    assert verify_mask_lock(source, result, edit, protected)["hard_lock_pass"]
    assert np.array_equal(source, source_before)
    assert np.array_equal(candidate, candidate_before)
    assert np.array_equal(edit, edit_before)
    assert not np.shares_memory(result, source)

    for axis in (0, 1):
        transformed = composite_mask_only(
            np.flip(source, axis), np.flip(candidate, axis), np.flip(edit, axis)
        )
        assert np.array_equal(transformed, np.flip(result, axis))
    if channels is not None:
        permuted = composite_mask_only(source[..., ::-1], candidate[..., ::-1], edit)
        assert np.array_equal(permuted, result[..., ::-1])


@pytest.mark.parametrize("value,expected", [(0, False), (8, False), (9, True), (255, True)])
def test_mask_threshold_is_explicit_and_matches_pil(value, expected):
    source = np.zeros((3, 5, 3), dtype=np.uint8)
    candidate = np.full_like(source, 200)
    mask = np.full(source.shape[:2], value, dtype=np.uint8)
    result = composite_mask_only(source, candidate, mask)
    pil_result = composite_mask_only(source, candidate, Image.fromarray(mask))
    assert np.array_equal(result, pil_result)
    assert bool(np.any(result != source)) is expected


@pytest.mark.parametrize(
    "invalid_mask",
    [
        np.full((3, 5), np.nan),
        np.full((3, 5), np.inf),
        np.ones((3, 5, 3), dtype=np.uint8) * 255,
        np.ones((3, 5, 1), dtype=np.uint8) * 255,
        np.ones(15, dtype=np.uint8) * 255,
        np.empty((0, 5), dtype=np.uint8),
    ],
    ids=["nan", "infinite", "rgb", "rank3-single-channel", "rank1", "empty"],
)
def test_malformed_mask_contracts_fail_closed(invalid_mask):
    with pytest.raises(ValueError, match="mask|Mask"):
        validate_object_role_contract(invalid_mask, invalid_mask, invalid_mask, "REMOVE")


def test_mask_lock_detects_fractional_outside_drift_without_truncation():
    source = np.zeros((3, 5, 3), dtype=np.float64)
    edit = np.zeros((3, 5), dtype=bool)
    edit[1, 2] = True
    result = composite_mask_only(source, np.ones_like(source), edit)
    result[0, 0, 0] = 0.25
    qa = verify_mask_lock(source, result, edit)
    assert qa["outside_changed_pixels"] == 1
    assert qa["outside_max_channel_diff"] == 0.25
    assert qa["hard_lock_pass"] is False


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_nonfinite_image_data_is_rejected(value):
    source = np.zeros((3, 5, 3))
    edit = np.zeros((3, 5), dtype=bool)
    edit[1, 2] = True
    result = source.copy()
    result[1, 2] = value
    with pytest.raises(ValueError, match="finite"):
        verify_mask_lock(source, result, edit)


def test_pixel_lock_does_not_prove_semantic_removal():
    source = np.zeros((3, 5, 3), dtype=np.uint8)
    edit = np.zeros((3, 5), dtype=bool)
    edit[1, 2] = True
    result = composite_mask_only(source, np.full_like(source, 32), edit)
    assert verify_mask_lock(source, result, edit)["hard_lock_pass"] is True
    assert target_modified_ratio(source, result, edit)["semantic_removal_verified"] is False


def test_empty_and_unchanged_edits_cannot_be_completion_positive_controls():
    source = np.zeros((3, 5, 3), dtype=np.uint8)
    edit = np.ones((3, 5), dtype=bool)
    empty = np.zeros_like(edit)
    assert verify_mask_lock(source, source, edit)["hard_lock_pass"] is False
    assert verify_mask_lock(source, source, empty)["hard_lock_pass"] is False
    assert validate_remove_target_contract(edit, empty, empty)["hard_gate_pass"] is False


@pytest.mark.parametrize("dtype", [np.uint16, np.uint64, np.int64])
def test_pixel_differences_do_not_overflow_integer_ranges(dtype):
    source = np.full((3, 5), np.iinfo(dtype).min, dtype=dtype)
    result = source.copy()
    edit = np.zeros((3, 5), dtype=bool)
    edit[1, 2] = True
    result[1, 2] = np.iinfo(dtype).max
    qa = verify_mask_lock(source, result, edit)
    assert qa["hard_lock_pass"] is True
    assert qa["inside_max_channel_diff"] == int(np.iinfo(dtype).max) - int(np.iinfo(dtype).min)
    assert target_modified_ratio(source, result, edit)["target_modified_ratio"] == 1.0


def test_compositor_rejects_implicit_dtype_conversion():
    source = np.zeros((3, 5), dtype=np.uint8)
    candidate = np.full((3, 5), 0.5, dtype=np.float64)
    with pytest.raises(ValueError, match="data types"):
        composite_mask_only(source, candidate, np.ones((3, 5), dtype=bool))


@pytest.mark.parametrize("threshold", [-1, -0.5, np.nan, np.inf])
def test_target_modified_ratio_rejects_invalid_threshold(threshold):
    source = np.zeros((3, 5), dtype=np.uint8)
    with pytest.raises(ValueError, match="nonnegative"):
        target_modified_ratio(source, source, np.ones((3, 5), dtype=bool), threshold=threshold)


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_pil_float_mask_is_validated_before_lossy_grayscale_conversion(value):
    mask = Image.fromarray(np.full((3, 5), value, dtype=np.float32))
    with pytest.raises(ValueError, match="finite"):
        validate_object_role_contract(mask, mask, mask, "REMOVE")
