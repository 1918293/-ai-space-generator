from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
from PIL import Image

ScaleBasis = Literal["FIELD_MEASURED", "FIELD_ANCHORED_ESTIMATE", "RELATIVE", "SCENARIO"]


@dataclass(frozen=True)
class PerspectiveMaterialConfig:
    """Inputs required for deterministic, mask-only floor material projection.

    `plane_quad` is TL, TR, BR, BL in source-image pixels. `floor_width_mm`
    and `floor_depth_mm` define the modeled plane extent; they are not promoted
    to measured reality unless `scale_basis == FIELD_MEASURED`.
    """

    plane_quad: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]
    floor_width_mm: float
    floor_depth_mm: float
    tile_width_mm: float
    tile_depth_mm: float
    grout_mm: float = 2.0
    scale_basis: ScaleBasis = "RELATIVE"
    roughness: float = 0.85
    specular_strength: float = 0.035
    tile_pixels: int = 96


def _rgb(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.uint8)


def _mask(mask: Image.Image, size: tuple[int, int]) -> np.ndarray:
    arr = np.asarray(mask.resize(size).convert("L"), dtype=np.uint8)
    return arr > 8


def _validate_config(config: PerspectiveMaterialConfig) -> None:
    if config.floor_width_mm <= 0 or config.floor_depth_mm <= 0:
        raise ValueError("Modeled floor dimensions must be positive.")
    if config.tile_width_mm <= 0 or config.tile_depth_mm <= 0:
        raise ValueError("Tile dimensions must be positive.")
    if config.grout_mm < 0:
        raise ValueError("Grout width cannot be negative.")
    if config.tile_pixels < 24:
        raise ValueError("tile_pixels must be at least 24 for stable texture mapping.")
    quad = np.asarray(config.plane_quad, dtype=np.float32)
    if quad.shape != (4, 2):
        raise ValueError("plane_quad must contain TL, TR, BR, BL pixel points.")
    area = abs(float(cv2.contourArea(quad)))
    if area < 16.0:
        raise ValueError("plane_quad is degenerate.")
    if not 0.0 <= config.specular_strength <= 0.25:
        raise ValueError("specular_strength must stay within a low-reflection range.")
    if not 0.0 <= config.roughness <= 1.0:
        raise ValueError("roughness must be between 0 and 1.")


def metric_scale_verified(config: PerspectiveMaterialConfig) -> bool:
    """Only a field-measured plane may be called metrically verified."""
    return config.scale_basis == "FIELD_MEASURED"


def _variant(tile: np.ndarray, index: int) -> np.ndarray:
    mode = index % 8
    if mode == 0:
        return tile
    if mode == 1:
        return np.fliplr(tile)
    if mode == 2:
        return np.flipud(tile)
    if mode == 3:
        return np.flipud(np.fliplr(tile))
    if mode == 4:
        return np.rot90(tile, 1)
    if mode == 5:
        return np.rot90(tile, 2)
    if mode == 6:
        return np.rot90(tile, 3)
    return np.fliplr(np.rot90(tile, 1))


def build_orthographic_tile_plane(
    texture: Image.Image,
    config: PerspectiveMaterialConfig,
) -> tuple[np.ndarray, dict[str, int]]:
    """Build a deterministic tile plane from the supplied real/catalog swatch.

    No procedural replacement texture is synthesized here. The input texture is
    the material identity source; deterministic flips/rotations only reduce
    obvious repetition between square cells.
    """
    _validate_config(config)
    tile_px = int(config.tile_pixels)
    cols = max(1, int(np.ceil(config.floor_width_mm / config.tile_width_mm)))
    rows = max(1, int(np.ceil(config.floor_depth_mm / config.tile_depth_mm)))

    src = _rgb(texture)
    src = cv2.resize(src, (tile_px, tile_px), interpolation=cv2.INTER_AREA)
    grout_px = int(round(config.grout_mm / max(config.tile_width_mm, 1.0) * tile_px))
    grout_px = max(1 if config.grout_mm > 0 else 0, grout_px)

    median = np.median(src.reshape(-1, 3), axis=0)
    grout_colour = np.clip(median * 0.78, 0, 255).astype(np.uint8)
    plane = np.empty((rows * tile_px, cols * tile_px, 3), dtype=np.uint8)
    plane[:] = grout_colour

    inset = grout_px // 2
    for row in range(rows):
        for col in range(cols):
            cell = _variant(src, row * cols + col)
            y0, x0 = row * tile_px, col * tile_px
            y1, x1 = y0 + tile_px, x0 + tile_px
            if grout_px:
                plane[y0 + inset : y1 - (grout_px - inset), x0 + inset : x1 - (grout_px - inset)] = cell[
                    inset : tile_px - (grout_px - inset), inset : tile_px - (grout_px - inset)
                ]
            else:
                plane[y0:y1, x0:x1] = cell

    return plane, {"rows": rows, "cols": cols, "grout_px_ortho": grout_px}


