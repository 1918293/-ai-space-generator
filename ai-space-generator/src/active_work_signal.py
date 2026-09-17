from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


SIGNAL_HEADER = "HAO_ACTIVE_WORK_SIGNAL_V1"
MAX_SLOTS = 4

_REQUIRED_CONTROL_VALUES = {
    "ROLE": "NON_AUTHORITY_REBUILDABLE_RUNTIME_SIGNAL",
    "PURPOSE": "CROSS_CHAT_ACTIVE_WORK_COORDINATION_ONLY",
    "FORMAL_AUTHORITY": "GOOGLE_DRIVE_HAO_SYSTEM",
    "HISTORY": "NONE",
    "PRIVATE_PAYLOAD": "DENY",
    "TASK_DATABASE": "NO",
    "WRITE_CONTROL": "GOOGLE_DOC_REVISION_CAS",
    "MAX_SLOTS": str(MAX_SLOTS),
    "TTL_REQUIRED": "TRUE",
    "FULL_CAPACITY": "FAIL_CLOSED_OR_WAIT",
}

_SLOT_FIELDS = (
    "STATUS",
    "GENERATION",
    "RUN_KEY",
    "OBJECTIVE",
    "TARGET",
    "EXECUTION_LANE",
    "WORK_STATE",
    "STARTED_AT",
    "UPDATED_AT",
    "EXPIRES_AT",
    "EXPECTED_DELTA",
    "READBACK_STATE",
    "OWNER",
)


class ActiveWorkStatus(StrEnum):
    EMPTY = "EMPTY"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class ActiveWorkSlot:
    slot_id: str
    status: ActiveWorkStatus
    generation: int
    run_key: str
    objective: str
    target: str
    execution_lane: str
    work_state: str
    started_at: datetime | None
    updated_at: datetime | None
    expires_at: datetime | None
    expected_delta: str
    readback_state: str
    owner: str

    def is_live(self, now: datetime) -> bool:
        _require_aware(now, "NOW")
        return (
            self.status == ActiveWorkStatus.ACTIVE
            and self.expires_at is not None
            and now < self.expires_at
        )

    @property
    def ref(self) -> str:
        if self.status != ActiveWorkStatus.ACTIVE:
            raise ValueError("ACTIVE_WORK_REF_REQUIRES_ACTIVE_SLOT")
        return f"ACTIVE_WORK:{self.slot_id}:G{self.generation}:{self.run_key}"


@dataclass(frozen=True)
class ActiveWorkSignal:
    slots: tuple[ActiveWorkSlot, ...]

    def live_same_objective(
        self,
        objective: str,
        *,
        now: datetime,
    ) -> tuple[ActiveWorkSlot, ...]:
        objective = objective.strip()
        if not objective:
            raise ValueError("ACTIVE_WORK_OBJECTIVE_REQUIRED")
        _require_aware(now, "NOW")
        return tuple(
            slot
            for slot in self.slots
            if slot.objective == objective and slot.is_live(now)
        )


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"ACTIVE_WORK_{field}_TIMEZONE_REQUIRED")


def _parse_time(raw: str, field: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"ACTIVE_WORK_{field}_TIMESTAMP_INVALID") from exc
    _require_aware(value, field)
    return value


def _parse_pairs(text: str) -> dict[str, str]:
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if not lines or lines[0] != SIGNAL_HEADER:
        raise ValueError("ACTIVE_WORK_SIGNAL_HEADER_INVALID")

    values: dict[str, str] = {}
    for line in lines[1:]:
        if "=" not in line:
            raise ValueError("ACTIVE_WORK_SIGNAL_LINE_INVALID")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError("ACTIVE_WORK_SIGNAL_PAIR_INVALID")
        if key in values:
            raise ValueError("ACTIVE_WORK_SIGNAL_DUPLICATE_KEY")
        values[key] = value
    return values


def _validate_controls(values: dict[str, str]) -> None:
    for key, expected in _REQUIRED_CONTROL_VALUES.items():
        if values.get(key) != expected:
            raise ValueError(f"ACTIVE_WORK_SIGNAL_CONTROL_INVALID:{key}")


