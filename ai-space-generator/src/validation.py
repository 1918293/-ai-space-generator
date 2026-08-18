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


def _pixel_delta(
    source: Image.Image | np.ndarray,
    result: Image.Image | np.ndarray,
) -> np.ndarray:
    """Return the maximum absolute per-channel delta for each pixel."""
    src = np.asarray(source.convert("RGB") if isinstance(source, Image.Image) else source)
    out = np.asarray(result.convert("RGB") if isinstance(result, Image.Image) else result)

    if src.shape != out.shape:
        raise ValueError("Source and result must have matching dimensions.")

    diff = np.abs(src.astype(np.int16) - out.astype(np.int16))
    if diff.ndim == 2:
        return diff
    if diff.ndim == 3:
        return np.max(diff, axis=2)
    raise ValueError("Source and result must be 2-D grayscale or 3-D image arrays.")


def _region_change_stats(
    delta: np.ndarray,
    region_mask: np.ndarray,
    threshold: int,
) -> dict[str, Any]:
    """Summarize pixel changes within a boolean region mask."""
    if delta.shape != region_mask.shape:
        raise ValueError("Delta and region mask must have identical dimensions.")

    region_pixels = int(np.count_nonzero(region_mask))
    changed_pixels = int(np.count_nonzero((delta > int(threshold)) & region_mask))
    changed_ratio = float(changed_pixels) / float(region_pixels) if region_pixels else 0.0

    if region_pixels:
        region_delta = delta[region_mask]
        max_delta = int(np.max(region_delta))
        mean_delta = float(np.mean(region_delta))
    else:
        max_delta = 0
        mean_delta = 0.0

    return {
        "region_pixels": region_pixels,
        "changed_pixels": changed_pixels,
        "changed_ratio": changed_ratio,
        "max_channel_delta": max_delta,
        "mean_channel_delta": mean_delta,
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
    if int(threshold) < 0:
        raise ValueError("threshold must be non-negative.")

    delta = _pixel_delta(source, result)
    target = _binary_mask(remove_target_mask)

    if delta.shape != target.shape:
        raise ValueError("Source, result, and remove-target mask must have matching dimensions.")

    stats = _region_change_stats(delta, target, int(threshold))

    return {
        "remove_target_pixels": stats["region_pixels"],
        "target_pixels_changed": stats["changed_pixels"],
        "target_modified_ratio": stats["changed_ratio"],
        "threshold": int(threshold),
        "semantic_removal_verified": False,
    }


def outside_mask_invariance(
    source: Image.Image | np.ndarray,
    result: Image.Image | np.ndarray,
    edit_mask: Image.Image | np.ndarray,
    tolerance: int = 0,
) -> dict[str, Any]:
    """Verify that pixels outside the approved edit mask remain unchanged.

    The tolerance is the maximum allowed per-pixel channel delta. A zero tolerance
    enforces exact pixel preservation outside the edit region.
    """
    if int(tolerance) < 0:
        raise ValueError("tolerance must be non-negative.")

    delta = _pixel_delta(source, result)
    edit = _binary_mask(edit_mask)

    if delta.shape != edit.shape:
        raise ValueError("Source, result, and edit mask must have matching dimensions.")

    stats = _region_change_stats(delta, ~edit, int(tolerance))

    return {
        "outside_mask_pixels": stats["region_pixels"],
        "outside_mask_changed_pixels": stats["changed_pixels"],
        "outside_mask_changed_ratio": stats["changed_ratio"],
        "outside_mask_max_channel_delta": stats["max_channel_delta"],
        "outside_mask_mean_channel_delta": stats["mean_channel_delta"],
        "tolerance": int(tolerance),
        "hard_gate_pass": bool(stats["region_pixels"] > 0 and stats["changed_pixels"] == 0),
    }


def edit_integrity_report(
    source: Image.Image | np.ndarray,
    result: Image.Image | np.ndarray,
    edit_mask: Image.Image | np.ndarray,
    protected_mask: Image.Image | np.ndarray | None = None,
    remove_target_mask: Image.Image | np.ndarray | None = None,
    tolerance: int = 0,
    target_threshold: int = 8,
    max_edit_ratio: float | None = None,
) -> dict[str, Any]:
    """Run edit-integrity hard gates from a single image-difference pass.

    This validates pixel preservation and mask geometry only. It does not verify
    semantic removal quality or brand/visual quality.
    """
    if int(tolerance) < 0:
        raise ValueError("tolerance must be non-negative.")
    if int(target_threshold) < 0:
        raise ValueError("target_threshold must be non-negative.")
    if max_edit_ratio is not None and not 0.0 < float(max_edit_ratio) <= 1.0:
        raise ValueError("max_edit_ratio must be within (0, 1].")

    delta = _pixel_delta(source, result)
    edit = _binary_mask(edit_mask)
    if delta.shape != edit.shape:
        raise ValueError("Source, result, and edit mask must have matching dimensions.")

    total_pixels = int(edit.size)
    edit_pixels = int(np.count_nonzero(edit))
    edit_ratio = float(edit_pixels) / float(total_pixels) if total_pixels else 0.0
    outside_stats = _region_change_stats(delta, ~edit, int(tolerance))

    report: dict[str, Any] = {
        "total_pixels": total_pixels,
        "edit_mask_pixels": edit_pixels,
        "edit_mask_ratio": edit_ratio,
        "outside_mask": {
            "pixels": outside_stats["region_pixels"],
            "changed_pixels": outside_stats["changed_pixels"],
            "changed_ratio": outside_stats["changed_ratio"],
            "max_channel_delta": outside_stats["max_channel_delta"],
            "mean_channel_delta": outside_stats["mean_channel_delta"],
        },
        "tolerance": int(tolerance),
        "target_threshold": int(target_threshold),
        "max_edit_ratio": None if max_edit_ratio is None else float(max_edit_ratio),
        "semantic_removal_verified": False,
    }

    gates = {
        "edit_mask_nonempty": bool(edit_pixels > 0),
        "outside_mask_exists": bool(outside_stats["region_pixels"] > 0),
        "outside_mask_unchanged": bool(outside_stats["changed_pixels"] == 0),
        "edit_ratio_within_limit": bool(
            max_edit_ratio is None or edit_ratio <= float(max_edit_ratio)
        ),
    }

    protected = None
    if protected_mask is not None:
        protected = _binary_mask(protected_mask)
        if protected.shape != edit.shape:
            raise ValueError("Protected mask must match source, result, and edit-mask dimensions.")
        protected_stats = _region_change_stats(delta, protected, int(tolerance))
        overlap_pixels = int(np.count_nonzero(edit & protected))
        report["protected_mask"] = {
            "pixels": protected_stats["region_pixels"],
            "changed_pixels": protected_stats["changed_pixels"],
            "changed_ratio": protected_stats["changed_ratio"],
            "max_channel_delta": protected_stats["max_channel_delta"],
            "mean_channel_delta": protected_stats["mean_channel_delta"],
            "edit_overlap_pixels": overlap_pixels,
        }
        gates["protected_mask_nonempty"] = bool(protected_stats["region_pixels"] > 0)
        gates["edit_protected_disjoint"] = bool(overlap_pixels == 0)
        gates["protected_mask_unchanged"] = bool(protected_stats["changed_pixels"] == 0)

    if remove_target_mask is not None:
        target = _binary_mask(remove_target_mask)
        if target.shape != edit.shape:
            raise ValueError("Remove-target mask must match source, result, and edit-mask dimensions.")
        target_stats = _region_change_stats(delta, target, int(target_threshold))
        target_edit_pixels = int(np.count_nonzero(target & edit))
        target_protected_pixels = (
            int(np.count_nonzero(target & protected)) if protected is not None else 0
        )
        target_pixels = target_stats["region_pixels"]
        report["remove_target"] = {
            "pixels": target_pixels,
            "changed_pixels": target_stats["changed_pixels"],
            "modified_ratio": target_stats["changed_ratio"],
            "in_edit_pixels": target_edit_pixels,
            "in_protected_pixels": target_protected_pixels,
        }
        gates["remove_target_nonempty"] = bool(target_pixels > 0)
        gates["remove_target_fully_editable"] = bool(
            target_pixels > 0 and target_edit_pixels == target_pixels
        )
        if protected is not None:
            gates["remove_target_not_protected"] = bool(target_protected_pixels == 0)

    report["gates"] = gates
    report["hard_gate_pass"] = bool(all(gates.values()))
    return report
