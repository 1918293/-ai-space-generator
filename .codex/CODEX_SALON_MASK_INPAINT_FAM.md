# Codex FAM Task — Explicit-Mask Inpainting Runtime

## Read first
Read `AGENTS.md` before doing anything.

## Context
This is a **FAM validation task**, not a production merge task. The current salon-photo workflow has already validated an explicit protected/background mask chain, but the available Adobe generative cleanup path is permission-blocked. We need to determine whether a mature open-source runtime can reliably accept an explicit edit mask, perform inpainting, preserve source dimensions, and produce measurable output suitable for later Pixel QA.

The user's private salon Source Original must **NOT** be uploaded to this public repository or copied into commits, comments, logs, fixtures, or artifacts.

## Objective
Build and validate the smallest practical open-source **explicit-mask inpainting** probe, using a synthetic local test image only.

Prefer the mature self-hosted path:
1. IOPaint with the built-in `lama` model on CPU, using its explicit image+mask batch/CLI path.
2. If IOPaint cannot install/run safely in the Codex environment, diagnose the exact blocker. You may try the official LaMa repository only if that is materially simpler after the IOPaint failure; do not broaden into multiple unrelated models.

## Constraints
- Work only on branch `fam/salon-explicit-mask-inpaint`.
- DO NOT modify `main`.
- DO NOT merge or mark the PR ready.
- DO NOT modify GitHub Actions, Secrets, deployment, repository settings, or production environment.
- DO NOT add paid services, API keys, cloud inference, or external image uploads.
- DO NOT use the user's private salon image.
- Keep changes minimal and reversible.
- Do not modify `app.js`, `index.html`, or `styles.css` unless the runtime probe has passed and a tiny integration change is strictly necessary; otherwise leave the application untouched.
- If a dependency/model cannot be fetched because of network/runtime constraints, report that exact blocker instead of fabricating success.

## Required validation fixture
Create a synthetic RGB image at **935×1685** and a matching single-channel edit mask. The fixture should contain:
- a clearly defined protected foreground region,
- a distinct background target region inside the edit mask,
- enough texture/geometry to make accidental outside-mask changes measurable.

Do not commit large binary fixtures. Generate them at runtime.

## Required execution test
Run one explicit-mask inpainting operation and retain enough local outputs for validation during the Codex run.

Then perform a deterministic source pasteback outside the edit mask (or equivalent exact compositing step) so that the final test output obeys the contract:

`final = inpainted inside Edit Mask + original outside Edit Mask`

## Hard acceptance criteria
Report PASS only if all are true:
1. Explicit source image and explicit mask were both accepted by the inpainting runtime.
2. Output width×height remains exactly **935×1685**.
3. Inpainted pixels change inside the intended edit-mask region.
4. After deterministic pasteback, `outside_edit_mask_changed_pixels == 0`.
5. After deterministic pasteback, `outside_edit_mask_max_abs_diff == 0`.
6. No resize/crop is introduced anywhere in the validated path.
7. A machine-readable JSON result records at least: runtime/model, input/output dimensions, source SHA-256, mask SHA-256, raw inpaint SHA-256, final SHA-256, changed-pixel counts inside/outside mask, max outside-mask difference, elapsed time, and PASS/FAIL.

## Minimal persistent implementation if PASS
If the runtime actually passes in this environment, keep only the minimum reusable code needed to reproduce the probe. Prefer:
- one small Python probe/runner under an existing or minimally necessary tools location,
- one small test for the exact outside-mask preservation contract,
- a concise README note describing FAM status and how to rerun.

Do not commit downloaded model weights, virtual environments, generated images, caches, or large artifacts.

If PASS cannot be achieved, do not add speculative integration code. You may leave only a concise diagnostic/task note if needed, but avoid unnecessary files.

## Validation commands
Use an isolated temporary environment where practical. Run syntax/tests appropriate to whatever minimal files you add. Include exact commands and outputs in the final PR comment.

## Final report
Reply on the PR with:
- runtime actually attempted,
- exact package/model versions if resolved,
- install/run commands,
- PASS/FAIL for each acceptance criterion,
- measured dimensions/hashes/pixel-diff metrics/timing,
- files changed,
- exact blocker(s), if any,
- whether this candidate is safe to retain as a FAM draft,
- a clear boundary: this synthetic runtime validation does **not** mean the private salon Source Original has been processed or that a Production Master exists.

If the runtime passes, remove this temporary task file before finalizing the implementation commit unless it is still needed to explain an unresolved blocker. Keep the PR Draft.