# Hair Salon Production Pipeline v04

Status: **ORCHESTRATION_VALIDATED / SAFE_SR_EXECUTOR_READY / MULTI_IMAGE_QA_VALIDATED**  
Branch: `hao-hair-sr-test-20260817`  
Main branch: **UNTOUCHED**

## Purpose

Convert the salon-portrait research chain into a reusable, safety-first production workflow without turning aesthetic or generative edits into blind automation.

## Current execution chain

`Preflight -> Safety Orchestrator -> Optional Safe SR -> Dedicated Hair Mask -> Hair QA -> Multi-image Set QA -> Human Visual Acceptance`

Implemented files:

- `tools/hair_salon_pipeline/preflight.py`
- `tools/hair_salon_pipeline/orchestrator.py`
- `tools/hair_salon_pipeline/safe_sr.py`
- `tools/hair_salon_pipeline/hair_segment.py`
- `tools/hair_salon_pipeline/hair_qa.py`
- `tools/hair_salon_pipeline/gate.py`
- `tools/hair_salon_pipeline/multi_image_qa.py`

## Automation boundary

| Stage | Automation status | Required authority |
|---|---|---|
| SR | AUTO-ELIGIBLE only when Preflight=RUN + explicit permission | source image + pinned executor |
| Object removal | REVIEW / BLOCK without mask | explicit defect mask + visual review |
| Bokeh | VISUAL REVIEW | subject matte + visual direction |
| Tone | VISUAL REVIEW | clipping diagnostics + visual direction |
| Hair QA | AUTO | source authority + subject matte + preferably dedicated source-derived hair mask |
| Multi-image consistency | AUTO diagnostics / ADVISORY ranking | same client + same hairstyle/color service set |
| Final promotion | HUMAN | visual acceptance |

`RUN` from Preflight never equals execution permission.

## Dedicated hair segmentation

Production Gate v03 uses a source-derived dedicated hair mask rather than the older upper-subject proxy when available.

Verified current-case metrics:

- hair gradient correlation: `0.9951358502`
- edge-energy ratio: `1.000062828`
- median hue shift: `0 deg`
- P95 hue shift: `2 deg`
- median saturation shift: `1 / 255`
- candidate hair highlight clip fraction: `0.00108456`

Current v11 regression: **PASS_PRODUCTION_GATE / PASS_HAIR_FIDELITY**.

Run: `32074421708`.

## Safety Orchestrator v01

Run: `32074350891` — **SUCCESS**.

Validated controls:

1. `SR=RUN` does not execute without explicit safe-SR permission.
2. With permission, only conservative non-destructive SR becomes auto-eligible.
3. Object removal without an explicit defect mask is blocked.
4. A supplied defect mask changes removal to controlled review, not blind auto-inpainting.
5. Bokeh and tone remain visual-review stages.
6. Hair QA may run automatically after a candidate exists.

## Reusable Safe SR Executor v01

File: `tools/hair_salon_pipeline/safe_sr.py`.

Fail-closed requirements:

- explicit `--allow-safe-sr`
- Preflight decision must be `SR=RUN`
- Real-ESRGAN commit must exactly equal `fa4c8a03ae3dbc9ea6ed471a6ab5da94ac15c2ea` (`v0.3.0`)
- output must be a new file, never overwrite source
- geometry must be exactly 2x
- model weights must materialize and their SHA-256 values are recorded
- result status is `SR_EXECUTION_COMPLETE_PENDING_HAIR_QA`, never auto-promoted

CI run: `32074996668` — **SUCCESS**.

Fail-closed tests passed:

- no explicit permission -> refused
- Preflight `SR=SKIP` even with permission -> refused
- valid permission + `SR=RUN` -> execution succeeds

Pinned model configuration:

- model: `realesr-general-x4v3`
- denoise strength: `0.20`
- scale: `2`
- FP32: `true`
- tile: `128`
- tile pad: `16`
- face enhance: `false`

Recorded weight SHA-256:

- `realesr-general-wdn-x4v3.pth`: `1641f8c4464b9f097c9fdda5589273713f67cf59f3d909e0bd688f0cee269dca`
- `realesr-general-x4v3.pth`: `8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292`

Important evidence boundary: the executor CI uses the official Real-ESRGAN `v0.3.0` test asset and validates mechanics/provenance. It does **not** claim a new salon-photo fidelity validation. Real salon fidelity remains grounded in the existing verified salon SR plus Production Gate v03.

## Multi-image QA v01

Run: `32050086984` — **SUCCESS**.

Scope: images from the same client / same hairstyle-color service / same delivery set.

Validated behavior:

- consistent set -> PASS
- +12 degree hair-hue drift -> REVIEW and altered frame becomes Hero-ineligible
- strong hair-detail blur -> Hero advisory moves away from degraded frame

Hero scoring is technical/advisory only. It does not score identity, attractiveness, demographic attributes, or face beauty, and it never auto-promotes a final image.

A three-frame private salon validation also passed set-color consistency. Those private images were kept local and were not committed to the public repository. Their local masks were conservative explicit validation masks and are not claimed as dedicated-mask generalization evidence.

## Privacy boundary

Local/private-file mode remains supported. Personal source images do not need to be committed to GitHub.

The historical Adobe share URL used by the earliest SR test later returned HTTP 403. The new executor therefore does not depend on that URL for CI. Private images must not be made public merely to satisfy an automated test.

## Current production policy

1. Preflight first.
2. Router decisions are not permissions.
3. Skip unnecessary expensive stages.
4. Only non-destructive conservative SR and deterministic QA are currently auto-eligible.
5. Object removal requires explicit defect-mask authority and visual review.
6. Bokeh and tone remain visual decisions.
7. Dedicated source-derived hair masks are preferred.
8. Hair fidelity must pass before promotion.
9. Multi-image ranking is advisory only.
10. Final visual acceptance remains human-reviewed.

## Current state

The core workflow is now production-oriented and reusable rather than a one-off repair chain. No additional automatic image-editing stage is required to complete the current capability-development task. Further expansion should be justified by a real new source or a concrete failure case rather than by adding more models or rules.
