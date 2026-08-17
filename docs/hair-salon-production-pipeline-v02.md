# Hair Salon Production Pipeline v02

Status: **LIMITED_GENERALIZATION_VALIDATED / PRODUCTION_GATE_READY**  
Branch: `hao-hair-sr-test-20260817`

## Scope

v02 completes the reusable **routing + fidelity gate** for salon portrait work. It is intentionally not a universal automatic retoucher.

It now supports two execution modes:

1. **GitHub artifact mode** through `.github/workflows/hair-salon-production-gate-v01.yml` (workflow name: `Hao Hair Salon Production Gate v02`).
2. **Local/private-file mode** through `tools/hair_salon_pipeline/gate.py`, so personal photos do not need to be committed to a public repository.

The gate runs Preflight first, skips unnecessary expensive stages, requires explicit defect-mask authority for object removal, and blocks promotion when hair-fidelity QA fails.

## Implemented files

- `tools/hair_salon_pipeline/preflight.py`
- `tools/hair_salon_pipeline/hair_qa.py`
- `tools/hair_salon_pipeline/gate.py`
- `.github/workflows/hair-salon-production-gate-v01.yml`

## Single-command local usage

```bash
python tools/hair_salon_pipeline/gate.py \
  --source SOURCE_IMAGE \
  --candidate CANDIDATE_IMAGE \
  --matte MATTE_IMAGE \
  --output-dir reports
```

If a candidate is supplied without a matte, the gate returns `REVIEW_PRODUCTION_GATE` instead of pretending the candidate is verified.

## Validation A — current verified commercial case

Production Gate v01 baseline run: `32043310674` — SUCCESS / PASS_PRODUCTION_GATE.

Refactored v02 regression run: `32046720973` — SUCCESS.

Current-case route:

`SKIP_SR -> DEFECT_MASK_REVIEW -> BOKEH_REVIEW -> SKIP_TONE_CORRECTION -> HAIR_QA`

Current-case Hair QA remained PASS after the v02 single-command refactor.

## Validation B — second, meaningfully different salon source

A private Library photo was used locally and was **not committed or uploaded to the public GitHub repository**.

Source characteristics:

- dark, straight/layered hair rather than warm-brown curled layers
- side/profile framing rather than the first case's frontal-downward composition
- glasses + black mask
- visually busy real salon background
- source size: `864 x 1536`

Second-case Preflight route:

`RUN_SR -> DEFECT_MASK_REVIEW -> BOKEH_REVIEW -> SKIP_TONE_CORRECTION -> HAIR_QA`

Second-case diagnostics:

- short edge: `864` -> SR correctly routed to RUN for a 1600 px target
- highlight clip fraction: `0.0013473`
- black clip fraction: `0.0`
- background/subject edge-energy ratio: `0.5139365` -> bokeh routed to REVIEW, not blindly applied or skipped

### Hair-QA threshold stress tests on case B

The second case used a conservative local subject proxy for threshold stress testing. It was not claimed as a production MODNet matte.

#### Positive control — restrained luminance-only change

Change: `+0.035 stop` in linear light.

Result: **PASS**

- gradient correlation: `0.9997255`
- edge-energy ratio: `1.0124416`
- median hue shift: `0°`
- P95 hue shift: `2°`
- median saturation shift: `1 / 255`
- hair-proxy highlight clip fraction: `0.0006312`

#### Negative control — hair color drift

Change: `+12°` hue shift in the hair proxy.

Result: **FAIL as intended**

- median hue shift: `12°`
- P95 hue shift: `14°`

#### Negative control — hair structure blur

Change: local Gaussian blur in the hair proxy.

Result: **FAIL as intended**

- gradient correlation: `0.5848777`
- edge-energy ratio: `0.3943655`

#### Negative control — full-image regenerated salon candidate

A previously generated salon image with altered composition/hair rendering was used as a deliberate negative control after size normalization.

Result: **FAIL as intended**

- gradient correlation: `0.0503404`
- edge-energy ratio: `1.6153265`
- median hue shift: `16°`
- P95 hue shift: `160°`
- median saturation shift: `24 / 255`

This is important because the gate rejects a visually polished but source-nonfaithful full regeneration instead of rewarding appearance alone.

## What v02 can now be trusted to do

### VERIFIED / READY

- run Preflight before expensive processing
- distinguish `RUN / SKIP / REVIEW` decisions for SR, bokeh, and tone diagnostics
- refuse automatic object removal without an explicit defect mask
- run hair/face-proxy structure, edge, color, and highlight fidelity gates
- reject controlled color drift, structural blur, and a source-nonfaithful full regeneration
- evaluate local/private files without storing the photos in the public repository
- execute the same decision logic from GitHub Actions through one command
- preserve compact JSON/Markdown evidence for each gate run

### STILL CANDIDATE / NOT CLAIMED

- true dedicated hair segmentation across all hair types
- automatic defect-mask generation
- automatic execution of SR/inpainting/bokeh models based only on routing output
- universal thresholds across every hair color, curl pattern, lighting setup, camera, and framing
- multi-image color consistency / hero-image selection

## Production policy

1. Run `gate.py` first.
2. Do not run an expensive stage merely because it exists.
3. Object removal requires a visible defect and explicit mask authority.
4. Candidate promotion requires source-relative hair fidelity QA.
5. A polished full regeneration that changes source geometry/color must fail.
6. Private source images should stay local unless the user explicitly chooses to upload them.
7. Automated metrics never replace final visual acceptance.

## Current state

The original single-image research has now been converted into a **reusable, tested production gate**. The next meaningful expansion is no longer required to complete the current task; it would be a separate capability-development track such as dedicated hair segmentation, automatic expensive-stage orchestration, or multi-image consistency.
