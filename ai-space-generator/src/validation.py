from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

VALID_OBJECT_ROLES = {"REMOVE", "PRESERVE_OCCLUDER", "FIXED_STRUCTURE"}


def _binary_mask(mask: Image.Image | np.ndarray) -> np.ndarray:
    """Return a nonempty, finite, single-channel mask without mutating input.

    Boolean arrays retain their meaning. Numeric masks use the existing > 8
    threshold; array callers must supply a two-dimensional channel explicitly.
    """
    if isinstance(mask, Image.Image):
        original = np.asarray(mask)
        if original.dtype.kind not in "biuf" or not np.all(np.isfinite(original)):
            raise ValueError("Mask must contain finite numeric or boolean values.")
        array = np.asarray(mask.convert("L"), dtype=np.uint8)
    else:
        array = np.asarray(mask)
    if array.ndim != 2 or not array.size:
        raise ValueError("Mask must be a nonempty two-dimensional single-channel array.")
    if array.dtype.kind not in "biuf" or not np.all(np.isfinite(array)):
        raise ValueError("Mask must contain finite numeric or boolean values.")
    if array.dtype == np.bool_:
        return array.copy()
    return array > 8


def _image_array(image: Image.Image | np.ndarray) -> np.ndarray:
    """Reject malformed or non-finite image data before comparison/compositing."""
    array = np.asarray(image)
    if array.ndim not in (2, 3) or not array.size:
        raise ValueError("Image must be a nonempty 2-D grayscale or 3-D image array.")
    if array.dtype.kind not in "biuf" or not np.all(np.isfinite(array)):
        raise ValueError("Image must contain finite numeric or boolean values.")
    return array


def _absolute_difference(source: np.ndarray, result: np.ndarray) -> np.ndarray:
    """Subtract without unsigned overflow or truncating fractional differences."""
    if source.dtype.kind in "iu" and result.dtype.kind in "iu":
        # Python integers are needed only for 64-bit integer extremes. Ordinary
        # image buffers use the inexpensive vectorized int64 path.
        dtype = object if max(source.dtype.itemsize, result.dtype.itemsize) == 8 else np.int64
    else:
        dtype = np.result_type(source.dtype, result.dtype, np.float64)
    return np.abs(source.astype(dtype) - result.astype(dtype))


def validate_edit_protected_contract(
    edit_mask: Image.Image | np.ndarray,
    protected_mask: Image.Image | np.ndarray,
) -> dict[str, Any]:
    """Reject any authorization overlap between editable and protected pixels."""
    edit = _binary_mask(edit_mask)
    protected = _binary_mask(protected_mask)

    if edit.shape != protected.shape:
        raise ValueError("Edit and Protected masks must have identical dimensions.")

    overlap_pixels = int(np.count_nonzero(edit & protected))
    return {
        "edit_mask_pixels": int(np.count_nonzero(edit)),
        "protected_mask_pixels": int(np.count_nonzero(protected)),
        "edit_protected_overlap_pixels": overlap_pixels,
        "hard_gate_pass": overlap_pixels == 0,
    }


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
    src = _image_array(source.convert("RGB") if isinstance(source, Image.Image) else source)
    out = _image_array(result.convert("RGB") if isinstance(result, Image.Image) else result)
    target = _binary_mask(remove_target_mask)

    if src.shape != out.shape or src.shape[:2] != target.shape:
        raise ValueError("Source, result, and remove-target mask must have matching dimensions.")

    if not np.isfinite(float(threshold)) or float(threshold) < 0:
        raise ValueError("Target-change threshold must be finite and nonnegative.")
    diff = _absolute_difference(src, out)
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

