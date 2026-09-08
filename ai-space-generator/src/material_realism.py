from __future__ import annotations

import cv2
import numpy as np


def perspective_prefilter(
    rgb: np.ndarray,
    edit_mask: np.ndarray,
    *,
    far_sigma_x: float = 1.10,
    far_sigma_y: float = 2.70,
) -> np.ndarray:
    """Apply perspective-aware minification filtering inside a floor mask.

    This is a lightweight CPU analogue of mip/anisotropic texture filtering for
    a receding floor plane. Distant pixels receive stronger filtering, with more
    blur in the depth/image-y direction than image-x. The operation is bounded
    to the edit region and is intended for already-projected material imagery.
    """
    image = np.asarray(rgb, dtype=np.float32)
    edit = np.asarray(edit_mask, dtype=bool)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("rgb must be HxWx3")
    if edit.shape != image.shape[:2] or not np.any(edit):
        raise ValueError("edit_mask must match image size and contain floor pixels")

    ys = np.where(edit)[0]
    top, bottom = int(ys.min()), int(ys.max())
    height = image.shape[0]
    yy = np.arange(height, dtype=np.float32)[:, None]
    near = np.clip((yy - top) / max(bottom - top, 1), 0.0, 1.0)
    far = 1.0 - near

    levels = [
        image,
        cv2.GaussianBlur(image, (0, 0), sigmaX=0.40, sigmaY=0.80),
        cv2.GaussianBlur(image, (0, 0), sigmaX=0.75, sigmaY=1.65),
        cv2.GaussianBlur(image, (0, 0), sigmaX=far_sigma_x, sigmaY=far_sigma_y),
    ]

    w3 = np.clip((far - 0.58) / 0.42, 0.0, 1.0)[:, :, None]
    w2 = np.clip((far - 0.26) / 0.50, 0.0, 1.0)[:, :, None] * (1.0 - w3)
    w1 = np.clip(far / 0.65, 0.0, 1.0)[:, :, None] * (1.0 - w2 - w3)
    w0 = np.clip(1.0 - w1 - w2 - w3, 0.0, 1.0)
    filtered = levels[0] * w0 + levels[1] * w1 + levels[2] * w2 + levels[3] * w3

    out = image.copy()
    out[edit] = filtered[edit]
    return np.clip(out, 0, 255).astype(np.uint8)


def inside_contact_occlusion(edit_mask: np.ndarray, strength: float = 0.012, width_px: float = 1.55) -> np.ndarray:
    """Return a narrow inside-only contact-occlusion multiplier.

    The band is derived from the distance transform, so it never samples or
    blurs protected wall pixels across the semantic boundary. Keep strength and
    width intentionally small; this is contact darkening, not a scene shadow.
    """
    edit = np.asarray(edit_mask, dtype=bool)
    if not np.any(edit):
        raise ValueError("edit_mask must contain floor pixels")
    if not 0.0 <= strength <= 0.05:
        raise ValueError("strength must remain subtle")
    if width_px <= 0:
        raise ValueError("width_px must be positive")

    dist = cv2.distanceTransform(edit.astype(np.uint8), cv2.DIST_L2, 3)
    ao = np.exp(-((dist / width_px) ** 2)).astype(np.float32)
    multiplier = np.ones(edit.shape, dtype=np.float32)
    multiplier[edit] = 1.0 - strength * ao[edit]
    return multiplier
