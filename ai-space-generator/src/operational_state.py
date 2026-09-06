from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
import sqlite3

from .execution_control import ExecutionRecord, Mode


class CommandActor(StrEnum):
    USER = "USER"
    MODEL = "MODEL"
    SYSTEM = "SYSTEM"
    PROJECTION = "PROJECTION"


@dataclass(frozen=True)
class ActiveOperationalState:
    mode: Mode
    task: str
    version: int
    last_event_id: str = ""


@dataclass(frozen=True)
class OperationalCommand:
    event_id: str
    actor: CommandActor
    text: str
    explicit_task: str = ""
    expected_version: int | None = None


@dataclass(frozen=True)
class OperationalUpdate:
    state: ActiveOperationalState
    applied: bool
    code: str


_MODE_PREFIX = re.compile(r"^\s*(EXP|FAM|EXE|INT|SYS)(?=\s|>|:|$)", re.IGNORECASE)


def explicit_user_mode(text: str) -> Mode | None:
    """Recognize only an explicit leading Hao mode command.

    `Auto`, `Auto Loop`, `繼續`, quoted headers, or a mode word later in prose do
    not match. The caller must also prove the command actor is USER.
    """
    match = _MODE_PREFIX.match(text or "")
    if not match:
        return None
    return Mode(match.group(1).upper())


class SQLiteOperationalStateStore:
    """Single-writer operational state reference implementation.

    Handoff, XMemo, model output, and projections can read this state but cannot
    author Mode or TASK through this API. TASK changes require an explicit
    user-authoritative task field from the interaction/control boundary.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operational_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    mode TEXT NOT NULL,
                    task TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    last_event_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operational_events (
                    event_id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    command_text TEXT NOT NULL,
                    resulting_version INTEGER NOT NULL,
                    applied INTEGER NOT NULL,
                    code TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self, *, mode: Mode, task: str) -> ActiveOperationalState:
        if not task.strip():
            raise ValueError("TASK_REQUIRED")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT mode, task, version, last_event_id FROM operational_state WHERE singleton = 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO operational_state(singleton, mode, task, version) VALUES (1, ?, ?, 1)",
                    (mode.value, task.strip()),
                )
                conn.execute("COMMIT")
                return ActiveOperationalState(mode, task.strip(), 1)
            conn.execute("COMMIT")
            return ActiveOperationalState(
                Mode(row["mode"]),
                row["task"],
                row["version"],
                row["last_event_id"],
            )

    def get(self) -> ActiveOperationalState:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT mode, task, version, last_event_id FROM operational_state WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise ValueError("OPERATIONAL_STATE_NOT_INITIALIZED")
        return ActiveOperationalState(
            Mode(row["mode"]),
            row["task"],
            row["version"],
            row["last_event_id"],
        )

    def apply(self, command: OperationalCommand) -> OperationalUpdate:
        event_id = command.event_id.strip()
        if not event_id:
            raise ValueError("EVENT_ID_REQUIRED")

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            prior_event = conn.execute(
                "SELECT resulting_version, applied, code FROM operational_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if prior_event is not None:
                row = conn.execute(
                    "SELECT mode, task, version, last_event_id FROM operational_state WHERE singleton = 1"
                ).fetchone()
                conn.execute("COMMIT")
                return OperationalUpdate(
                    ActiveOperationalState(
                        Mode(row["mode"]),
                        row["task"],
                        row["version"],
                        row["last_event_id"],
                    ),
                    bool(prior_event["applied"]),
                    "EVENT_ALREADY_APPLIED",
                )

            row = conn.execute(
                "SELECT mode, task, version, last_event_id FROM operational_state WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise ValueError("OPERATIONAL_STATE_NOT_INITIALIZED")

            current = ActiveOperationalState(
                Mode(row["mode"]),
                row["task"],
                row["version"],
                row["last_event_id"],
            )
            if command.expected_version is not None and command.expected_version != current.version:
                conn.execute("ROLLBACK")
                return OperationalUpdate(current, False, "STALE_OPERATIONAL_STATE")

            next_mode = current.mode
            next_task = current.task
            code = "NO_OPERATIONAL_CHANGE"

            if command.actor == CommandActor.USER:
                requested = explicit_user_mode(command.text)
                if requested is not None:
                    next_mode = requested
                    code = "USER_MODE_COMMAND_APPLIED"
                if command.explicit_task.strip():
                    next_task = command.explicit_task.strip()
                    code = "USER_TASK_COMMAND_APPLIED" if requested is None else "USER_MODE_AND_TASK_APPLIED"
            elif command.explicit_task.strip():
                code = "NON_USER_TASK_CHANGE_IGNORED"

            changed = next_mode != current.mode or next_task != current.task
            next_version = current.version + 1 if changed else current.version
            if changed:
                conn.execute(
                    """
                    UPDATE operational_state
                    SET mode = ?, task = ?, version = ?, last_event_id = ?
                    WHERE singleton = 1 AND version = ?
                    """,
                    (next_mode.value, next_task, next_version, event_id, current.version),
                )
                if conn.total_changes < 1:
                    raise RuntimeError("OPERATIONAL_STATE_CAS_FAILED")

            conn.execute(
                """
                INSERT INTO operational_events(
                    event_id, actor, command_text, resulting_version, applied, code
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    command.actor.value,
                    command.text,
                    next_version,
                    int(changed),
                    code,
                ),
            )
            conn.execute("COMMIT")
            return OperationalUpdate(
                ActiveOperationalState(next_mode, next_task, next_version, event_id if changed else current.last_event_id),
                changed,
                code,
            )
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()


def execution_record_from_operational_state(
    state: ActiveOperationalState,
    *,
    run_id: str,
    goal_valid: bool,
    acceptance_criteria: tuple[str, ...],
    **kwargs,
) -> ExecutionRecord:
    """Create a run using runtime-owned Mode/TASK, never model-authored values."""
    return ExecutionRecord(
        run_id=run_id,
        task=state.task,
        mode=state.mode,
        goal_valid=goal_valid,
        acceptance_criteria=acceptance_criteria,
        **kwargs,
    )
