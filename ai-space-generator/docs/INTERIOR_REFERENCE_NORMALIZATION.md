# Interior Reference Photo Normalization

This module standardizes a set of interior photos for design-reference review without generative editing.

## Scope

Allowed transforms are deterministic and global:

- EXIF orientation normalization
- bounded roll correction (maximum ±5°)
- center crop to one target aspect ratio
- bounded neutral-pixel white balance
- bounded exposure, contrast, and saturation correction
- ICC profile preservation when the source file contains one

It does **not** remove objects, invent surfaces, synthesize textures, or claim that different source cameras become physically identical lenses.

## Recommended workflow

1. Lock the source originals and record their hashes outside Git history when the images are private.
2. Choose one output aspect ratio for the whole set; 4:3 is a practical default for the current interior-reference workflow.
3. Estimate only the minimum roll correction needed for each frame.
4. Keep exposure within ±1 EV, saturation within 0.75–1.25, and contrast within 0.8–1.2.
5. Export generic names such as `view_01_normalized.jpg`.
6. Review the set as a group for verticals, framing, white balance, perceived brightness, floor/wall neutrality, and continuity between views.
7. Keep the original files as the geometric/content authority. Normalized images are presentation/reference derivatives.

## Example

```python
from pathlib import Path

from src.interior_reference_normalizer import NormalizationSpec, normalize_set

sources = [Path("a.jpg"), Path("b.jpg"), Path("c.jpg")]
specs = [
    NormalizationSpec(roll_degrees=-0.4),
    NormalizationSpec(roll_degrees=0.8),
    NormalizationSpec(roll_degrees=0.3),
]

normalize_set(sources, Path("normalized"), specs)
```

## Acceptance boundary

A passing run means the deterministic transform completed and its automated invariants passed. It does not by itself prove professional photographic acceptance. Visual review remains required for perspective comfort, cross-view tonal continuity, and whether the set is suitable as a design-reference package.
