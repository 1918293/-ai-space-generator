from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import sqlite3
import time
from typing import Mapping, Protocol


SCOPE_ACCESS = "hao:access"
SCOPE_READ = "hao:read"
SCOPE_EXECUTE = "hao:execute"
SCOPE_APPROVE = "hao:approve"


class Permission(StrEnum):
    ACCESS = "access"
    READ = "read"
    EXECUTE = "execute"
    APPROVE = "approve"


_PERMISSION_SCOPE = {
    Permission.ACCESS: SCOPE_ACCESS,
    Permission.READ: SCOPE_READ,
    Permission.EXECUTE: SCOPE_EXECUTE,
    Permission.APPROVE: SCOPE_APPROVE,
}


class AuthorityActor(StrEnum):
    HUMAN = "HUMAN"
    MODEL = "MODEL"
    SYSTEM = "SYSTEM"
    PROJECTION = "PROJECTION"


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    scopes: frozenset[str]
    expires_at: int
    token_id: str
    raw_token: str = ""


@dataclass(frozen=True)
class AuthoritySnapshot:
    mode: str
    task: str
    operational_version: int
    authority_refs: tuple[str, ...]
    authority_versions: tuple[str, ...]
    projection_ref: str = ""

    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "mode": self.mode,
                "task": self.task,
                "operational_version": self.operational_version,
                "authority_refs": list(self.authority_refs),
                "authority_versions": list(self.authority_versions),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return "sha256:" + sha256(payload).hexdigest()

    def require_fresh(self, current: "AuthoritySnapshot") -> None:
        if self.fingerprint() != current.fingerprint():
            raise PermissionError("STALE_AUTHORITY_SNAPSHOT")


@dataclass(frozen=True)
class TaskPolicy:
    task: str
    authority_sources: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    required_gates: tuple[str, ...]
    hao_acceptance_required: bool
    required_assurance_tags: tuple[str, ...] = ()
    forbidden_assurance_tags: tuple[str, ...] = ()


class TaskPolicyProvider(Protocol):
    def get(self, task: str) -> TaskPolicy: ...


class ConfiguredTaskPolicyProvider:
    def __init__(self, raw_json: str) -> None:
        self._policies = _parse_task_policies(raw_json)

    def get(self, task: str) -> TaskPolicy:
        key = task.strip()
        if not key or key not in self._policies:
            raise PermissionError("TASK_POLICY_NOT_CONFIGURED")
        return self._policies[key]


@dataclass(frozen=True)
class DeploymentAuthorityContracts:
    sheets_targets: Mapping[str, Mapping[str, object]]
    task_policies: Mapping[str, TaskPolicy]
    parent_task_plans: Mapping[str, Mapping[str, object]]

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "DeploymentAuthorityContracts":
        sheets = _required_json_object(values, "HAO_SHEETS_TARGETS_JSON")
        policies_raw = str(values.get("HAO_TASK_POLICIES_JSON", "")).strip()
        if not policies_raw:
            raise ValueError("MISSING_CONFIG:HAO_TASK_POLICIES_JSON")
        policies = _parse_task_policies(policies_raw)
        parents = _required_json_object(values, "HAO_PARENT_TASK_PLANS_JSON")
        _validate_sheets_targets(sheets)
        _validate_parent_plans(parents)
        return cls(sheets, policies, parents)


