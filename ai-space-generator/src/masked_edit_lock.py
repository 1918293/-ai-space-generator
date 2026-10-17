"""Hard pixel-lock helpers for masked image edits.

The generator may alter any pixel internally. The final acceptance surface is
defined here: only pixels inside allowed_mask may differ from the canonical
source, and image dimensions must remain identical.
"""

from __future__ import annotations

import numpy as np


def _validate_shapes(
    original: np.ndarray,
    other: np.ndarray,
    allowed_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    original = np.asarray(original)
    other = np.asarray(other)
    mask = np.asarray(allowed_mask)

    if original.ndim != 3:
        raise ValueError("original must be HxWxC")
    if other.shape != original.shape:
        raise ValueError(
            f"image shape mismatch: original={original.shape}, other={other.shape}"
        )
    if mask.shape != original.shape[:2]:
        raise ValueError(
            f"mask shape mismatch: mask={mask.shape}, image={original.shape[:2]}"
        )

    return original, other, mask.astype(bool)


def composite_mask_only(
    original: np.ndarray,
    generated: np.ndarray,
    allowed_mask: np.ndarray,
) -> np.ndarray:
    """Return original pixels everywhere except inside allowed_mask."""
    original, generated, mask = _validate_shapes(original, generated, allowed_mask)
    output = original.copy()
    output[mask] = generated[mask]
    return output


def verify_mask_lock(
    original: np.ndarray,
    output: np.ndarray,
    allowed_mask: np.ndarray,
) -> dict[str, int | bool]:
    """Measure whether an edit obeys the hard masked-edit contract."""
    original, output, mask = _validate_shapes(original, output, allowed_mask)

    diff = np.abs(output.astype(np.int16) - original.astype(np.int16))
    changed = np.any(diff != 0, axis=2)

    outside = ~mask
    outside_changed_pixels = int(np.count_nonzero(changed & outside))
    inside_changed_pixels = int(np.count_nonzero(changed & mask))
    outside_max_channel_diff = int(diff[outside].max()) if np.any(outside) else 0
    inside_max_channel_diff = int(diff[mask].max()) if np.any(mask) else 0

    return {
        "dimensions_match": True,
        "outside_changed_pixels": outside_changed_pixels,
        "outside_max_channel_diff": outside_max_channel_diff,
        "inside_changed_pixels": inside_changed_pixels,
        "inside_max_channel_diff": inside_max_channel_diff,
        "hard_lock_pass": (
            outside_changed_pixels == 0
            and outside_max_channel_diff == 0
            and inside_changed_pixels > 0
        ),
    }
