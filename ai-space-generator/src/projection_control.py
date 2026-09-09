from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from .execution_control import (
    ControlDecision,
    ExecutionRecord,
    FailureStage,
    render_header,
)


@dataclass(frozen=True)
class ProjectionEnvelope:
    projection_id: str
    source_run_id: str
    source_fingerprint: str
    mode: str
    task: str
    phase: str
    action_id: str
    summary: str = ""


def state_fingerprint(record: ExecutionRecord) -> str:
    payload = json.dumps(
        asdict(record),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def make_projection(
    record: ExecutionRecord,
    *,
    projection_id: str,
    summary: str = "",
) -> ProjectionEnvelope:
    if not projection_id.strip():
        raise ValueError("PROJECTION_ID_REQUIRED")
    return ProjectionEnvelope(
        projection_id=projection_id,
        source_run_id=record.run_id,
        source_fingerprint=state_fingerprint(record),
        mode=record.mode.value,
        task=record.task,
        phase=record.phase.value,
        action_id=record.action.action_id if record.action else "",
        summary=summary,
    )


def validate_projection(
    projection: ProjectionEnvelope,
    current: ExecutionRecord,
) -> ControlDecision:
    if projection.source_run_id != current.run_id:
        return ControlDecision(False, "PROJECTION_RUN_MISMATCH", FailureStage.PROJECTION)
    if projection.source_fingerprint != state_fingerprint(current):
        return ControlDecision(False, "STALE_PROJECTION", FailureStage.PROJECTION)
    expected_action = current.action.action_id if current.action else ""
    if projection.mode != current.mode.value:
        return ControlDecision(False, "PROJECTION_MODE_MISMATCH", FailureStage.PROJECTION)
    if projection.task != current.task:
        return ControlDecision(False, "PROJECTION_TASK_MISMATCH", FailureStage.PROJECTION)
    if projection.phase != current.phase.value:
        return ControlDecision(False, "PROJECTION_PHASE_MISMATCH", FailureStage.PROJECTION)
    if projection.action_id != expected_action:
        return ControlDecision(False, "PROJECTION_ACTION_MISMATCH", FailureStage.PROJECTION)
    return ControlDecision(True, "PROJECTION_CURRENT")


def render_projection_header(
    projection: ProjectionEnvelope,
    current: ExecutionRecord,
    *,
    date: str,
    time_with_offset: str,
) -> str:
    validation = validate_projection(projection, current)
    if not validation.allowed:
        raise ValueError(validation.code)
    # Render from authoritative current state, never from projection text.
    return render_header(current, date=date, time_with_offset=time_with_offset)