class SQLiteIdentitySecurityStore:
    """Durable semantic identity/replay state without persisting bearer credentials."""

    def __init__(self, path: str) -> None:
        self._path = path
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS identity_sessions (
                    session_id TEXT PRIMARY KEY,
                    owner_subject TEXT NOT NULL,
                    token_fingerprint TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_owners (
                    run_id TEXT PRIMARY KEY,
                    owner_subject TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS replay_ledger (
                    replay_kind TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    owner_subject TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    authority_fingerprint TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(replay_kind, artifact_id)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def bind_session(self, session_id: str, identity: VerifiedIdentity) -> None:
        session_id = session_id.strip()
        if not session_id or not identity.subject.strip() or not identity.token_id.strip():
            raise ValueError("INVALID_IDENTITY_SESSION")
        token_fp = sha256(identity.token_id.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_subject, token_fingerprint FROM identity_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is not None:
                if row["owner_subject"] != identity.subject or row["token_fingerprint"] != token_fp:
                    raise PermissionError("SESSION_IDENTITY_CONFLICT")
                return
            conn.execute(
                "INSERT INTO identity_sessions(session_id, owner_subject, token_fingerprint, created_at) VALUES (?, ?, ?, ?)",
                (session_id, identity.subject, token_fp, int(time.time())),
            )

    def require_session(self, session_id: str, subject: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_subject FROM identity_sessions WHERE session_id = ?",
                (session_id.strip(),),
            ).fetchone()
        if row is None:
            raise PermissionError("UNKNOWN_IDENTITY_SESSION")
        if row["owner_subject"] != subject.strip():
            raise PermissionError("SESSION_OWNER_MISMATCH")

    def bind_run(self, run_id: str, owner_subject: str) -> None:
        run_id, owner_subject = run_id.strip(), owner_subject.strip()
        if not run_id or not owner_subject:
            raise ValueError("RUN_OWNER_REQUIRED")
        with self._connect() as conn:
            row = conn.execute("SELECT owner_subject FROM run_owners WHERE run_id = ?", (run_id,)).fetchone()
            if row is not None:
                if row["owner_subject"] != owner_subject:
                    raise PermissionError("RUN_OWNER_CONFLICT")
                return
            conn.execute("INSERT INTO run_owners(run_id, owner_subject) VALUES (?, ?)", (run_id, owner_subject))

    def require_run_owner(self, run_id: str, owner_subject: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT owner_subject FROM run_owners WHERE run_id = ?", (run_id.strip(),)).fetchone()
        if row is None:
            raise PermissionError("UNKNOWN_RUN_IDENTITY")
        if row["owner_subject"] != owner_subject.strip():
            raise PermissionError("RUN_OWNER_MISMATCH")

    def register_one_time_artifact(
        self,
        *,
        replay_kind: str,
        artifact_id: str,
        owner_subject: str,
        scope: str,
        authority_fingerprint: str,
    ) -> None:
        fields = [replay_kind, artifact_id, owner_subject, scope, authority_fingerprint]
        if any(not str(item).strip() for item in fields):
            raise ValueError("REPLAY_ARTIFACT_FIELDS_REQUIRED")
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO replay_ledger(replay_kind, artifact_id, owner_subject, scope, authority_fingerprint) VALUES (?, ?, ?, ?, ?)",
                    tuple(str(item).strip() for item in fields),
                )
        except sqlite3.IntegrityError as exc:
            raise PermissionError("REPLAY_ARTIFACT_ALREADY_REGISTERED") from exc

    def consume_one_time_artifact(
        self,
        *,
        replay_kind: str,
        artifact_id: str,
        owner_subject: str,
        scope: str,
        authority_fingerprint: str,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM replay_ledger WHERE replay_kind = ? AND artifact_id = ?",
                (replay_kind.strip(), artifact_id.strip()),
            ).fetchone()
            if row is None:
                raise PermissionError("REPLAY_ARTIFACT_UNKNOWN")
            if row["consumed"]:
                raise PermissionError("REPLAY_ARTIFACT_ALREADY_CONSUMED")
            if row["owner_subject"] != owner_subject.strip():
                raise PermissionError("REPLAY_ARTIFACT_OWNER_MISMATCH")
            if row["scope"] != scope.strip():
                raise PermissionError("REPLAY_ARTIFACT_SCOPE_MISMATCH")
            if row["authority_fingerprint"] != authority_fingerprint.strip():
                raise PermissionError("REPLAY_ARTIFACT_STALE_AUTHORITY")
            conn.execute(
                "UPDATE replay_ledger SET consumed = 1 WHERE replay_kind = ? AND artifact_id = ? AND consumed = 0",
                (replay_kind.strip(), artifact_id.strip()),
            )
            if conn.total_changes != 1:
                raise PermissionError("REPLAY_ARTIFACT_CONSUME_RACE")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def stored_session_material(self, session_id: str) -> tuple[str, str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_subject, token_fingerprint FROM identity_sessions WHERE session_id = ?",
                (session_id.strip(),),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return row["owner_subject"], row["token_fingerprint"]


class HaoSemanticSecurityPolicy:
    def __init__(self, expected_subject: str, store: SQLiteIdentitySecurityStore) -> None:
        self._expected_subject = expected_subject.strip()
        if not self._expected_subject:
            raise ValueError("EXPECTED_HAO_SUBJECT_REQUIRED")
        self._store = store
        self._revoked_token_ids: set[str] = set()

    def revoke(self, token_id: str) -> None:
        token_id = token_id.strip()
        if not token_id:
            raise ValueError("TOKEN_ID_REQUIRED")
        self._revoked_token_ids.add(token_id)

    def require_identity(
        self,
        identity: VerifiedIdentity,
        permission: Permission,
        *,
        now_epoch: int,
        session_id: str = "",
        run_id: str = "",
    ) -> None:
        if identity.subject.strip() != self._expected_subject:
            raise PermissionError("HAO_IDENTITY_REQUIRED")
        if identity.expires_at <= int(now_epoch):
            raise PermissionError("TOKEN_EXPIRED")
        if not identity.token_id.strip() or identity.token_id in self._revoked_token_ids:
            raise PermissionError("TOKEN_REVOKED_OR_UNIDENTIFIED")
        if SCOPE_ACCESS not in identity.scopes:
            raise PermissionError(f"MISSING_SCOPE:{SCOPE_ACCESS}")
        required = _PERMISSION_SCOPE[permission]
        if required != SCOPE_ACCESS and required not in identity.scopes:
            raise PermissionError(f"MISSING_SCOPE:{required}")
        if session_id:
            self._store.require_session(session_id, identity.subject)
        if run_id:
            self._store.require_run_owner(run_id, identity.subject)

    @staticmethod
    def require_human_authority(actor: AuthorityActor) -> None:
        if actor != AuthorityActor.HUMAN:
            raise PermissionError("HUMAN_AUTHORITY_REQUIRED")


def _required_json_object(values: Mapping[str, str], key: str) -> dict[str, object]:
    raw = str(values.get(key, "")).strip()
    if not raw:
        raise ValueError(f"MISSING_CONFIG:{key}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MALFORMED_CONFIG:{key}") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError(f"CONFIG_OBJECT_REQUIRED:{key}")
    return parsed


def _as_str_tuple(value: object, code: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(code)
    result = tuple(str(item).strip() for item in value if str(item).strip())
    if not allow_empty and not result:
        raise ValueError(code)
    return result


def _parse_task_policies(raw_json: str) -> dict[str, TaskPolicy]:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError("MALFORMED_CONFIG:HAO_TASK_POLICIES_JSON") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("CONFIG_OBJECT_REQUIRED:HAO_TASK_POLICIES_JSON")
    result: dict[str, TaskPolicy] = {}
    for key, raw in parsed.items():
        task_key = str(key).strip()
        if not task_key or not isinstance(raw, dict):
            raise ValueError("TASK_POLICY_OBJECT_REQUIRED")
        declared_task = str(raw.get("task", task_key)).strip()
        if declared_task != task_key:
            raise ValueError("TASK_POLICY_KEY_TASK_MISMATCH")
        result[task_key] = TaskPolicy(
            task=task_key,
            authority_sources=_as_str_tuple(raw.get("authority_sources"), "TASK_POLICY_AUTHORITY_SOURCES_REQUIRED"),
            acceptance_criteria=_as_str_tuple(raw.get("acceptance_criteria"), "TASK_POLICY_ACCEPTANCE_CRITERIA_REQUIRED"),
            required_gates=_as_str_tuple(raw.get("required_gates"), "TASK_POLICY_REQUIRED_GATES_REQUIRED"),
            hao_acceptance_required=bool(raw.get("hao_acceptance_required", False)),
            required_assurance_tags=_as_str_tuple(raw.get("required_assurance_tags", []), "TASK_POLICY_REQUIRED_ASSURANCE_TAGS_INVALID", allow_empty=True),
            forbidden_assurance_tags=_as_str_tuple(raw.get("forbidden_assurance_tags", []), "TASK_POLICY_FORBIDDEN_ASSURANCE_TAGS_INVALID", allow_empty=True),
        )
    return result


def _validate_sheets_targets(parsed: Mapping[str, object]) -> None:
    for binding_id, raw in parsed.items():
        if not str(binding_id).strip() or not isinstance(raw, dict):
            raise ValueError("SHEETS_TARGET_OBJECT_REQUIRED")
        if not str(raw.get("spreadsheet_id", "")).strip() or not str(raw.get("range_a1", "")).strip():
            raise ValueError("SHEETS_TARGET_FIELDS_REQUIRED")


def _validate_parent_plans(parsed: Mapping[str, object]) -> None:
    for plan_id, raw in parsed.items():
        if not str(plan_id).strip() or not isinstance(raw, dict):
            raise ValueError("PARENT_PLAN_OBJECT_REQUIRED")
        if not str(raw.get("task", "")).strip():
            raise ValueError("PARENT_PLAN_TASK_REQUIRED")
        children = raw.get("children")
        if not isinstance(children, list) or not children:
            raise ValueError("PARENT_PLAN_CHILDREN_REQUIRED")
        for child in children:
            if not isinstance(child, dict):
                raise ValueError("PARENT_PLAN_CHILD_OBJECT_REQUIRED")
            for field in ("slot_id", "requested_capability", "binding_id", "authorization_target"):
                if not str(child.get(field, "")).strip():
                    raise ValueError(f"PARENT_PLAN_CHILD_FIELD_REQUIRED:{field}")
