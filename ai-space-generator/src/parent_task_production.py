from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from typing import Any, Callable, Iterable, Iterator, Mapping
import uuid

from .action_catalog import ModelActionIntent
from .control_gateway import ModelIngressRequest
from .execution_control import Mode, RunPhase
from .operational_state import ActiveOperationalState
from .production_execution import ProductionExecutionService
from .task_runtime import (
    ChildActionOutcome,
    ParentTaskPhase,
    ParentTaskRecord,
    close_parent_task,
    record_child_outcome,
    record_hao_task_acceptance,
    record_task_gate_pass,
    validate_parent_task,
)


ConnectionFactory = Callable[[], Any]


def _default_connect_factory(database_url: str) -> ConnectionFactory:
    def connect() -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PSYCOPG_REQUIRED_FOR_PARENT_TASK_RUNTIME") from exc
        return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)

    return connect


def _value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _json_tuple(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _parse_str_tuple(raw: str) -> tuple[str, ...]:
    values = json.loads(raw or "[]")
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ValueError("PARENT_TASK_STRING_LIST_INVALID")
    return tuple(values)


def _outcomes_json(values: tuple[ChildActionOutcome, ...]) -> str:
    return json.dumps(
        [
            {
                "action_id": item.action_id,
                "phase": item.phase.value,
                "authoritative": item.authoritative,
                "failure_code": item.failure_code,
            }
            for item in values
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parse_outcomes(raw: str) -> tuple[ChildActionOutcome, ...]:
    values = json.loads(raw or "[]")
    if not isinstance(values, list):
        raise ValueError("PARENT_TASK_OUTCOMES_INVALID")
    result: list[ChildActionOutcome] = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("PARENT_TASK_OUTCOME_OBJECT_REQUIRED")
        result.append(
            ChildActionOutcome(
                action_id=str(item.get("action_id", "")),
                phase=RunPhase(str(item.get("phase", ""))),
                authoritative=bool(item.get("authoritative", False)),
                failure_code=str(item.get("failure_code", "")),
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class ParentChildPlan:
    slot_id: str
    requested_capability: str
    binding_id: str
    authorization_target: str = ""
    depends_on_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParentTaskPlan:
    plan_id: str
    task: str
    children: tuple[ParentChildPlan, ...]
    required_gate_ids: tuple[str, ...] = ()
    hao_acceptance_required: bool = False


def _validate_dependency_graph(children: tuple[ParentChildPlan, ...]) -> tuple[ParentChildPlan, ...]:
    slot_ids = {child.slot_id for child in children}
    graph: dict[str, tuple[str, ...]] = {}
    normalized: list[ParentChildPlan] = []
    for child in children:
        deps = tuple(value.strip() for value in child.depends_on_slots if value.strip())
        if len(set(deps)) != len(deps):
            raise ValueError("DUPLICATE_PARENT_CHILD_DEPENDENCY")
        if child.slot_id in deps:
            raise ValueError("PARENT_CHILD_SELF_DEPENDENCY")
        if any(dep not in slot_ids for dep in deps):
            raise ValueError("PARENT_CHILD_DEPENDENCY_NOT_FOUND")
        graph[child.slot_id] = deps
        normalized.append(replace(child, depends_on_slots=deps))

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(slot: str) -> None:
        if slot in visited:
            return
        if slot in visiting:
            raise ValueError("PARENT_CHILD_DEPENDENCY_CYCLE")
        visiting.add(slot)
        for dep in graph[slot]:
            visit(dep)
        visiting.remove(slot)
        visited.add(slot)

    for slot in graph:
        visit(slot)
    return tuple(normalized)


class ParentTaskPlanCatalog:
    def __init__(self, plans: Iterable[ParentTaskPlan]) -> None:
        by_id: dict[str, ParentTaskPlan] = {}
        for plan in plans:
            plan_id = plan.plan_id.strip()
            if not plan_id or plan_id in by_id:
                raise ValueError("INVALID_OR_DUPLICATE_PARENT_PLAN_ID")
            if not plan.task.strip() or not plan.children:
                raise ValueError("PARENT_PLAN_TASK_AND_CHILDREN_REQUIRED")
            slot_ids: set[str] = set()
            normalized_children: list[ParentChildPlan] = []
            for child in plan.children:
                slot = child.slot_id.strip()
                if not slot or slot in slot_ids:
                    raise ValueError("INVALID_OR_DUPLICATE_PARENT_CHILD_SLOT")
                if not child.requested_capability.strip() or not child.binding_id.strip():
                    raise ValueError("PARENT_CHILD_BINDING_REQUIRED")
                slot_ids.add(slot)
                normalized_children.append(
                    replace(
                        child,
                        slot_id=slot,
                        requested_capability=child.requested_capability.strip(),
                        binding_id=child.binding_id.strip(),
                        authorization_target=child.authorization_target.strip(),
                    )
                )
            children = _validate_dependency_graph(tuple(normalized_children))
            by_id[plan_id] = ParentTaskPlan(
                plan_id=plan_id,
                task=plan.task.strip(),
                children=children,
                required_gate_ids=tuple(value.strip() for value in plan.required_gate_ids if value.strip()),
                hao_acceptance_required=plan.hao_acceptance_required,
            )
        self._by_id = by_id

    def get(self, plan_id: str) -> ParentTaskPlan | None:
        return self._by_id.get(plan_id.strip())


@dataclass(frozen=True)
class ParentChildRuntime:
    task_run_id: str
    slot_index: int
    slot_id: str
    binding_id: str
    action_id: str
    workflow_id: str = ""
    operational_version: int = 0
    finalized: bool = False
    depends_on_slots: tuple[str, ...] = ()


class PostgresParentTaskStore:
    """Shared durable parent/child orchestration state in the Runtime v2 Postgres DB."""

    def __init__(
        self,
        database_url: str,
        *,
        connect_factory: ConnectionFactory | None = None,
        initialize_schema: bool = True,
    ) -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("POSTGRES_DATABASE_URL_REQUIRED")
        self._connect_factory = connect_factory or _default_connect_factory(database_url)
        if initialize_schema:
            self.initialize_schema()

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        conn = self._connect_factory()
        try:
            conn.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def initialize_schema(self) -> None:
        with self._transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS parent_tasks (
                    task_run_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    task TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    admitted_operational_version INTEGER NOT NULL,
                    required_action_ids_json TEXT NOT NULL,
                    required_gate_ids_json TEXT NOT NULL,
                    hao_acceptance_required INTEGER NOT NULL,
                    authority_snapshot_fingerprint TEXT NOT NULL DEFAULT '',
                    child_outcomes_json TEXT NOT NULL DEFAULT '[]',
                    passed_gate_ids_json TEXT NOT NULL DEFAULT '[]',
                    hao_accepted INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL,
                    failure_code TEXT NOT NULL DEFAULT '',
                    row_revision INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS parent_task_children (
                    task_run_id TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    slot_id TEXT NOT NULL,
                    binding_id TEXT NOT NULL,
                    action_id TEXT NOT NULL UNIQUE,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    operational_version INTEGER NOT NULL DEFAULT 0,
                    finalized INTEGER NOT NULL DEFAULT 0,
                    depends_on_slots_json TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY(task_run_id, slot_index)
                )
                """
            )

    @staticmethod
    def _record(row: Any) -> ParentTaskRecord:
        return validate_parent_task(
            ParentTaskRecord(
                task_run_id=str(_value(row, "task_run_id", 0)),
                task=str(_value(row, "task", 2)),
                mode=Mode(str(_value(row, "mode", 3))),
                admitted_operational_version=int(_value(row, "admitted_operational_version", 4)),
                required_action_ids=_parse_str_tuple(str(_value(row, "required_action_ids_json", 5))),
                required_gate_ids=_parse_str_tuple(str(_value(row, "required_gate_ids_json", 6))),
                hao_acceptance_required=bool(_value(row, "hao_acceptance_required", 7)),
                authority_snapshot_fingerprint=str(_value(row, "authority_snapshot_fingerprint", 8) or ""),
                child_outcomes=_parse_outcomes(str(_value(row, "child_outcomes_json", 9))),
                passed_gate_ids=_parse_str_tuple(str(_value(row, "passed_gate_ids_json", 10))),
                hao_accepted=bool(_value(row, "hao_accepted", 11)),
                phase=ParentTaskPhase(str(_value(row, "phase", 12))),
                failure_code=str(_value(row, "failure_code", 13) or ""),
                store_revision=int(_value(row, "row_revision", 14) or 0),
            )
        )

    @staticmethod
    def _child(row: Any) -> ParentChildRuntime:
        return ParentChildRuntime(
            task_run_id=str(_value(row, "task_run_id", 0)),
            slot_index=int(_value(row, "slot_index", 1)),
            slot_id=str(_value(row, "slot_id", 2)),
            binding_id=str(_value(row, "binding_id", 3)),
            action_id=str(_value(row, "action_id", 4)),
            workflow_id=str(_value(row, "workflow_id", 5) or ""),
            operational_version=int(_value(row, "operational_version", 6) or 0),
            finalized=bool(_value(row, "finalized", 7)),
            depends_on_slots=_parse_str_tuple(str(_value(row, "depends_on_slots_json", 8) or "[]")),
        )

    def create(
        self,
        *,
        plan_id: str,
        record: ParentTaskRecord,
        children: tuple[ParentChildRuntime, ...],
    ) -> ParentTaskRecord:
        record = validate_parent_task(record)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO parent_tasks(
                    task_run_id, plan_id, task, mode, admitted_operational_version,
                    required_action_ids_json, required_gate_ids_json,
                    hao_acceptance_required, authority_snapshot_fingerprint,
                    child_outcomes_json, passed_gate_ids_json, hao_accepted, phase, failure_code,
                    row_revision
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.task_run_id,
                    plan_id.strip(),
                    record.task,
                    record.mode.value,
                    record.admitted_operational_version,
                    _json_tuple(record.required_action_ids),
                    _json_tuple(record.required_gate_ids),
                    int(record.hao_acceptance_required),
                    record.authority_snapshot_fingerprint,
                    _outcomes_json(record.child_outcomes),
                    _json_tuple(record.passed_gate_ids),
                    int(record.hao_accepted),
                    record.phase.value,
                    record.failure_code,
                    record.store_revision,
                ),
            )
            for child in children:
                conn.execute(
                    """
                    INSERT INTO parent_task_children(
                        task_run_id, slot_index, slot_id, binding_id, action_id,
                        workflow_id, operational_version, finalized, depends_on_slots_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        child.task_run_id,
                        child.slot_index,
                        child.slot_id,
                        child.binding_id,
                        child.action_id,
                        child.workflow_id,
                        child.operational_version,
                        int(child.finalized),
                        _json_tuple(child.depends_on_slots),
                    ),
                )
        return record

    def get(self, task_run_id: str) -> ParentTaskRecord | None:
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT task_run_id, plan_id, task, mode, admitted_operational_version,
                       required_action_ids_json, required_gate_ids_json,
                       hao_acceptance_required, authority_snapshot_fingerprint,
                       child_outcomes_json, passed_gate_ids_json, hao_accepted, phase, failure_code,
                       row_revision
                FROM parent_tasks WHERE task_run_id = %s
                """,
                (task_run_id.strip(),),
            ).fetchone()
        return None if row is None else self._record(row)

    def plan_id(self, task_run_id: str) -> str:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT plan_id FROM parent_tasks WHERE task_run_id = %s",
                (task_run_id.strip(),),
            ).fetchone()
        if row is None:
            raise ValueError("PARENT_TASK_NOT_FOUND")
        return str(_value(row, "plan_id", 0))

    def children(self, task_run_id: str) -> tuple[ParentChildRuntime, ...]:
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT task_run_id, slot_index, slot_id, binding_id, action_id,
                       workflow_id, operational_version, finalized, depends_on_slots_json
                FROM parent_task_children
                WHERE task_run_id = %s ORDER BY slot_index
                """,
                (task_run_id.strip(),),
            ).fetchall()
        return tuple(self._child(row) for row in rows)

    def bind_workflow(
        self,
        *,
        task_run_id: str,
        slot_index: int,
        workflow_id: str,
        operational_version: int,
    ) -> ParentChildRuntime:
        workflow_id = workflow_id.strip()
        if not workflow_id or operational_version < 1:
            raise ValueError("PARENT_CHILD_WORKFLOW_BINDING_REQUIRED")
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT workflow_id FROM parent_task_children
                WHERE task_run_id = %s AND slot_index = %s FOR UPDATE
                """,
                (task_run_id.strip(), slot_index),
            ).fetchone()
            if row is None:
                raise ValueError("PARENT_CHILD_SLOT_NOT_FOUND")
            prior = str(_value(row, "workflow_id", 0) or "")
            if prior and prior != workflow_id:
                raise ValueError("PARENT_CHILD_WORKFLOW_CONFLICT")
            conn.execute(
                """
                UPDATE parent_task_children
                SET workflow_id = %s, operational_version = %s
                WHERE task_run_id = %s AND slot_index = %s
                """,
                (workflow_id, operational_version, task_run_id.strip(), slot_index),
            )
        return self.children(task_run_id)[slot_index]

    def mark_finalized(self, *, task_run_id: str, slot_index: int) -> None:
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE parent_task_children SET finalized = 1
                WHERE task_run_id = %s AND slot_index = %s
                """,
                (task_run_id.strip(), slot_index),
            )
            if cursor.rowcount != 1:
                raise ValueError("PARENT_CHILD_SLOT_NOT_FOUND")

    def save(self, record: ParentTaskRecord) -> ParentTaskRecord:
        record = validate_parent_task(record)
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE parent_tasks
                SET child_outcomes_json = %s,
                    passed_gate_ids_json = %s,
                    hao_accepted = %s,
                    phase = %s,
                    failure_code = %s,
                    row_revision = row_revision + 1
                WHERE task_run_id = %s
                  AND admitted_operational_version = %s
                  AND row_revision = %s
                """,
                (
                    _outcomes_json(record.child_outcomes),
                    _json_tuple(record.passed_gate_ids),
                    int(record.hao_accepted),
                    record.phase.value,
                    record.failure_code,
                    record.task_run_id,
                    record.admitted_operational_version,
                    record.store_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("PARENT_TASK_CHANGED_OR_MISSING")
        saved = self.get(record.task_run_id)
        if saved is None:
            raise RuntimeError("PARENT_TASK_SAVE_LOST")
        return saved


@dataclass(frozen=True)
class ParentTaskStartResult:
    task_run_id: str
    phase: ParentTaskPhase
    child_slots: tuple[ParentChildRuntime, ...]


@dataclass(frozen=True)
class ParentTaskChildSubmission:
    task_run_id: str
    slot_id: str
    workflow_id: str
    action_id: str
    accepted: bool
    code: str


class ProductionParentTaskService:
    """Parent orchestration that reuses ProductionExecutionService for every child effect."""

    def __init__(
        self,
        *,
        production: ProductionExecutionService,
        store: PostgresParentTaskStore,
        plans: ParentTaskPlanCatalog,
    ) -> None:
        self._production = production
        self._store = store
        self._plans = plans

    @staticmethod
    def _child_run_id(task_run_id: str, slot_index: int) -> str:
        return f"{task_run_id}:C{slot_index + 1:03d}"

    @classmethod
    def _action_id(cls, task_run_id: str, slot_index: int, binding_id: str) -> str:
        return f"{cls._child_run_id(task_run_id, slot_index)}:A0001:{binding_id.strip()}"

    def start(
        self,
        state: ActiveOperationalState,
        *,
        plan_id: str,
        task_run_id: str | None = None,
        authority_snapshot_fingerprint: str = "",
    ) -> ParentTaskStartResult:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise ValueError("PARENT_TASK_PLAN_NOT_FOUND")
        if state.task != plan.task:
            raise ValueError("PARENT_TASK_PLAN_TASK_MISMATCH")
        task_run_id = (task_run_id or ("TASK-" + uuid.uuid4().hex)).strip()
        children = tuple(
            ParentChildRuntime(
                task_run_id=task_run_id,
                slot_index=index,
                slot_id=child.slot_id,
                binding_id=child.binding_id,
                action_id=self._action_id(task_run_id, index, child.binding_id),
                depends_on_slots=child.depends_on_slots,
            )
            for index, child in enumerate(plan.children)
        )
        record = ParentTaskRecord(
            task_run_id=task_run_id,
            task=state.task,
            mode=state.mode,
            admitted_operational_version=state.version,
            required_action_ids=tuple(child.action_id for child in children),
            required_gate_ids=plan.required_gate_ids,
            hao_acceptance_required=plan.hao_acceptance_required,
            authority_snapshot_fingerprint=authority_snapshot_fingerprint.strip(),
            phase=ParentTaskPhase.OPEN,
        )
        self._store.create(plan_id=plan.plan_id, record=record, children=children)
        return ParentTaskStartResult(task_run_id, record.phase, children)

    async def submit_child(
        self,
        state: ActiveOperationalState,
        *,
        task_run_id: str,
        slot_id: str,
        expected_state_delta: str = "",
        arguments: Mapping[str, str] | None = None,
    ) -> ParentTaskChildSubmission:
        record = self._store.get(task_run_id)
        if record is None:
            raise ValueError("PARENT_TASK_NOT_FOUND")
        if state.version != record.admitted_operational_version or state.task != record.task:
            raise ValueError("PARENT_TASK_OPERATIONAL_CONTEXT_CHANGED")
        plan = self._plans.get(self._store.plan_id(task_run_id))
        if plan is None:
            raise ValueError("PARENT_TASK_PLAN_NOT_FOUND")
        children = self._store.children(task_run_id)
        matching = [child for child in children if child.slot_id == slot_id.strip()]
        if len(matching) != 1:
            raise ValueError("PARENT_CHILD_SLOT_NOT_FOUND")
        runtime_child = matching[0]
        if runtime_child.workflow_id:
            return ParentTaskChildSubmission(
                task_run_id,
                runtime_child.slot_id,
                runtime_child.workflow_id,
                runtime_child.action_id,
                True,
                "PARENT_CHILD_ALREADY_SUBMITTED",
            )
        outcomes = {item.action_id: item for item in record.child_outcomes}
        children_by_slot = {item.slot_id: item for item in children}
        for dependency_slot in runtime_child.depends_on_slots:
            dependency = children_by_slot[dependency_slot]
            outcome = outcomes.get(dependency.action_id)
            if not dependency.finalized or outcome is None:
                return ParentTaskChildSubmission(
                    task_run_id,
                    runtime_child.slot_id,
                    "",
                    runtime_child.action_id,
                    False,
                    "PARENT_CHILD_DEPENDENCY_PENDING",
                )
            if outcome.phase != RunPhase.CLOSED or not outcome.authoritative:
                return ParentTaskChildSubmission(
                    task_run_id,
                    runtime_child.slot_id,
                    "",
                    runtime_child.action_id,
                    False,
                    "PARENT_CHILD_DEPENDENCY_NOT_SATISFIED",
                )
        planned = plan.children[runtime_child.slot_index]
        intent = ModelActionIntent(
            intent_id="INTENT-PARENT-" + uuid.uuid4().hex,
            requested_capability=planned.requested_capability,
            binding_id=planned.binding_id,
            expected_state_delta=expected_state_delta.strip(),
            authorization_target=planned.authorization_target,
            arguments=tuple((str(key), str(value)) for key, value in (arguments or {}).items()),
        )
        submission = await self._production.submit(
            state,
            ModelIngressRequest(
                run_id=self._child_run_id(task_run_id, runtime_child.slot_index),
                sequence=1,
                intent=intent,
            ),
        )
        if not submission.accepted or submission.pending is None:
            return ParentTaskChildSubmission(
                task_run_id,
                runtime_child.slot_id,
                "",
                runtime_child.action_id,
                False,
                submission.code,
            )
        self._store.bind_workflow(
            task_run_id=task_run_id,
            slot_index=runtime_child.slot_index,
            workflow_id=submission.pending.handle.workflow_id,
            operational_version=submission.pending.operational_version,
        )
        record = replace(record, phase=ParentTaskPhase.RUNNING, failure_code="")
        self._store.save(record)
        return ParentTaskChildSubmission(
            task_run_id,
            runtime_child.slot_id,
            submission.pending.handle.workflow_id,
            runtime_child.action_id,
            True,
            submission.code,
        )

    async def refresh(
        self,
        state: ActiveOperationalState,
        *,
        task_run_id: str,
        issued_at: str | None = None,
    ) -> ParentTaskRecord:
        record = self._store.get(task_run_id)
        if record is None:
            raise ValueError("PARENT_TASK_NOT_FOUND")
        children = self._store.children(task_run_id)
        transient_phases: list[RunPhase] = []
        child_state_unknown = False
        for child in children:
            if child.finalized or not child.workflow_id:
                continue
            pending = await self._production.resume(
                child.workflow_id,
                operational_version=child.operational_version,
            )
            current = await self._production.current_state(pending)
            if current is None:
                child_state_unknown = True
                continue
            if current.phase != RunPhase.CLOSED:
                transient_phases.append(current.phase)
                continue
            result = await self._production.finalize(
                pending,
                issued_at=issued_at or datetime.now(timezone.utc).isoformat(),
            )
            record = record_child_outcome(
                record,
                ChildActionOutcome(
                    action_id=child.action_id,
                    phase=RunPhase.CLOSED,
                    authoritative=result.authoritative,
                    failure_code="" if result.authoritative else result.code,
                ),
            )
            self._store.mark_finalized(
                task_run_id=task_run_id,
                slot_index=child.slot_index,
            )

        if state.version != record.admitted_operational_version:
            record = replace(
                record,
                phase=ParentTaskPhase.RECONCILIATION_REQUIRED,
                failure_code="STALE_OPERATIONAL_CONTEXT",
            )
        elif child_state_unknown:
            record = replace(
                record,
                phase=ParentTaskPhase.RECONCILIATION_REQUIRED,
                failure_code="CHILD_STATE_UNKNOWN",
            )
        elif RunPhase.UNSYNCED in transient_phases:
            record = replace(
                record,
                phase=ParentTaskPhase.RECONCILIATION_REQUIRED,
                failure_code="CHILD_RECONCILIATION_REQUIRED",
            )
        elif RunPhase.AWAITING_HAO in transient_phases:
            record = replace(record, phase=ParentTaskPhase.AWAITING_HAO, failure_code="")
        elif RunPhase.FAILED in transient_phases:
            record = replace(record, phase=ParentTaskPhase.FAILED, failure_code="CHILD_FAILED")
        elif RunPhase.BLOCKED in transient_phases:
            record = replace(record, phase=ParentTaskPhase.BLOCKED, failure_code="CHILD_BLOCKED")
        elif len(record.child_outcomes) < len(record.required_action_ids):
            record = replace(record, phase=ParentTaskPhase.RUNNING, failure_code="")
        else:
            record = close_parent_task(record, state.version)
        return self._store.save(record)

    def record_verified_gate(self, *, task_run_id: str, gate_id: str) -> ParentTaskRecord:
        record = self._store.get(task_run_id)
        if record is None:
            raise ValueError("PARENT_TASK_NOT_FOUND")
        return self._store.save(record_task_gate_pass(record, gate_id))

    def record_hao_acceptance(self, *, task_run_id: str, accepted: bool) -> ParentTaskRecord:
        record = self._store.get(task_run_id)
        if record is None:
            raise ValueError("PARENT_TASK_NOT_FOUND")
        return self._store.save(record_hao_task_acceptance(record, accepted))
