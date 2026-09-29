from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from src.material_projection import (
    PerspectiveMaterialConfig,
    outside_mask_invariance,
    render_perspective_material,
    validate_material_mask_contract,
)


def _write_qa(path: str, payload: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic mask-only perspective material projection."
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--edit-mask", required=True)
    parser.add_argument("--protected-mask")
    parser.add_argument("--occluder-mask")
    parser.add_argument("--texture", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--qa", required=True)
    args = parser.parse_args()

    source = Image.open(args.source).convert("RGB")
    edit_mask = Image.open(args.edit_mask).convert("L")
    protected = Image.open(args.protected_mask).convert("L") if args.protected_mask else None
    occluder = Image.open(args.occluder_mask).convert("L") if args.occluder_mask else None
    texture = Image.open(args.texture).convert("RGB")
    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    config = PerspectiveMaterialConfig(**raw)

    mask_contract = validate_material_mask_contract(
        edit_mask,
        config,
        protected_mask=protected,
        occluder_mask=occluder,
    )
    if not mask_contract["hard_gate_pass"]:
        _write_qa(
            args.qa,
            {
                "mask_contract": mask_contract,
                "pixel_lock_pass": False,
                "render_executed": False,
                "visual_qa": "NOT_RUN_MASK_GATE_FAILED",
                "promotion_allowed": False,
            },
        )
        raise SystemExit("Material mask contract failed; refusing render.")

    result, render_meta = render_perspective_material(
        source,
        edit_mask,
        texture,
        config,
    )
    pixel_qa = outside_mask_invariance(source, result, edit_mask)
    pixel_lock_pass = bool(
        pixel_qa["dimensions_match"]
        and pixel_qa["outside_changed_pixels"] == 0
        and pixel_qa["outside_max_channel_diff"] == 0
    )

    qa: dict[str, object] = {
        **pixel_qa,
        "mask_contract": mask_contract,
        "pixel_lock_pass": pixel_lock_pass,
        "render": render_meta,
        "render_executed": True,
        "visual_qa": "REQUIRED_SEPARATELY",
        "promotion_allowed": False,
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    result.save(args.output, "PNG")
    _write_qa(args.qa, qa)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
