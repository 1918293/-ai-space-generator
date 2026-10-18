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
