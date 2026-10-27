"""Non-generative normalization for interior reference-photo sets.

The module intentionally limits itself to deterministic geometric and global
photometric transforms. It never synthesizes objects or textures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from PIL import Image as PILImage


@dataclass(frozen=True)
class NormalizationSpec:
    width: int = 1448
    height: int = 1086
    roll_degrees: float = 0.0
    exposure_ev: float = 0.0
    saturation: float = 0.96
    contrast: float = 1.04
    neutral_clip_percentile: float = 98.0

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("output dimensions must be positive")
        if abs(self.roll_degrees) > 5.0:
            raise ValueError("roll correction is bounded to ±5 degrees")
        if not -1.0 <= self.exposure_ev <= 1.0:
            raise ValueError("exposure correction is bounded to ±1 EV")
        if not 0.75 <= self.saturation <= 1.25:
            raise ValueError("saturation is bounded to [0.75, 1.25]")
        if not 0.8 <= self.contrast <= 1.2:
            raise ValueError("contrast is bounded to [0.8, 1.2]")
        if not 90.0 <= self.neutral_clip_percentile <= 100.0:
            raise ValueError("neutral clip percentile must be in [90, 100]")


def _image_dependencies():
    import cv2
    import numpy as np
    from PIL import Image, ImageEnhance, ImageOps

    return cv2, np, Image, ImageEnhance, ImageOps


def _largest_inscribed_rectangle(width: int, height: int, degrees: float) -> tuple[int, int]:
    """Return a centered rectangle fully contained in the rotated source footprint."""
    angle = abs(math.radians(degrees))
    if angle < 1e-12:
        return width, height

    sin_a = abs(math.sin(angle))
    cos_a = abs(math.cos(angle))
    width_is_longer = width >= height
    side_long = max(width, height)
    side_short = min(width, height)

    if side_short <= 2.0 * sin_a * cos_a * side_long or abs(sin_a - cos_a) < 1e-12:
        half_short = 0.5 * side_short
        if width_is_longer:
            crop_w = half_short / sin_a
            crop_h = half_short / cos_a
        else:
            crop_w = half_short / cos_a
            crop_h = half_short / sin_a
    else:
        cos_2a = cos_a * cos_a - sin_a * sin_a
        crop_w = (width * cos_a - height * sin_a) / cos_2a
        crop_h = (height * cos_a - width * sin_a) / cos_2a

    return max(1, math.floor(crop_w)), max(1, math.floor(crop_h))


def _rotate_edge_safe(image: "PILImage.Image", degrees: float):
    _, _, Image, _, _ = _image_dependencies()
    if abs(degrees) < 1e-9:
        return image.copy()

    rotated = image.rotate(
        degrees,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=None,
    )
    crop_w, crop_h = _largest_inscribed_rectangle(
        image.width,
        image.height,
        degrees,
    )
    left = (rotated.width - crop_w) // 2
    top = (rotated.height - crop_h) // 2
    return rotated.crop((left, top, left + crop_w, top + crop_h))


def _center_crop_aspect(image: "PILImage.Image", width: int, height: int):
    _, _, Image, _, _ = _image_dependencies()
    target_ratio = width / height
    source_ratio = image.width / image.height
    if source_ratio > target_ratio:
        crop_w = round(image.height * target_ratio)
        left = (image.width - crop_w) // 2
        box = (left, 0, left + crop_w, image.height)
    else:
        crop_h = round(image.width / target_ratio)
        top = (image.height - crop_h) // 2
        box = (0, top, image.width, top + crop_h)
    return image.crop(box).resize((width, height), Image.Resampling.LANCZOS)


def _bounded_gray_world(image: "PILImage.Image", percentile: float):
    """Neutralize global cast using bright, low-chroma pixels with bounded gain."""
    cv2, np, Image, _, _ = _image_dependencies()
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    hsv = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2HSV)
    value = hsv[..., 2].astype(np.float32)
    sat = hsv[..., 1].astype(np.float32)

    bright_cut = np.percentile(value, percentile)
    sat_cut = np.percentile(sat, 40.0)
    mask = (value >= bright_cut) & (sat <= sat_cut)
    if mask.sum() < 64:
        mask = value >= np.percentile(value, 85.0)

    samples = rgb[mask]
    means = samples.mean(axis=0)
    neutral = float(means.mean())
    gains = neutral / np.maximum(means, 1.0)
    gains = np.clip(gains, 0.90, 1.10)
    balanced = np.clip(rgb * gains.reshape(1, 1, 3), 0, 255).astype(np.uint8)
    return Image.fromarray(balanced, mode="RGB")


def normalize_image(image: "PILImage.Image", spec: NormalizationSpec):
    """Return a deterministic, non-generative normalized interior reference frame."""
    _, _, _, ImageEnhance, ImageOps = _image_dependencies()
    spec.validate()
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    normalized = _rotate_edge_safe(normalized, spec.roll_degrees)
    normalized = _center_crop_aspect(normalized, spec.width, spec.height)
    normalized = _bounded_gray_world(normalized, spec.neutral_clip_percentile)

    exposure_gain = 2.0 ** spec.exposure_ev
    normalized = ImageEnhance.Brightness(normalized).enhance(exposure_gain)
    normalized = ImageEnhance.Contrast(normalized).enhance(spec.contrast)
    normalized = ImageEnhance.Color(normalized).enhance(spec.saturation)
    return normalized


def normalize_file(source: Path, destination: Path, spec: NormalizationSpec) -> Path:
    """Normalize one image while preserving its ICC profile when one exists."""
    _, _, Image, _, _ = _image_dependencies()
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as image:
        icc = image.info.get("icc_profile")
        out = normalize_image(image, spec)
        save_kwargs = {"quality": 95, "subsampling": 0, "optimize": True}
        if icc:
            save_kwargs["icc_profile"] = icc
        out.save(destination, **save_kwargs)
    return destination


def normalize_set(
    sources: Sequence[Path],
    destination_dir: Path,
    specs: Sequence[NormalizationSpec],
) -> list[Path]:
    """Normalize a set using one explicit bounded spec per source image."""
    if len(sources) != len(specs):
        raise ValueError("sources and specs must have the same length")
    destination_dir = Path(destination_dir)
    outputs: list[Path] = []
    for index, (source, spec) in enumerate(zip(sources, specs), start=1):
        outputs.append(
            normalize_file(source, destination_dir / f"view_{index:02d}_normalized.jpg", spec)
        )
    return outputs


__all__ = ["NormalizationSpec", "normalize_image", "normalize_file", "normalize_set"]
