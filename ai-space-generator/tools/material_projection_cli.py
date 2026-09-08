from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from src.material_projection import (
    PerspectiveMaterialConfig,
    outside_mask_invariance,
    render_perspective_material,
)


def _binary(mask: Image.Image, size: tuple[int, int]) -> np.ndarray:
    return np.asarray(mask.resize(size).convert("L"), dtype=np.uint8) > 8


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic mask-only perspective material projection."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--edit-mask", required=True)
    parser.add_argument("--protected-mask")
    parser.add_argument("--texture", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--qa", required=True)
    args = parser.parse_args()

    source = Image.open(args.source).convert("RGB")
    edit_mask = Image.open(args.edit_mask).convert("L")
    texture = Image.open(args.texture).convert("RGB")
    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    config = PerspectiveMaterialConfig(**raw)

    protected_overlap = 0
    if args.protected_mask:
        protected = Image.open(args.protected_mask).convert("L")
        edit = _binary(edit_mask, source.size)
        protect = _binary(protected, source.size)
        protected_overlap = int(np.count_nonzero(edit & protect))
        if protected_overlap:
            raise SystemExit(
                f"Protected mask overlaps Edit Mask by {protected_overlap} pixels; refusing render."
            )

    result, render_meta = render_perspective_material(
        source,
        edit_mask,
        texture,
        config,
    )
    qa = outside_mask_invariance(source, result, edit_mask)
    qa.update(
        {
            "protected_overlap_pixels": protected_overlap,
            "pixel_lock_pass": bool(
                qa["dimensions_match"]
                and qa["outside_changed_pixels"] == 0
                and qa["outside_max_channel_diff"] == 0
                and protected_overlap == 0
            ),
            "render": render_meta,
            "visual_qa": "REQUIRED_SEPARATELY",
            "promotion_allowed": False,
        }
    )

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    result.save(args.output, "PNG")
    Path(args.qa).write_text(
        json.dumps(qa, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
