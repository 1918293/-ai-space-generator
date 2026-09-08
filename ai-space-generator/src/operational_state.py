from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import hmac
import json
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
class TaskChangeReceipt:
    """Runtime-signed proof that Hao explicitly requested a durable TASK change."""

    event_id: str
    prior_task: str
    requested_task: str
    source_text_sha256: str
    evidence_start: int
    evidence_end: int
    receipt_fingerprint: str


@dataclass(frozen=True)
class OperationalCommand:
    event_id: str
    actor: CommandActor
    text: str
    task_change_receipt: TaskChangeReceipt | None = None
    expected_version: int | None = None


@dataclass(frozen=True)
class OperationalUpdate:
    state: ActiveOperationalState
    applied: bool
    code: str


_MODE_PREFIX = re.compile(r"^\s*(EXP|FAM|EXE|INT|SYS)(?=\s|>|:|：|$)", re.IGNORECASE)
_TASK_DIRECTIVE = re.compile(
    r"^\s*(?:(?:EXP|FAM|EXE|INT|SYS)\s*(?:>|:|：)?\s*)?"
    r"(?:(?:Auto(?:\s+Loop)?|繼續)\s*(?:>|:|：)?\s*)?"
    r"(?:TASK|新\s*TASK)\s*(?:=|:|：|>)\s*(?P<task>\S(?:.*\S)?)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def explicit_user_mode(text: str) -> Mode | None:
    """Recognize only an explicit leading Hao mode command.

    `Auto`, `Auto Loop`, `繼續`, quoted headers, or a mode word later in prose do
    not match. The caller must also prove the command actor is USER.
    """
    match = _MODE_PREFIX.match(text or "")
    if not match:
        return None
    return Mode(match.group(1).upper())


def explicit_user_task(text: str) -> tuple[str, int, int] | None:
    """Return a deterministic explicit TASK directive from raw Hao text only.

    TASK changes intentionally require an explicit `TASK:`/`TASK=`/`新 TASK：`
    directive. Attachments, tool outputs, model inference, quoted headers, plain
    `Auto`/`Auto Loop`/`繼續`, and ordinary prose cannot mint a durable TASK
    transition through this parser.
    """
    match = _TASK_DIRECTIVE.fullmatch(text or "")
    if not match:
        return None
    task = match.group("task").strip()
    if not task:
        return None
    start, end = match.span("task")
    return task, start, end


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _task_change_signature_payload(
    *,
    event_id: str,
    prior_task: str,
    requested_task: str,
    source_text_sha256: str,
    evidence_start: int,
    evidence_end: int,
) -> bytes:
    payload = {
        "event_id": event_id,
        "prior_task": prior_task,
        "requested_task": requested_task,
        "source_text_sha256": source_text_sha256,
        "evidence_start": evidence_start,
        "evidence_end": evidence_end,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class TaskChangeAuthority:
    """Trusted runtime mint/verifier for explicit Hao TASK transitions.

    The caller never supplies `requested_task`. The authority derives it from
    raw USER text with `explicit_user_task`, signs the exact event/prior state/
    source text/evidence span, and the state store re-runs the same parser before
    accepting the receipt. This prevents attachments, model output, projections,
    or upstream inferred strings from becoming durable TASK changes.
    """

    def __init__(self, signing_key: bytes) -> None:
        if len(signing_key) < 32:
            raise ValueError("TASK_CHANGE_SIGNING_KEY_TOO_SHORT")
        self._signing_key = bytes(signing_key)

    def issue(
        self,
        state: ActiveOperationalState,
        *,
        event_id: str,
        actor: CommandActor,
        text: str,
    ) -> TaskChangeReceipt | None:
        if actor != CommandActor.USER:
            return None
        event_id = event_id.strip()
        if not event_id:
            raise ValueError("EVENT_ID_REQUIRED")
        parsed = explicit_user_task(text)
        if parsed is None:
            return None
        requested_task, evidence_start, evidence_end = parsed
        source_text_sha256 = _sha256_text(text)
        signature = hmac.new(
            self._signing_key,
            _task_change_signature_payload(
                event_id=event_id,
                prior_task=state.task,
                requested_task=requested_task,
                source_text_sha256=source_text_sha256,
                evidence_start=evidence_start,
                evidence_end=evidence_end,
            ),
            hashlib.sha256,
        ).hexdigest()
        return TaskChangeReceipt(
            event_id=event_id,
            prior_task=state.task,
            requested_task=requested_task,
            source_text_sha256=source_text_sha256,
            evidence_start=evidence_start,
            evidence_end=evidence_end,
            receipt_fingerprint="hmac-sha256:" + signature,
        )

    def verify(
        self,
        state: ActiveOperationalState,
        command: OperationalCommand,
        receipt: TaskChangeReceipt,
    ) -> bool:
        if command.actor != CommandActor.USER:
            return False
        if receipt.event_id.strip() != command.event_id.strip():
            return False
        if receipt.prior_task != state.task:
            return False
        if receipt.source_text_sha256 != _sha256_text(command.text):
            return False

        parsed = explicit_user_task(command.text)
        if parsed is None:
            return False
        requested_task, evidence_start, evidence_end = parsed
        if requested_task != receipt.requested_task:
            return False
        if evidence_start != receipt.evidence_start or evidence_end != receipt.evidence_end:
            return False
        if command.text[evidence_start:evidence_end] != receipt.requested_task:
            return False

        prefix = "hmac-sha256:"
        if not receipt.receipt_fingerprint.startswith(prefix):
            return False
        supplied_signature = receipt.receipt_fingerprint[len(prefix):]
        expected_signature = hmac.new(
            self._signing_key,
            _task_change_signature_payload(
                event_id=receipt.event_id.strip(),
                prior_task=receipt.prior_task,
                requested_task=receipt.requested_task,
                source_text_sha256=receipt.source_text_sha256,
                evidence_start=receipt.evidence_start,
                evidence_end=receipt.evidence_end,
            ),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(supplied_signature, expected_signature)


class SQLiteOperationalStateStore:
    """Single-writer operational state reference implementation.

    Handoff, XMemo, model output, attachments, tool results, and projections can
    read this state but cannot author Mode or TASK through this API. Mode changes
    require an explicit leading Hao Mode command. TASK changes additionally
    require a runtime-signed `TaskChangeReceipt` minted from explicit raw Hao
    text; a naked caller-supplied task string is not part of this API.
    """

    def __init__(
        self,
        path: str,
        *,
        task_change_authority: TaskChangeAuthority | None = None,
    ) -> None:
        self._path = path
        self._task_change_authority = task_change_authority
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
                    code TEXT NOT NULL,
                    task_change_receipt_fingerprint TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(operational_events)").fetchall()
            }
            if "task_change_receipt_fingerprint" not in columns:
                conn.execute(
                    "ALTER TABLE operational_events "
                    "ADD COLUMN task_change_receipt_fingerprint TEXT NOT NULL DEFAULT ''"
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

            requested_mode = explicit_user_mode(command.text) if command.actor == CommandActor.USER else None
            explicit_task_directive = (
                explicit_user_task(command.text) if command.actor == CommandActor.USER else None
            )

            next_mode = current.mode
            next_task = current.task
            code = "NO_OPERATIONAL_CHANGE"
            verified_receipt_fingerprint = ""

            if command.actor != CommandActor.USER and command.task_change_receipt is not None:
                code = "NON_USER_TASK_CHANGE_IGNORED"
            elif command.actor == CommandActor.USER:
                receipt = command.task_change_receipt
                if explicit_task_directive is not None and receipt is None:
                    code = "USER_TASK_CHANGE_RECEIPT_REQUIRED"
                    requested_mode = None
                elif receipt is not None:
                    if self._task_change_authority is None:
                        code = "TASK_CHANGE_AUTHORITY_REQUIRED"
                        requested_mode = None
                    elif not self._task_change_authority.verify(current, command, receipt):
                        code = "TASK_CHANGE_RECEIPT_INVALID"
                        requested_mode = None
                    else:
                        next_task = receipt.requested_task
                        verified_receipt_fingerprint = receipt.receipt_fingerprint
                        if requested_mode is not None:
                            next_mode = requested_mode
                            code = "USER_MODE_AND_TASK_RECEIPT_APPLIED"
                        else:
                            code = "USER_TASK_RECEIPT_APPLIED"
                elif requested_mode is not None:
                    next_mode = requested_mode
                    code = "USER_MODE_COMMAND_APPLIED"

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
                    event_id,
                    actor,
                    command_text,
                    resulting_version,
                    applied,
                    code,
                    task_change_receipt_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    command.actor.value,
                    command.text,
                    next_version,
                    int(changed),
                    code,
                    verified_receipt_fingerprint,
                ),
            )
            conn.execute("COMMIT")
            return OperationalUpdate(
                ActiveOperationalState(
                    next_mode,
                    next_task,
                    next_version,
                    event_id if changed else current.last_event_id,
                ),
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
