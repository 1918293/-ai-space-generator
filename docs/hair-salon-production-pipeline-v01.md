# Hair Salon Production Pipeline v01

Status: **VALIDATED_ON_CURRENT_CASE / GENERALIZATION_CANDIDATE**  
Branch: `hao-hair-sr-test-20260817`

## Purpose

Convert the successful single-image retouch research into a reusable production gate that reduces repeated research, skips unnecessary expensive stages, and protects hair/color fidelity before any candidate is promoted.

This v01 is intentionally a **router + QA gate**, not a universal automatic retoucher.

## Implemented components

### 1. Defect-driven Preflight

File: `tools/hair_salon_pipeline/preflight.py`

Outputs:
- resolution / short-edge check
- highlight and black clipping diagnostics
- subject/background edge-energy comparison when a matte is available
- `RUN / SKIP / REVIEW` routing for SR, bokeh, tone
- explicit `MANUAL_DEFECT_MASK_REQUIRED` for object removal

Policy:
- no automatic inpainting without an explicit defect mask
- expensive stages are skipped when not needed
- routing heuristics never replace visual direction

### 2. Hair-specific Fidelity QA

File: `tools/hair_salon_pipeline/hair_qa.py`

Current hard gates:
- hair/face proxy gradient correlation >= 0.995
- edge-energy ratio 0.90–1.15
- median hue shift <= 2°
- P95 hue shift <= 6°
- median saturation shift <= 4 / 255
- hair-proxy highlight clipping <= 1.5%

Important limitation:
- v01 does **not** yet have a dedicated hair segmentation mask.
- It uses the upper 50% of the high-confidence MODNet subject as a clearly labeled `hair/face proxy`.
- Therefore this is a validated conservative QA proxy, not a claim of true per-hair segmentation.

### 3. Reusable Production Gate Workflow

Workflow: `.github/workflows/hair-salon-production-gate-v01.yml`

The workflow accepts source, candidate, and matte artifact run IDs/names through `workflow_dispatch` inputs. Defaults point to the current verified case so the workflow is self-testing.

It performs:
1. artifact resolution
2. lightweight dependency setup
3. defect-driven preflight
4. hair-specific fidelity QA
5. production decision summary
6. evidence artifact upload
7. hard enforcement of the hair-fidelity gate

It does not rerun Real-ESRGAN, LeftRefill, MODNet, ZITS++, LaMa, or any other heavy model.

## First validation run

Workflow run: `32043310674`  
Result: **SUCCESS / PASS_PRODUCTION_GATE**

Current-case route:

`SKIP_SR -> DEFECT_MASK_REVIEW -> BOKEH_REVIEW -> SKIP_TONE_CORRECTION -> HAIR_QA`

Interpretation:
- The supplied authority was already the verified 2x SR baseline, so SR correctly skipped.
- The background edge-energy ratio was `0.3980`, above the v01 review threshold `0.35`, so bokeh was routed to review rather than blindly applied.
- Object removal remained mask-authority-driven rather than being guessed from generic image statistics.
- Hair QA passed before candidate promotion.

### Current-case Hair QA result

- status: `PASS_HAIR_FIDELITY`
- hair/face proxy gradient correlation: `0.9996753667`
- candidate/authority edge-energy ratio: `1.0101724890`
- median hue shift: `0°`
- P95 hue shift: `2°`
- median saturation shift: `1 / 255`
- hair-proxy highlight clip fraction: `0.007608` (0.761%)

## Evidence artifact

Artifact: `hao-hair-salon-production-gate-v01`  
Run: `32043310674`

Contains:
- `preflight.json`
- `hair_qa.json`
- `production_decision.json`
- `PRODUCTION_DECISION.md`

## What is verified vs not yet verified

### VERIFIED ON CURRENT CASE

- workflow executes successfully on a standard GitHub runner
- artifact routing works
- preflight can skip/review stages without heavy inference
- current v11 passes hair-fidelity gates against the verified SR authority
- evidence is retained as a compact artifact

### CANDIDATE / NOT YET GENERALIZED

- thresholds across different hair colors, lengths, curl patterns, lighting, and framing
- automatic object-defect detection
- true dedicated hair segmentation
- automatic orchestration of expensive SR/inpainting/bokeh stages
- multi-image consistency and hero-image selection

## Production rule

For a new salon image:

1. Run this gate first.
2. Only run expensive stages that the preflight or explicit visual goal requires.
3. Object removal requires a visible defect and explicit mask authority.
4. Any final candidate must pass hair-fidelity QA against its source authority.
5. Visual acceptance remains separate from automated metrics.
6. Do not modify or supersede the current v11 image authority merely because this pipeline exists.

## Next validation target

The highest-value next test is **not another change to v11**. It is to run this same gate on a second, meaningfully different salon source image to test whether the routing and hair-QA thresholds generalize without manual threshold tuning.