def validate_promotion_contract(
    *,
    technical_preservation_pass: bool,
    mask_minimality_pass: bool,
    target_absence_pass: bool,
    local_quality_pass: bool,
    color_profile_pass: bool,
    human_acceptance_pass: bool | None = None,
) -> dict[str, Any]:
    """Combine independent local-edit gates without letting pixel QA imply acceptance.

    The required machine-verifiable gates must all pass before an artifact can
    become a technical candidate. Human acceptance is deliberately separate:
    None means technically admissible but not accepted for promotion.
    """
    required = {
        "technical_preservation": technical_preservation_pass,
        "mask_minimality": mask_minimality_pass,
        "target_absence": target_absence_pass,
        "local_quality": local_quality_pass,
        "color_profile": color_profile_pass,
    }
    if any(type(value) is not bool for value in required.values()):
        raise ValueError("Promotion gate inputs must be booleans.")
    if human_acceptance_pass is not None and type(human_acceptance_pass) is not bool:
        raise ValueError("human_acceptance_pass must be True, False, or None.")

    failed_gates = [name for name, passed in required.items() if not passed]
    technical_candidate_pass = not failed_gates
    pending_human_acceptance = technical_candidate_pass and human_acceptance_pass is None
    production_acceptance_pass = technical_candidate_pass and human_acceptance_pass is True

    return {
        "required_gates": required,
        "failed_gates": failed_gates,
        "technical_candidate_pass": technical_candidate_pass,
        "human_acceptance_pass": human_acceptance_pass,
        "pending_human_acceptance": pending_human_acceptance,
        "production_acceptance_pass": production_acceptance_pass,
        "promotion_allowed": production_acceptance_pass,
    }

def composite_mask_only(
    source: Image.Image | np.ndarray,
    candidate: Image.Image | np.ndarray,
    edit_mask: Image.Image | np.ndarray,
) -> np.ndarray:
    """Paste candidate pixels only inside edit_mask; keep all other source pixels bit-exact."""
    src = _image_array(source)
    out = _image_array(candidate)
    edit = _binary_mask(edit_mask)

    if src.shape != out.shape or src.shape[:2] != edit.shape:
        raise ValueError("Source, candidate, and edit mask must have matching dimensions.")
    if src.ndim not in (2, 3):
        raise ValueError("Source and candidate must be 2-D grayscale or 3-D image arrays.")
    if src.dtype != out.dtype:
        raise ValueError("Source and candidate must have matching image data types.")

    composited = src.copy()
    composited[edit] = out[edit]
    return composited


def verify_mask_lock(
    source: Image.Image | np.ndarray,
    result: Image.Image | np.ndarray,
    edit_mask: Image.Image | np.ndarray,
    protected_mask: Image.Image | np.ndarray | None = None,
) -> dict[str, Any]:
    """Verify bit-exact preservation outside edit_mask and inside an optional protected region."""
    src = _image_array(source)
    out = _image_array(result)
    edit = _binary_mask(edit_mask)
    protected = (
        np.zeros_like(edit, dtype=bool)
        if protected_mask is None
        else _binary_mask(protected_mask)
    )

    if (
        src.shape != out.shape
        or src.shape[:2] != edit.shape
        or protected.shape != edit.shape
    ):
        raise ValueError(
            "Source, result, edit mask, and protected mask must have matching dimensions."
        )
    if src.ndim not in (2, 3):
        raise ValueError("Source and result must be 2-D grayscale or 3-D image arrays.")

    diff = _absolute_difference(src, out)
    unequal = src != out
    changed = unequal if unequal.ndim == 2 else np.any(unequal, axis=2)

    outside = ~edit
    edit_protected_overlap_pixels = int(np.count_nonzero(edit & protected))
    outside_changed_pixels = int(np.count_nonzero(changed & outside))
    protected_changed_pixels = int(np.count_nonzero(changed & protected))
    inside_changed_pixels = int(np.count_nonzero(changed & edit))

    outside_max_channel_diff = np.asarray(diff[outside].max()).item() if np.any(outside) else 0
    protected_max_channel_diff = np.asarray(diff[protected].max()).item() if np.any(protected) else 0
    inside_max_channel_diff = np.asarray(diff[edit].max()).item() if np.any(edit) else 0

    return {
        "dimensions_match": True,
        "edit_mask_pixels": int(np.count_nonzero(edit)),
        "edit_protected_overlap_pixels": edit_protected_overlap_pixels,
        "outside_changed_pixels": outside_changed_pixels,
        "outside_max_channel_diff": outside_max_channel_diff,
        "protected_changed_pixels": protected_changed_pixels,
        "protected_max_channel_diff": protected_max_channel_diff,
        "inside_changed_pixels": inside_changed_pixels,
        "inside_max_channel_diff": inside_max_channel_diff,
        "hard_lock_pass": bool(
            edit_protected_overlap_pixels == 0
            and outside_changed_pixels == 0
            and outside_max_channel_diff == 0
            and protected_changed_pixels == 0
            and protected_max_channel_diff == 0
            and inside_changed_pixels > 0
        ),
    }

