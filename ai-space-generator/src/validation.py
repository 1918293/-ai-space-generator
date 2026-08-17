from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

VALID_OBJECT_ROLES = {"REMOVE", "PRESERVE_OCCLUDER", "FIXED_STRUCTURE"}


def _binary_mask(mask: Image.Image | np.ndarray) -> np.ndarray:
    """Return a boolean mask without changing the caller's image."""
    if isinstance(mask, Image.Image):
        array = np.asarray(mask.convert("L"), dtype=np.uint8)
    else:
        array = np.asarray(mask)
        if array.ndim == 3:
            array = np.max(array, axis=2)
    if array.dtype == np.bool_:
        return array.copy()
    return array > 8


def validate_object_role_contract(
    edit_mask: Image.Image | np.ndarray,
    protected_mask: Image.Image | np.ndarray,
    object_mask: Image.Image | np.ndarray,
    object_role: str,
) -> dict[str, Any]:
    """Validate mask placement against the object's task-specific semantic role."""
    role = str(object_role).upper()
    if role not in VALID_OBJECT_ROLES:
        raise ValueError(f"Unsupported object_role: {object_role}")

    edit = _binary_mask(edit_mask)
    protected = _binary_mask(protected_mask)
    target = _binary_mask(object_mask)

    if edit.shape != protected.shape or edit.shape != target.shape:
        raise ValueError("Edit, Protected, and object masks must have identical dimensions.")

    target_pixels = int(np.count_nonzero(target))
    overlap_pixels = int(np.count_nonzero(edit & protected))
    target_editable_pixels = int(np.count_nonzero(target & edit))
    target_protected_pixels = int(np.count_nonzero(target & protected))

    if role == "REMOVE":
        expected_pixels = target_editable_pixels
        conflicting_pixels = target_protected_pixels
    else:
        expected_pixels = target_protected_pixels
        conflicting_pixels = target_editable_pixels

    expected_coverage_ratio = (
        float(expected_pixels) / float(target_pixels) if target_pixels else 0.0
    )
    hard_gate_pass = bool(
        target_pixels > 0
        and overlap_pixels == 0
        and conflicting_pixels == 0
        and expected_pixels == target_pixels
    )

    return {
        "object_role": role,
        "object_pixels": target_pixels,
        "edit_protected_overlap_pixels": overlap_pixels,
        "object_in_edit_pixels": target_editable_pixels,
        "object_in_protected_pixels": target_protected_pixels,
        "expected_mask_coverage_ratio": expected_coverage_ratio,
        "hard_gate_pass": hard_gate_pass,
    }


def validate_remove_target_contract(
    edit_mask: Image.Image | np.ndarray,
    protected_mask: Image.Image | np.ndarray,
    remove_target_mask: Image.Image | np.ndarray,
) -> dict[str, Any]:
    """Validate that a requested removal target is editable, never protected."""
    qa = validate_object_role_contract(
        edit_mask,
        protected_mask,
        remove_target_mask,
        object_role="REMOVE",
    )
    return {
        "remove_target_pixels": qa["object_pixels"],
        "edit_protected_overlap_pixels": qa["edit_protected_overlap_pixels"],
        "remove_target_in_edit_pixels": qa["object_in_edit_pixels"],
        "remove_target_in_protected_pixels": qa["object_in_protected_pixels"],
        "remove_target_edit_coverage_ratio": qa["expected_mask_coverage_ratio"],
        "hard_gate_pass": qa["hard_gate_pass"],
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

    diff = np.abs(src.astype(np.int16) - out.astype(np.int16))
    if diff.ndim == 2:
        delta = diff
    elif diff.ndim == 3:
        delta = np.max(diff, axis=2)
    else:
        raise ValueError("Source and result must be 2-D grayscale or 3-D image arrays.")

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
