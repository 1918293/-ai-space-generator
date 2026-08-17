# Hair Commercial v10 — Verified Handoff

Status: **TECHNICAL_PASS_PRIMARY_CANDIDATE**  
Visual QA: **PASS**  
User acceptance: **PENDING**  
Branch: `hao-hair-sr-test-20260817`

## Authority chain

1. Verified 2x SR baseline — run `32003834720`
2. LeftRefill v06 background candidate — run `32021100743`
3. Verified MODNet matte — run `32009172839`
4. Fast Finalizer v09 background/package authority — run `32031627343`
5. Tone Refinement v10 — run `32038358919`

## Why v10 supersedes v09

v09 passed its background and fidelity gates, but multi-scale visual QA found avoidable 255-level highlight clipping concentrated on the face, hands, and pink shirt lettering. v10 changes only the studio key exposure from `+0.08 stop` to `+0.05 stop`; background reconstruction, MODNet matte, bokeh, crop geometry, and subject structure are not rerun.

## Production outputs

- `Hao_Hair_Commercial_PrimaryCandidate_v10.png`
  - size: 2360 × 4192
  - sha256: `c77f12f96dc145aee2ee3434466fdc58322c02b4a4dac78f2dabdfcea8ae6154`
- `Hao_Hair_Commercial_Portfolio4x5_v10.png`
  - size: 1800 × 2250
  - sha256: `e2edbb08367147fb2e76b1d29a4e1b07e716be79146b83e466172b1c45c0cf65`

## v10 hard gates — PASS

- Highlight clipping reduced by at least 20% vs v09
- Subject-core highlight clipping reduced vs v09
- Subject structure fidelity: PASS
- Tone color fidelity: PASS
- Subject gradient correlation: `0.9996481936`
- Median hue shift: `0°`
- P95 hue shift: `2°`
- Median saturation shift: `1 / 255`

## Highlight clipping improvement

- v09 full-frame highlight clip fraction: `0.0115045607`
- v10 full-frame highlight clip fraction: `0.0088577719`
- reduction: `23.006%`
- v09 subject-core highlight clip fraction: `0.0258702713`
- v10 subject-core highlight clip fraction: `0.0199184451`
- subject-core reduction: `23.006%`

## Preserved v09 background authority

The v10 workflow directly reuses the verified v09 clean plate and hair-safe bokeh. It does not rerun LeftRefill, MODNet, ZITS++, LaMa, PatchMatch, Real-ESRGAN, or any generative inpainting model.

## Rejected / do not retry automatically

- PatchMatch — wrong nearby texture transfer
- Big-LaMa — gray low-detail collapse / umbrella-edge residue
- ZITS++ — executable but visually unsuitable for this mixed-material occlusion
- LeftRefill 512 v07 — worse geometry/color contamination than v06 256
- direct source-plate v15/v16 — seam / perspective mismatch
- Studio key `+0.08 stop` v09 — technically valid but superseded by v10 due avoidable highlight clipping

## Continuation rule

Treat v10 as the current primary candidate. Do not modify image pixels automatically unless one of these becomes true:

1. a new visible defect is identified in v10;
2. the user supplies a new source image;
3. the user explicitly requests a different visual direction;
4. a new method demonstrates a measurable improvement without reducing source fidelity.

For continuation, verify the v10 hashes above and use `verification_final_v10.json` plus this handoff as the checkpoint.