def _illumination_field(source: np.ndarray, edit: np.ndarray, roughness: float) -> np.ndarray:
    gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    sigma = max(source.shape[:2]) * 0.045
    low = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    samples = low[edit]
    reference = float(np.median(samples)) if samples.size else float(np.median(low))
    reference = max(reference, 1e-4)
    ratio = np.clip(low / reference, 0.74, 1.16)
    # Rough anti-slip surfaces should retain broad illumination but suppress
    # mirror-like highlight swings.
    return 1.0 + (ratio - 1.0) * (0.55 + 0.35 * float(roughness))


def render_perspective_material(
    source: Image.Image,
    edit_mask: Image.Image,
    texture: Image.Image,
    config: PerspectiveMaterialConfig,
) -> tuple[Image.Image, dict[str, object]]:
    """Project one material onto one plane and paste it only inside Edit Mask.

    The function deliberately does not inpaint or regenerate the full photo.
    Outside-mask pixels are copied from the decoded source array after all
    transforms, enabling strict pixel invariance checks on lossless output.
    """
    _validate_config(config)
    src = _rgb(source)
    height, width = src.shape[:2]
    edit = _mask(edit_mask, (width, height))
    if not np.any(edit):
        raise ValueError("Edit mask is empty.")

    plane, plane_meta = build_orthographic_tile_plane(texture, config)
    ph, pw = plane.shape[:2]
    from_quad = np.float32([[0, 0], [pw - 1, 0], [pw - 1, ph - 1], [0, ph - 1]])
    to_quad = np.asarray(config.plane_quad, dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(from_quad, to_quad)
    warped = cv2.warpPerspective(
        plane,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    ).astype(np.float32)

    illumination = _illumination_field(src, edit, config.roughness)
    shaded = warped * illumination[..., None]
    highlight = np.clip(illumination - 1.0, 0.0, 1.0)[..., None]
    shaded += 255.0 * highlight * float(config.specular_strength) * (1.0 - 0.65 * config.roughness)
    shaded = np.clip(shaded, 0, 255).astype(np.uint8)

    out = src.copy()
    out[edit] = shaded[edit]
    # Explicit source pasteback is intentional, even though assignment above is
    # already local. It protects future refactors from contaminating preserve area.
    out[~edit] = src[~edit]

    meta: dict[str, object] = {
        **plane_meta,
        "source_size": [width, height],
        "tile_size_mm": [config.tile_width_mm, config.tile_depth_mm],
        "modeled_floor_mm": [config.floor_width_mm, config.floor_depth_mm],
        "scale_basis": config.scale_basis,
        "metric_scale_verified": metric_scale_verified(config),
        "full_image_generation_used": False,
        "outside_mask_source_pasteback": True,
    }
    return Image.fromarray(out, mode="RGB"), meta


def outside_mask_invariance(
    source: Image.Image,
    result: Image.Image,
    edit_mask: Image.Image,
) -> dict[str, int | bool]:
    src = _rgb(source)
    out = _rgb(result)
    if src.shape != out.shape:
        return {"dimensions_match": False, "outside_changed_pixels": -1, "outside_max_channel_diff": -1}
    height, width = src.shape[:2]
    edit = _mask(edit_mask, (width, height))
    delta = np.abs(src.astype(np.int16) - out.astype(np.int16))
    outside = ~edit
    changed = int(np.count_nonzero(np.max(delta, axis=2)[outside] > 0))
    max_diff = int(delta[outside].max()) if np.any(outside) else 0
    return {
        "dimensions_match": True,
        "outside_changed_pixels": changed,
        "outside_max_channel_diff": max_diff,
    }
