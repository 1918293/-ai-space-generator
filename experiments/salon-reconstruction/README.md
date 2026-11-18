# Salon Whole-Person Reconstruction — EXP v0.5

## Purpose
Build a repeatable way to make a Salon portrait **clearly slimmer at first glance** by treating shoulder width, upper-arm volume, ribcage/torso width, waist/abdomen, garment drape, hands/phone occlusion and newly exposed background as one reconstruction problem rather than a local liquify/warp.

This branch is EXP-only. No generated image is an Accepted Baseline unless the user explicitly promotes it.

## Canonical execution spine
There is now one free Zero-GPU runner authority:

`profiles/upper_body_strong_slim_v01.json`
→ `run_hf_zero_fast_salon.py`
→ backend comparison
→ private result
→ visual QA

`run_hf_zero_fast_salon.py` supports two fast hosted Qwen-Image-Edit-2511 backends:

- `HF_ZERO_BACKEND=lightning` → `LPX55/Qwen-Image-Edit-2511-Turbo-Lightning`
  - execution-proven on the current private Salon portrait;
  - very strong body reconstruction capability;
  - **not** trusted for garment/hand/phone preservation without further constraints.
- `HF_ZERO_BACKEND=lora_fast` → `prithivMLmods/Qwen-Image-Edit-2511-LoRAs-Fast`
  - current next comparator for garment-aware / LoRA-constrained reconstruction;
  - API contract and synthetic path validated;
  - current private portrait still requires direct comparison before promotion.

Supported input modes:

- `source_only`
- `source_garment`
- `source_garment_structure`

Supported prompt variants:

- `preserve_garment` — default; hard garment/hand/phone constraints
- `strong_keypoints` — stronger structure following
- `identity_first` — stronger source identity preservation

## Actual evidence from the current private portrait
The execution gate has been crossed. The current Salon portrait has been sent through the private encrypted GitHub Actions → Hugging Face Zero-GPU path and returned successfully.

Three Lightning tests produced high-value negative evidence:

1. **Source + garment + keypoints** — strong first-glance slimming succeeded, but the T-shirt construction and hand/phone relation drifted too far. `REJECT / HIGH INFORMATION VALUE`.
2. **Source + garment + silhouette** — the abstract silhouette was interpreted partly as appearance/content rather than a pure geometry constraint; garment identity degraded. `REJECT`.
3. **Source + garment** — removing explicit structure did not solve garment preservation. `HARD REJECT`.

### Current interpretation
The tested Lightning backend is **not equivalent to a ControlNet geometry pipeline**. A third keypoint/silhouette image cannot be assumed to act as pure structure authority. Strong body reconstruction is already possible; the present frontier is constraint quality:

- preserve original crew neckline, covered shoulders, short sleeves and untucked shirt length;
- preserve readable `CALIFORNIA / LONG BEACH / SUNSET` graphic identity;
- keep hands/phone anatomically and proportionally credible;
- rebuild garment drape and occlusion around a slimmer body instead of changing garment category;
- compare backends rather than increasing warp strength.

These findings are encoded in `profiles/upper_body_strong_slim_v01.json` revision 2.

## Private execution
### Preferred privacy-preserving lane
`.github/workflows/salon-private-zero-run.yml`

The workflow:

1. generates an ephemeral RSA transport key inside GitHub Actions;
2. publishes only the public key;
3. receives a RSA-encrypted private transport manifest;
4. downloads 1–3 short-lived private input URLs into runner temp storage;
5. executes the unified Zero runner;
6. AES-encrypts the generated result before publishing it as a one-day artifact;
7. deletes plaintext inputs/output and the ephemeral private key in `always()` cleanup.

No portrait image is committed to this public repository.

### Simpler presigned transport lane
`.github/workflows/salon-hf-zero.yml`

Requires private GitHub Secrets:

- `SALON_PACK_URL`
- `SALON_RESULT_UPLOAD_URL`

This workflow is useful when short-lived private GET/PUT URLs already exist. It now supports backend and prompt-variant selection without changing code.

## Other comparators
- `run_qwencloud_salon.py` — hosted `qwen-image-3.0-pro` fallback; requires `DASHSCOPE_API_KEY`; no local GPU.
- `run_qwen_lightx2v_fp8.sh` — self-hosted Qwen-2511 distilled/FP8 comparator.
- `run_qwen_salon.py` — self-hosted full Qwen comparator.
- `run_fitvton_salon.sh` — garment-fit comparator for slim/height targets.
- MHR / InstantHMR remains a geometry research lane; it has not been runtime-executed in this repository.

## Validation
Cheap PR validation stays in `.github/workflows/salon-runner-validation.yml` and performs compile/syntax/QwenCloud dry-run checks without spending Zero GPU capacity.

`.github/workflows/salon-hf-zero-validation.yml` is manual-only and checks the current fast Zero contracts plus one small synthetic Lightning inference when explicitly requested.

Known evidence:

- runner compile/syntax checks: `PASS`
- QwenCloud three-image dry-run payload: `PASS`
- LPX55 Lightning Zero-GPU synthetic inference: `PASS`
- private encrypted real-portrait Zero-GPU execution: `PASS`
- Lightning garment/hand/phone preservation: `INSUFFICIENT / REJECTED`
- Adobe Firefly body reconstruction: `ATTEMPTED / ACCESS DENIED`
- Picsart alternatives: `CREDIT GATED`
- GitHub-hosted local GPU: `NOT AVAILABLE`
- FitVTON / self-hosted Qwen / MHR actual inference: `NOT EXECUTED`

## Privacy boundary
Do **not** commit portrait/source/control images, decrypted manifests, result passwords or plaintext generated portrait outputs to this public repository.

Temporary encrypted transport material is not a knowledge source and should be removed after the private experiment is complete. GitHub stores code/configuration; research conclusions and accepted visual decisions remain outside the public repository.

## Next experiment
Do **not** spend more cycles on stronger 2D warp/TPS or more Lightning prompt variants of the same type.

The highest-value next comparison is:

`same private source + same garment reference`
→ `lora_fast preserve_garment`
→ compare against rejected Lightning V1/V2/V3
→ evaluate first-glance slimming, shirt construction, graphic identity, hands/phone and photographic naturalness.
