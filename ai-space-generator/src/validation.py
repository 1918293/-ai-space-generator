from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image


def _binary_mask(mask: Image.Image | np.ndarray) -> np.ndarray:
    """Return a boolean mask without changing the caller's image."""
    if isinstance(mask, Image.Image):
        array = np.asarray(mask.convert("L"), dtype=np.uint8)
    else:
        array = np.asarray(mask)
        if array.ndim == 3:
            array = np.max(array, axis=2)
    return array > 8


def validate_remove_target_contract(
    edit_mask: Image.Image | np.ndarray,
    protected_mask: Image.Image | np.ndarray,
    remove_target_mask: Image.Image | np.ndarray,
) -> dict[str, Any]:
    """Validate that a requested removal target is editable, never protected.

    This is a preflight gate. It prevents a false PASS where an object that must be
    removed is accidentally placed inside Protected Mask and then rewarded for being
    pixel-identical to the source.
    """
    edit = _binary_mask(edit_mask)
    protected = _binary_mask(protected_mask)
    target = _binary_mask(remove_target_mask)

    if edit.shape != protected.shape or edit.shape != target.shape:
        raise ValueError("Edit, Protected, and remove-target masks must have identical dimensions.")

    target_pixels = int(np.count_nonzero(target))
    overlap_pixels = int(np.count_nonzero(edit & protected))
    target_editable_pixels = int(np.count_nonzero(target & edit))
    target_protected_pixels = int(np.count_nonzero(target & protected))
    target_edit_coverage_ratio = (
        float(target_editable_pixels) / float(target_pixels) if target_pixels else 0.0
    )

    hard_gate_pass = bool(
        target_pixels > 0
        and overlap_pixels == 0
        and target_protected_pixels == 0
        and target_editable_pixels == target_pixels
    )

    return {
        "remove_target_pixels": target_pixels,
        "edit_protected_overlap_pixels": overlap_pixels,
        "remove_target_in_edit_pixels": target_editable_pixels,
        "remove_target_in_protected_pixels": target_protected_pixels,
        "remove_target_edit_coverage_ratio": target_edit_coverage_ratio,
        "hard_gate_pass": hard_gate_pass,
    }


def target_modified_ratio(
    source: Image.Image | np.ndarray,
    result: Image.Image | np.ndarray,
    remove_target_mask: Image.Image | np.ndarray,
    threshold: int = 8,
) -> dict[str, Any]:
    """Measure whether target pixels changed; this is a proxy, not Visual QA.

    A high changed ratio does not prove that the object was semantically removed.
    The caller must still run target-aware Visual QA or a post-edit segmentation check.
    """
    src = np.asarray(source.convert("RGB") if isinstance(source, Image.Image) else source)
    out = np.asarray(result.convert("RGB") if isinstance(result, Image.Image) else result)
    target = _binary_mask(remove_target_mask)

    if src.shape != out.shape or src.shape[:2] != target.shape:
        raise ValueError("Source, result, and remove-target mask must have matching dimensions.")

    delta = np.max(np.abs(src.astype(np.int16) - out.astype(np.int16)), axis=2)
    target_pixels = int(np.count_nonzero(target))
    changed_pixels = int(np.count_nonzero((delta > int(threshold)) & target))
    ratio = float(changed_pixels) / float(target_pixels) if target_pixels else 0.0

    return {
        "remove_target_pixels": target_pixels,
        "target_pixels_changed": changed_pixels,
        "target_modified_ratio": ratio,
        "threshold": int(threshold),
        "semantic_removal_verified": False,
    }
