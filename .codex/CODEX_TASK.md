# Codex Task — Chenghe428 v0.3 recovery

Execute on branch `auto/chenghe428-v0.3`.

## Current verified state

- Repository: `1918293/-ai-space-generator`
- Required base: `main` at `c79d282aaed20f170f6157bdd61a36d28c7729c2`
- The earlier v0.2→v0.3 patch was prepared against a richer v0.2 working tree that is not present in the current repository.
- Current `main` is the simpler root implementation: it still downsizes to 1600 px, has only one mask, generates from the previous result, and has no Run Manifest / Pixel QA state.
- Therefore: **do not try to force-apply the historical v0.2 patch. Rebase the intended v0.3 invariants onto current `main`.**

## Safety / scope

Read and follow `AGENTS.md` first.

Only modify these existing application files:

- `app.js`
- `index.html`
- `styles.css`

Do not modify GitHub Actions/workflows, secrets, production deployment, repository structure, service worker behavior, Python code, or unrelated files.

Keep `.codex/DO_NOT_MERGE.md` in place while validation is incomplete. Do not open or merge a PR while the original handoff validation is unavailable.

## Required v0.3 invariants

Implement the smallest current-main-compatible change set that provides all of the following:

1. **Full-resolution backing store**
   - Remove the forced 1600 px processing downscale.
   - Canvas backing dimensions equal the uploaded image's natural dimensions.
   - CSS may scale the visible canvas responsively; backing pixels must remain original resolution.

2. **Immutable source per candidate**
   - Preserve an immutable `state.original`.
   - Every candidate must be generated from `state.original`, never from the previous candidate.

3. **Edit Mask + Protected Mask**
   - Keep separate backing canvases / pixel buffers for editable and protected regions.
   - Provide explicit UI tools for painting/erasing both masks.

4. **Frozen Run Snapshot**
   - At candidate creation, freeze copies of Edit Mask and Protected Mask.
   - Later live-mask edits must not mutate the active candidate's frozen masks or QA basis.

5. **SHA-256 + manifest schema 1.1**
   - Compute SHA-256 for binary frozen Edit/Protected masks.
   - Manifest must use `schema_version: "1.1"` and include mask revision + both hashes.
   - Keep source dimensions and immutable-source constraint in the manifest.

6. **QA lifecycle**
   - Each new candidate resets Visual QA to `PENDING`.
   - Pixel QA must independently verify:
     - dimensions preserved;
     - no changed pixels outside Edit Mask;
     - no changed pixels in Protected Mask;
     - zero Edit/Protected overlap.
   - Overall PASS requires Pixel QA PASS **and** Visual QA PASS.

7. **Overlap pre-flight**
   - If Edit Mask and Protected Mask overlap, fail before creating a new run/candidate.
   - Do not overwrite the previous valid active run with a failed pre-flight attempt.

8. **Atomic undo**
   - Undo restores candidate image + active run + QA + manifest + the run's frozen masks together.

9. **Continuous mobile strokes**
   - Use Pointer Events only for mask drawing.
   - Connect pointer samples with line segments and pointer capture where supported.
   - Do not register duplicate touch + pointer drawing paths.

10. **Truthful output naming**
    - Output filename contains `validated` only when Overall QA is PASS.
    - Otherwise use `candidate`.

## UI minimum

Keep the existing application structure and visual language. Add only what is required for the invariants:

- four mask tools: Edit paint / Edit erase / Protected paint / Protected erase;
- clear Edit / clear Protected / clear all;
- Protected Mask overlay canvas;
- Visual QA selector (`PENDING`, `PASS`, `FAIL`);
- compact QA result panel;
- download buttons for candidate/validated PNG, Manifest JSON, frozen Edit Mask, frozen Protected Mask.

Do not redesign unrelated UI.

## Validation

Before committing application changes, run at minimum:

```bash
node --check app.js
git diff --check
git status --short
git diff -- app.js index.html styles.css
```

Also verify by inspection/test that:

- no `fitDimensions(...1600...)` processing path remains;
- only Pointer Events drive mask drawing;
- repeated candidate generation is based on immutable original pixels;
- live mask edits do not alter frozen run hashes/masks;
- a new candidate always returns Visual QA to `PENDING`;
- overlap pre-flight creates no new run;
- Undo restores the whole snapshot;
- output naming follows Overall QA.

### Original self-test boundary

The handoff referenced `/workspace/runtime_selftest.js` with an expected `19/19 PASS`, but that file is not currently present in the repository or accessible task artifacts.

- If the original `runtime_selftest.js` is present in the Codex runtime, run it and require **19/19 PASS**.
- If it is absent, report **ORIGINAL_SELFTEST_MISSING**. Do not invent a pass result.
- A replacement/recovery self-test may be used as additional evidence, but must be labelled as a recovery test and must not be represented as the original 19/19 artifact.

## Commit / branch policy

If syntax, diff, and recovery/invariant checks pass, you may commit the three application files to this branch as an **implementation candidate** with:

```text
Implement v0.3 recovery candidate
```

Do not push to `main`.
Do not remove the do-not-merge guard.
Do not open/merge the final PR until the original self-test boundary is resolved or Hao explicitly accepts an alternative validation path.

## Stop conditions

Stop and report rather than forcing completion if:

- current `main` no longer matches the verified base in a conflicting way;
- any required invariant cannot be implemented without unrelated repository changes;
- syntax/diff/invariant validation fails;
- unexpected files would be modified;
- original self-test is absent when final promotion is requested;
- Actions, secrets, repository restructuring, paid services, or production deployment would be required.
