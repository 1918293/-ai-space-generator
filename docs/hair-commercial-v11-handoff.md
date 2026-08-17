# Hair Commercial v11 — Verified Handoff

Status: **TECHNICAL_PASS_PRIMARY_CANDIDATE**  
Visual QA: **PASS**  
User acceptance: **PENDING**  
Branch: `hao-hair-sr-test-20260817`

## Authority chain

1. Verified 2x SR baseline — run `32003834720`
2. LeftRefill v06 background candidate — run `32021100743`
3. Verified MODNet matte — run `32009172839`
4. Fast Finalizer v09 background authority — run `32031627343`
5. Tone Refinement v10 — run `32038358919`
6. Edge-safe Bokeh v11 — run `32038938614`

## Why v11 supersedes v10

v10 passed color, highlight, subject-core, and background-removal gates, but its bokeh was built by Gaussian-blurring the entire clean image before MODNet compositing. That allows foreground hair/skin/clothing colors to bleed into immediately adjacent background pixels, creating a subtle cutout/halo effect.

v11 replaces only the background blur donor construction with a normalized background-only convolution using high-confidence MODNet background pixels (`matte < 0.20`) as donors. It does **not** rerun inpainting, MODNet, super-resolution, or any generative model, and it does not modify the toned subject core relative to v10.

## Production outputs

- `Hao_Hair_Commercial_PrimaryCandidate_v11.png`
  - size: 2360 × 4192
  - sha256: `f814aad96be09920818f4492d51baeedda8bf285840f0c41cbe831ca2add2fdb`
- `Hao_Hair_Commercial_Portfolio4x5_v11.png`
  - size: 1800 × 2250
  - sha256: `0eb56b5a4466bdcea2bb698949c8a89ead0684dabc8d4bf9cdcea255e2fe7543`
- `Hao_Hair_Commercial_HairSafe_Bokeh_EdgeSafe_v11.png`
  - size: 2360 × 4192
  - sha256: `d6af651d2fd74d1623704153780eab1945b483ed32f4647054e2ad369b454298`

## v11 hard gates — PASS

- Toned subject core is bit-identical to v10
- Bokeh subject core is bit-identical to clean plate
- Edge foreground donor contamination median reduced by at least 65%
- Edge foreground donor contamination P95 reduced by at least 70%
- Edge low-background-support fraction <= 0.1%
- Studio tone fidelity: PASS

## Edge contamination improvement

Measured on 112,134 high-confidence background pixels within 30 px of the MODNet subject core:

- v10 donor-matte median: `0.0973328128`
- v11 donor-matte median: `0.0252644718`
- median reduction: `74.043%`
- v10 donor-matte P95: `0.2281463705`
- v11 donor-matte P95: `0.0469810644`
- P95 reduction: `79.407%`
- low-support fraction: `0.0`

## Tone / fidelity

- Subject gradient correlation: `0.9996233708`
- Median hue shift: `0°`
- P95 hue shift: `2°`
- Median saturation shift: `1 / 255`
- v10 highlight clip fraction: `0.0088577719`
- v11 highlight clip fraction: `0.0088577719`

## Preserved authorities

v11 directly reuses the verified v10 clean plate and the already-verified MODNet matte. Background object removal, hair color, subject geometry, 2x SR baseline, and studio tone target are not re-solved.

## Rejected / do not retry automatically

- PatchMatch — wrong nearby texture transfer
- Big-LaMa — gray low-detail collapse / umbrella-edge residue
- ZITS++ — executable but visually unsuitable for this mixed-material occlusion
- LeftRefill 512 v07 — worse geometry/color contamination than v06 256
- direct source-plate v15/v16 — seam / perspective mismatch
- v09 studio key +0.08 stop — superseded by v10 highlight correction
- v10 whole-image Gaussian bokeh donor construction — superseded by v11 edge-safe background-only normalized blur

## Continuation rule

Treat v11 as the current primary candidate. Do not modify image pixels automatically unless one of these becomes true:

1. a new visible defect is identified in v11;
2. the user supplies a new source image;
3. the user explicitly requests a different visual direction;
4. a new method demonstrates a measurable improvement without reducing source fidelity.

For continuation, verify the hashes above and use `verification_final_v11.json` plus this handoff as the checkpoint.
