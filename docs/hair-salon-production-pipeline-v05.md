# Hair Salon Production Pipeline v05

Status: **DECISION_PACKET_READY / ONE_USER_DECISION_REMAINING**  
Branch: `hao-hair-sr-test-20260817`  
Main branch: **UNTOUCHED**

## What changed from v04

The production router is now intent-aware for object removal.

Previously, every image inherited `MANUAL_DEFECT_MASK_REQUIRED`, even when object removal was not part of the job. That produced a false permanent blocker.

New rule:

- object removal not requested -> `NOT_REQUESTED / SKIP`
- object removal explicitly requested without defect mask -> `BLOCK_NO_EXPLICIT_DEFECT_MASK`
- explicit defect mask supplied -> `CONTROLLED_REVIEW_WITH_MASK`

Object-removal intent is never inferred from generic pixel statistics.

## Decision Packet

New tool: `tools/hair_salon_pipeline/decision_packet.py`

Purpose: collapse production evidence into the minimum necessary human decision instead of exposing every internal REVIEW stage separately.

Policy:

1. Technical failures remain system investigation work and are not pushed to the user as aesthetic decisions.
2. False blockers are removed when a stage is outside declared job intent.
3. Related visual-review stages may be collapsed into final visual acceptance when the candidate already exists and technical fidelity has passed.
4. Resolved technical stages are not reopened without a new failure signal.
5. Final visual acceptance remains human.

`gate.py` now emits:

- `production_decision.json`
- `orchestration_plan.json`
- `hair_qa.json` when applicable
- `decision_packet.json`
- `DECISION_PACKET.md`

## Current v11 real-case regression

Existing source/candidate/matte/hair authorities are unchanged.

Intent-aware route:

`SKIP_SR -> SKIP_OBJECT_REMOVAL -> BOKEH_REVIEW -> SKIP_TONE_CORRECTION -> HAIR_QA`

Resolved without user:

- SR: SKIP
- Object removal: NOT REQUESTED / SKIP for the current regression job
- Tone: SKIP
- Hair QA: PASS_HAIR_FIDELITY

The current candidate already embodies the verified bokeh/background-separation treatment. Therefore `BOKEH_REVIEW` and final visual acceptance are intentionally collapsed into one decision rather than asking two overlapping questions.

## Only remaining user decision

Decision ID: `FINAL_VISUAL_ACCEPTANCE_V11`

Question:

**Accept current v11 overall commercial visual direction, including background separation/bokeh, as the completed image authority?**

If accepted:

- freeze v11 as final image authority
- keep resolved technical authorities closed
- keep Pipeline v05 as current reusable production candidate

If revision is requested:

- reopen only the named visual stage(s)
- do not rerun SR, segmentation, Hair QA, or retired inpainting research unless a new failure requires it

## Stop condition

No further autonomous iteration is justified before this visual decision. Continuing past this point would mean changing an aesthetic direction without user authority.
