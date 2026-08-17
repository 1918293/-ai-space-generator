# Hair Commercial v09 — Verified Handoff

Status: **TECHNICAL_PASS_PRIMARY_CANDIDATE**  
User acceptance: **PENDING**  
Branch: `hao-hair-sr-test-20260817`

## Authority chain

1. Verified 2x SR baseline — run `32003834720`
2. LeftRefill v06 background candidate — run `32021100743`
3. Verified MODNet matte — run `32009172839`
4. Fast Finalizer v09 — run `32031627343`

## Production outputs

- `Hao_Hair_Commercial_PrimaryCandidate_v09.png`
  - size: 2360 × 4192
  - sha256: `b19281dbc2b56847a70d166aab61db69adf44e63742c8d26c884f0e85437afb0`
- `Hao_Hair_Commercial_Portfolio4x5_v09.png`
  - size: 1800 × 2250
  - sha256: `f32a7dbb00dc1ad6581d3af60319b629fab460cb22e1aec51c8ebcd074e6ab08`
- clean plate sha256: `b2c577837b344559422ce0e054b128a140c0bb50543920035e06ac59845931bf`
- bokeh sha256: `b46731d2102a1c024423e4a368ff47aeb3305f618f3b0768e31c777f7d6c10c4`

## Hard gates — PASS

- Outside combined edit: bit-identical to verified baseline
- Subject core: bit-identical to verified baseline
- Bokeh subject core: bit-identical to clean plate
- Residual background red pixels: 0
- Edited background red pixels: 0
- Studio tone fidelity: PASS
- Subject gradient correlation: 0.9995386989
- Median hue shift: 0°
- P95 hue shift: 2°
- Median saturation shift: 1 / 255

## Efficiency checkpoint

Finalization now reuses verified artifacts and runs in one deterministic workflow. It does **not** rerun generative inpainting, MODNet inference, or heavy model downloads.

Current finalizer: `.github/workflows/hair-commercial-finalize-v08.yml` (workflow name v09 after QA strengthening).

## Do not automatically retry

The following routes were tested and should stay rejected for this image unless new evidence changes the decision:

- PatchMatch — copied nearby subject/clothing texture into the missing background
- Big-LaMa — gray/low-detail collapse and umbrella-edge residue
- ZITS++ — execution feasible, visual result unsuitable for this mixed-material occlusion
- LeftRefill 512 v07 — higher resolution worsened geometry/color contamination; v06 256 is superior
- Source-plate v15/v16 as direct final — visible seam / geometry mismatch

## Continuation rule

Do not modify v09 automatically. Next image edit requires a new visible defect, a new source image, or explicit user preference change. For continuation, verify hashes first and use this handoff plus `verification_final_v09.json` as the checkpoint.
