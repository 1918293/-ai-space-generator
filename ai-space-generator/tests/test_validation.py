import numpy as np
from PIL import Image, ImageDraw

from src.validation import target_modified_ratio, validate_remove_target_contract


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


def test_unchanged_target_is_not_removal_evidence():
    source = Image.new("RGB", (40, 40), "white")
    result = source.copy()
    target = _mask((10, 10, 20, 20))

    qa = target_modified_ratio(source, result, target)

    assert qa["target_modified_ratio"] == 0.0
    assert qa["semantic_removal_verified"] is False
