import numpy as np
from PIL import Image, ImageDraw

from src.validation import (
    edit_integrity_report,
    outside_mask_invariance,
    target_modified_ratio,
    validate_object_role_contract,
    validate_remove_target_contract,
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


def test_outside_mask_invariance_passes_when_changes_stay_inside_edit_mask():
    source = np.zeros((20, 20, 3), dtype=np.uint8)
    result = source.copy()
    edit = np.zeros((20, 20), dtype=bool)
    edit[8:12, 8:12] = True
    result[edit] = 255

    qa = outside_mask_invariance(source, result, edit)

    assert qa["hard_gate_pass"] is True
    assert qa["outside_mask_changed_pixels"] == 0
    assert qa["outside_mask_max_channel_delta"] == 0


def test_outside_mask_invariance_fails_when_unapproved_pixel_changes():
    source = np.zeros((20, 20, 3), dtype=np.uint8)
    result = source.copy()
    edit = np.zeros((20, 20), dtype=bool)
    edit[8:12, 8:12] = True
    result[1, 1] = 12

    qa = outside_mask_invariance(source, result, edit)

    assert qa["hard_gate_pass"] is False
    assert qa["outside_mask_changed_pixels"] == 1
    assert qa["outside_mask_changed_ratio"] > 0.0


def test_outside_mask_invariance_honors_small_tolerance():
    source = np.zeros((20, 20, 3), dtype=np.uint8)
    result = np.ones((20, 20, 3), dtype=np.uint8)
    edit = np.zeros((20, 20), dtype=bool)
    edit[8:12, 8:12] = True

    qa = outside_mask_invariance(source, result, edit, tolerance=1)

    assert qa["hard_gate_pass"] is True
    assert qa["outside_mask_changed_pixels"] == 0
    assert qa["outside_mask_max_channel_delta"] == 1


def test_edit_integrity_report_combines_core_gates_in_one_pass():
    source = np.zeros((20, 20, 3), dtype=np.uint8)
    result = source.copy()
    edit = np.zeros((20, 20), dtype=bool)
    edit[8:12, 8:12] = True
    target = edit.copy()
    protected = np.zeros((20, 20), dtype=bool)
    protected[0:4, 0:4] = True
    result[edit] = 255

    qa = edit_integrity_report(
        source,
        result,
        edit,
        protected_mask=protected,
        remove_target_mask=target,
        max_edit_ratio=0.10,
    )

    assert qa["hard_gate_pass"] is True
    assert qa["outside_mask"]["changed_pixels"] == 0
    assert qa["protected_mask"]["changed_pixels"] == 0
    assert qa["protected_mask"]["edit_overlap_pixels"] == 0
    assert qa["remove_target"]["modified_ratio"] == 1.0
    assert qa["semantic_removal_verified"] is False


def test_edit_integrity_report_rejects_edit_protected_overlap():
    source = np.zeros((20, 20, 3), dtype=np.uint8)
    result = source.copy()
    edit = np.zeros((20, 20), dtype=bool)
    edit[8:12, 8:12] = True
    protected = np.zeros((20, 20), dtype=bool)
    protected[10:14, 10:14] = True

    qa = edit_integrity_report(source, result, edit, protected_mask=protected)

    assert qa["hard_gate_pass"] is False
    assert qa["gates"]["edit_protected_disjoint"] is False
    assert qa["protected_mask"]["edit_overlap_pixels"] > 0


def test_edit_integrity_report_rejects_oversized_edit_mask():
    source = np.zeros((20, 20, 3), dtype=np.uint8)
    result = source.copy()
    edit = np.zeros((20, 20), dtype=bool)
    edit[:15, :] = True

    qa = edit_integrity_report(source, result, edit, max_edit_ratio=0.30)

    assert qa["hard_gate_pass"] is False
    assert qa["edit_mask_ratio"] == 0.75
    assert qa["gates"]["edit_ratio_within_limit"] is False


def test_edit_integrity_report_detects_unapproved_change():
    source = np.zeros((20, 20, 3), dtype=np.uint8)
    result = source.copy()
    edit = np.zeros((20, 20), dtype=bool)
    edit[8:12, 8:12] = True
    result[1, 1] = 9

    qa = edit_integrity_report(source, result, edit, tolerance=0)

    assert qa["hard_gate_pass"] is False
    assert qa["outside_mask"]["changed_pixels"] == 1
    assert qa["gates"]["outside_mask_unchanged"] is False