def _slot(values: dict[str, str], index: int) -> ActiveWorkSlot:
    prefix = f"S{index}_"
    raw: dict[str, str] = {}
    for field in _SLOT_FIELDS:
        key = f"{prefix}{field}"
        if key not in values:
            raise ValueError(f"ACTIVE_WORK_SIGNAL_SLOT_FIELD_MISSING:{key}")
        raw[field] = values[key]

    try:
        status = ActiveWorkStatus(raw["STATUS"])
    except ValueError as exc:
        raise ValueError(f"ACTIVE_WORK_SIGNAL_STATUS_INVALID:S{index}") from exc

    try:
        generation = int(raw["GENERATION"])
    except ValueError as exc:
        raise ValueError(f"ACTIVE_WORK_SIGNAL_GENERATION_INVALID:S{index}") from exc
    if generation < 0:
        raise ValueError(f"ACTIVE_WORK_SIGNAL_GENERATION_INVALID:S{index}")

    if status == ActiveWorkStatus.EMPTY:
        nonempty = [
            field
            for field in _SLOT_FIELDS
            if field not in {"STATUS", "GENERATION"} and raw[field] != "NONE"
        ]
        if nonempty:
            raise ValueError(f"ACTIVE_WORK_EMPTY_SLOT_NOT_CLEARED:S{index}")
        return ActiveWorkSlot(
            slot_id=f"S{index}",
            status=status,
            generation=generation,
            run_key="NONE",
            objective="NONE",
            target="NONE",
            execution_lane="NONE",
            work_state="NONE",
            started_at=None,
            updated_at=None,
            expires_at=None,
            expected_delta="NONE",
            readback_state="NONE",
            owner="NONE",
        )

    if generation < 1:
        raise ValueError(f"ACTIVE_WORK_ACTIVE_GENERATION_REQUIRED:S{index}")
    required_active = (
        "RUN_KEY",
        "OBJECTIVE",
        "TARGET",
        "EXECUTION_LANE",
        "WORK_STATE",
        "STARTED_AT",
        "UPDATED_AT",
        "EXPIRES_AT",
        "EXPECTED_DELTA",
        "READBACK_STATE",
        "OWNER",
    )
    if any(raw[field] == "NONE" for field in required_active):
        raise ValueError(f"ACTIVE_WORK_ACTIVE_SLOT_INCOMPLETE:S{index}")

    started_at = _parse_time(raw["STARTED_AT"], f"S{index}_STARTED_AT")
    updated_at = _parse_time(raw["UPDATED_AT"], f"S{index}_UPDATED_AT")
    expires_at = _parse_time(raw["EXPIRES_AT"], f"S{index}_EXPIRES_AT")
    if started_at > updated_at or updated_at >= expires_at:
        raise ValueError(f"ACTIVE_WORK_ACTIVE_TIMELINE_INVALID:S{index}")

    return ActiveWorkSlot(
        slot_id=f"S{index}",
        status=status,
        generation=generation,
        run_key=raw["RUN_KEY"],
        objective=raw["OBJECTIVE"],
        target=raw["TARGET"],
        execution_lane=raw["EXECUTION_LANE"],
        work_state=raw["WORK_STATE"],
        started_at=started_at,
        updated_at=updated_at,
        expires_at=expires_at,
        expected_delta=raw["EXPECTED_DELTA"],
        readback_state=raw["READBACK_STATE"],
        owner=raw["OWNER"],
    )


def parse_active_work_signal(text: str) -> ActiveWorkSignal:
    values = _parse_pairs(text)
    _validate_controls(values)

    expected_keys = set(_REQUIRED_CONTROL_VALUES)
    for index in range(1, MAX_SLOTS + 1):
        expected_keys.update(f"S{index}_{field}" for field in _SLOT_FIELDS)
    unexpected = set(values) - expected_keys
    if unexpected:
        raise ValueError("ACTIVE_WORK_SIGNAL_UNEXPECTED_FIELD")

    return ActiveWorkSignal(
        slots=tuple(_slot(values, index) for index in range(1, MAX_SLOTS + 1))
    )
