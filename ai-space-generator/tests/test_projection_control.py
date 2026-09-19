from dataclasses import replace

import pytest

from src.execution_control import ExecutionRecord, Mode, RunPhase
from src.projection_control import (
    ProjectionEnvelope,
    make_projection,
    render_projection_header,
    validate_projection,
)


def record(**overrides):
    values = dict(
        run_id="RUN-PROJECTION",
        task="Projection boundary",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("projection cannot own state",),
        authority_refs=("AUTH-1",),
    )
    values.update(overrides)
    return ExecutionRecord(**values)


def test_projection_is_current_only_for_exact_source_state():
    current = record()
    projection = make_projection(current, projection_id="HANDOFF-R1")
    assert validate_projection(projection, current).allowed is True


def test_old_handoff_projection_becomes_stale_after_runtime_state_changes():
    current = record()
    projection = make_projection(current, projection_id="HANDOFF-R1")
    changed = replace(current, phase=RunPhase.BLOCKED, failure_code="NEW_STATE")
    decision = validate_projection(projection, changed)
    assert decision.allowed is False
    assert decision.code == "STALE_PROJECTION"


def test_projection_text_cannot_spoof_mode_even_with_same_source_fingerprint():
    current = record(mode=Mode.EXP)
    projection = make_projection(current, projection_id="HANDOFF-R1")
    tampered = ProjectionEnvelope(
        projection_id=projection.projection_id,
        source_run_id=projection.source_run_id,
        source_fingerprint=projection.source_fingerprint,
        mode="SYS",
        task=projection.task,
        phase=projection.phase,
        action_id=projection.action_id,
        summary=projection.summary,
    )
    decision = validate_projection(tampered, current)
    assert decision.allowed is False
    assert decision.code == "PROJECTION_MODE_MISMATCH"


def test_projection_header_renders_from_current_state_only_after_freshness_check():
    current = record(mode=Mode.EXP, task="Current task")
    projection = make_projection(current, projection_id="HANDOFF-R1")
    header = render_projection_header(
        projection,
        current,
        date="2026-08-30",
        time_with_offset="09:50+08:00",
    )
    assert header.startswith("[MODE=EXP][TASK=Current task]")

    stale_current = replace(current, task="New task")
    with pytest.raises(ValueError, match="STALE_PROJECTION"):
        render_projection_header(
            projection,
            stale_current,
            date="2026-08-30",
            time_with_offset="09:51+08:00",
        )
