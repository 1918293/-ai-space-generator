# Hair Salon Production Pipeline v03

Status: **DEDICATED_HAIR_QA_VALIDATED / PRODUCTION_GATE_READY**  
Branch: `hao-hair-sr-test-20260817`

## What changed from v02

v03 replaces the default hair/face proxy on the verified commercial case with a **dedicated source-derived hair segmentation mask** while preserving the v02 proxy as a fallback.

The mask is generated from the source authority, not from the candidate. This prevents a changed candidate hairstyle from moving the evaluation mask with the defect.

## Dedicated hair segmentation

Implementation: `tools/hair_salon_pipeline/hair_segment_onnx.py`

Model:
- `yakhyo/face-parsing` ResNet18 ONNX
- MIT license
- CelebAMask-HQ training set
- hair class = 17
- CPU ONNXRuntime inference

The verified MODNet matte is used only for locating a permissive upper-subject crop and suppressing obvious background false positives. Hair classification itself comes from the dedicated parser.

Sanity gates before a dedicated hair mask can be used:
- non-empty hair mask
- hair area between 5% and 55% of subject area
- at least 80% of hair pixels overlap the verified subject core
- largest connected component at least 70% of total hair area

If a dedicated hair mask is unavailable, `hair_qa.py` retains the v02 MODNet upper-subject proxy fallback.

## Production integration

Updated components:
- `tools/hair_salon_pipeline/hair_segment_onnx.py`
- `tools/hair_salon_pipeline/hair_qa.py`
- `tools/hair_salon_pipeline/gate.py`
- `.github/workflows/hair-salon-production-gate-v01.yml` (workflow name: `Hao Hair Salon Production Gate v03`)

`gate.py` now accepts:

```bash
--hair-mask SOURCE_DERIVED_HAIR_MASK
```

When supplied, the dedicated mask is used for structure, edge, color, saturation, and highlight fidelity metrics. When absent, proxy fallback remains supported.

## Feasibility probe

Workflow: `Hao Hair Segmentation BiSeNet Probe v01`  
Run: `32048862925`  
Result: **SUCCESS after one retry caused by a transient GitHub artifact-backend outage.**

Probe metrics on the current salon authority/candidate family:
- hair pixels: `1,124,582`
- hair / MODNet subject fraction: `0.25010`
- hair pixels in MODNet core: `0.98109`
- largest connected component fraction: `1.0`

Visual QA confirmed useful coverage of the full visible hairstyle, including crown, side layers, and lower curled ends, while excluding most face, clothing, and background.

## v03 regression run

Workflow run: `32049215671`  
Result: **SUCCESS / PASS_PRODUCTION_GATE**

Dedicated source-mask sanity:
- status: `PASS_DEDICATED_HAIR_MASK`
- hair pixels: `1,181,124`
- hair / subject fraction: `0.2626757`
- hair pixels in MODNet core: `0.9722773`
- largest connected component fraction: `1.0`

Hair fidelity with the dedicated source mask:
- status: `PASS_HAIR_FIDELITY`
- gradient correlation: `0.99513585`
- candidate/authority edge-energy ratio: `1.00006283`
- median hue shift: `0°`
- P95 hue shift: `2°`
- median saturation shift: `1 / 255`
- candidate hair highlight clip fraction: `0.00108456`

Production route remained:

`SKIP_SR -> DEFECT_MASK_REVIEW -> BOKEH_REVIEW -> SKIP_TONE_CORRECTION -> HAIR_QA`

Hair mask mode: **DEDICATED**

## Evidence

Artifact: `hao-hair-salon-production-gate-v03`

Contains:
- `hair_mask_source_v03.png`
- `hair_segmentation_v03.json`
- `QA_hair_segmentation_v03.jpg`
- `hair_qa.json`
- `preflight.json`
- `production_decision.json`
- `PRODUCTION_DECISION.md`

## Verified / ready

- source-derived dedicated hair mask on the current verified case
- explicit sanity gates before mask promotion
- source-relative hair structure/color/highlight QA using the dedicated mask
- fallback to the v02 proxy when a dedicated mask is absent
- no change to candidate image pixels
- same one-command production gate remains usable

## Still candidate / not claimed

- dedicated parser generalization across every hair type, extreme curl, occlusion, hat, very short hair, and unusual lighting
- automatic defect-mask generation
- blind automatic execution of expensive SR/inpainting stages
- universal thresholds for every salon/camera setup
- multi-image consistency / hero-image selection

## Production rule

Prefer the dedicated source-derived hair mask when its sanity gates pass. Do not derive the authority mask from the candidate. If dedicated segmentation fails, fall back to the v02 proxy and label the run accordingly. Automated metrics still do not replace final visual acceptance.
