import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.execution_control import ExecutionRecord, Mode, RunPhase
from src.operational_state import ActiveOperationalState
from src.parent_task_production import (
    ParentChildPlan,
    ParentTaskPlan,
    ParentTaskPlanCatalog,
    PostgresParentTaskStore,
    ProductionParentTaskService,
)
from src.production_execution import ProductionExecutionResult
from src.task_runtime import ParentTaskPhase


class SqlitePostgresCompatConnection:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        normalized = sql.strip()
        if normalized == "BEGIN ISOLATION LEVEL SERIALIZABLE":
            return self._conn.execute("BEGIN IMMEDIATE")
        normalized = normalized.replace(" FOR UPDATE", "").replace("%s", "?")
        return self._conn.execute(normalized, params)

    def close(self):
        self._conn.close()


def make_store(tmp_path: Path):
    db = tmp_path / "parent.sqlite3"
    factory = lambda: SqlitePostgresCompatConnection(db)
    return PostgresParentTaskStore(
        "postgresql://runtime-v2/test",
        connect_factory=factory,
    ), factory


def catalog():
    return ParentTaskPlanCatalog(
        [
            ParentTaskPlan(
                plan_id="formal.two-step",
                task="Parent production task",
                children=(
                    ParentChildPlan("write", "formal_persistence", "formal.write"),
                    ParentChildPlan("index", "formal_persistence", "formal.index"),
                ),
            ),
            ParentTaskPlan(
                plan_id="acceptance-required",
                task="Parent production task",
                children=(
                    ParentChildPlan("write", "formal_persistence", "formal.write"),
                ),
                hao_acceptance_required=True,
            ),
        ]
    )


def state(version=7):
    return ActiveOperationalState(Mode.EXP, "Parent production task", version, "EVENT-7")


class Handle:
    def __init__(self, workflow_id, record):
        self.workflow_id = workflow_id
        self.record = record


class FakeProduction:
    def __init__(self):
        self.handles = {}
        self.submit_calls = 0
        self.finalize_calls = 0

    async def submit(self, state, request):
        self.submit_calls += 1
        record = ExecutionRecord(
            run_id=request.run_id,
            task=state.task,
            mode=state.mode,
            goal_valid=True,
            acceptance_criteria=("child",),
            phase=RunPhase.ADMITTED,
        )
        workflow_id = request.run_id
        handle = Handle(workflow_id, record)
        self.handles[workflow_id] = handle
        return SimpleNamespace(
            accepted=True,
            code="CONTROLLED_RUN_SUBMITTED",
            record=record,
            pending=SimpleNamespace(handle=handle, operational_version=state.version),
        )

    async def resume(self, workflow_id, *, operational_version):
        return SimpleNamespace(
            handle=self.handles[workflow_id], operational_version=operational_version
        )

    async def current_state(self, pending):
        return pending.handle.record

    async def finalize(self, pending, *, issued_at):
        self.finalize_calls += 1
        return ProductionExecutionResult(
            pending.handle.record,
            pending.handle.record.phase == RunPhase.CLOSED,
            "AUTHORITATIVE_COMPLETION_COMMITTED"
            if pending.handle.record.phase == RunPhase.CLOSED
            else "NOT_CLOSED",
        )


def service(tmp_path):
    store, factory = make_store(tmp_path)
    production = FakeProduction()
    return ProductionParentTaskService(
        production=production, store=store, plans=catalog()
    ), store, production, factory


def test_parent_start_owns_deterministic_required_child_action_ids(tmp_path):
    runtime, store, _, _ = service(tmp_path)
    opened = runtime.start(state(), plan_id="formal.two-step", task_run_id="TASK-1")
    record = store.get("TASK-1")

    assert opened.phase == ParentTaskPhase.OPEN
    assert record.required_action_ids == (
        "TASK-1:C001:A0001:formal.write",
        "TASK-1:C002:A0001:formal.index",
    )
    assert tuple(child.slot_id for child in opened.child_slots) == ("write", "index")


def test_parent_child_submission_uses_planned_binding_and_is_idempotent(tmp_path):
    async def scenario():
        runtime, store, production, _ = service(tmp_path)
        runtime.start(state(), plan_id="formal.two-step", task_run_id="TASK-2")
        first = await runtime.submit_child(
            state(),
            task_run_id="TASK-2",
            slot_id="write",
            expected_state_delta="one exact write",
            arguments={"values_json": "[[\"x\"]]"},
        )
        replay = await runtime.submit_child(
            state(), task_run_id="TASK-2", slot_id="write"
        )
        return first, replay, production, store

    first, replay, production, store = asyncio.run(scenario())
    assert first.accepted is True
    assert first.workflow_id == "TASK-2:C001"
    assert first.action_id == "TASK-2:C001:A0001:formal.write"
    assert replay.code == "PARENT_CHILD_ALREADY_SUBMITTED"
    assert production.submit_calls == 1
    assert store.children("TASK-2")[0].workflow_id == "TASK-2:C001"


def test_transient_awaiting_hao_is_projected_not_frozen_as_child_outcome(tmp_path):
    async def scenario():
        runtime, store, production, _ = service(tmp_path)
        runtime.start(state(), plan_id="formal.two-step", task_run_id="TASK-3")
        await runtime.submit_child(state(), task_run_id="TASK-3", slot_id="write")
        production.handles["TASK-3:C001"].record = ExecutionRecord(
            run_id="TASK-3:C001",
            task=state().task,
            mode=Mode.EXP,
            goal_valid=True,
            acceptance_criteria=("child",),
            phase=RunPhase.AWAITING_HAO,
        )
        refreshed = await runtime.refresh(state(), task_run_id="TASK-3")
        return refreshed

    refreshed = asyncio.run(scenario())
    assert refreshed.phase == ParentTaskPhase.AWAITING_HAO
    assert refreshed.child_outcomes == ()


def test_unsynced_child_requires_reconciliation_without_finalize_or_replay(tmp_path):
    async def scenario():
        runtime, _, production, _ = service(tmp_path)
        runtime.start(state(), plan_id="formal.two-step", task_run_id="TASK-UNSYNCED")
        await runtime.submit_child(
            state(), task_run_id="TASK-UNSYNCED", slot_id="write"
        )
        production.handles["TASK-UNSYNCED:C001"].record = ExecutionRecord(
            run_id="TASK-UNSYNCED:C001",
            task=state().task,
            mode=Mode.EXP,
            goal_valid=True,
            acceptance_criteria=("child",),
            phase=RunPhase.UNSYNCED,
            failure_code="UNKNOWN_EFFECT_REQUIRES_RECONCILIATION",
        )
        before_submit_calls = production.submit_calls
        refreshed = await runtime.refresh(state(), task_run_id="TASK-UNSYNCED")
        return refreshed, production, before_submit_calls

    refreshed, production, before_submit_calls = asyncio.run(scenario())
    assert refreshed.phase == ParentTaskPhase.RECONCILIATION_REQUIRED
    assert refreshed.failure_code == "CHILD_RECONCILIATION_REQUIRED"
    assert refreshed.child_outcomes == ()
    assert production.finalize_calls == 0
    assert production.submit_calls == before_submit_calls


def test_parent_closes_only_after_every_required_child_is_closed_and_authoritative(tmp_path):
    async def scenario():
        runtime, store, production, _ = service(tmp_path)
        runtime.start(state(), plan_id="formal.two-step", task_run_id="TASK-4")
        await runtime.submit_child(state(), task_run_id="TASK-4", slot_id="write")
        await runtime.submit_child(state(), task_run_id="TASK-4", slot_id="index")
        for workflow_id in ("TASK-4:C001", "TASK-4:C002"):
            production.handles[workflow_id].record = ExecutionRecord(
                run_id=workflow_id,
                task=state().task,
                mode=Mode.EXP,
                goal_valid=True,
                acceptance_criteria=("child",),
                phase=RunPhase.CLOSED,
            )
        refreshed = await runtime.refresh(state(), task_run_id="TASK-4")
        return refreshed, store, production

    refreshed, store, production = asyncio.run(scenario())
    assert refreshed.phase == ParentTaskPhase.CLOSED
    assert len(refreshed.child_outcomes) == 2
    assert all(outcome.authoritative for outcome in refreshed.child_outcomes)
    assert all(child.finalized for child in store.children("TASK-4"))
    assert production.finalize_calls == 2


def test_operational_version_change_blocks_parent_completion(tmp_path):
    async def scenario():
        runtime, _, _, _ = service(tmp_path)
        runtime.start(state(7), plan_id="formal.two-step", task_run_id="TASK-5")
        return await runtime.refresh(state(8), task_run_id="TASK-5")

    refreshed = asyncio.run(scenario())
    assert refreshed.phase == ParentTaskPhase.RECONCILIATION_REQUIRED
    assert refreshed.failure_code == "STALE_OPERATIONAL_CONTEXT"


def test_parent_acceptance_requirement_cannot_close_without_hao_acceptance(tmp_path):
    async def scenario():
        runtime, _, production, _ = service(tmp_path)
        runtime.start(state(), plan_id="acceptance-required", task_run_id="TASK-6")
        await runtime.submit_child(state(), task_run_id="TASK-6", slot_id="write")
        production.handles["TASK-6:C001"].record = ExecutionRecord(
            run_id="TASK-6:C001",
            task=state().task,
            mode=Mode.EXP,
            goal_valid=True,
            acceptance_criteria=("child",),
            phase=RunPhase.CLOSED,
        )
        before = await runtime.refresh(state(), task_run_id="TASK-6")
        runtime.record_hao_acceptance(task_run_id="TASK-6", accepted=True)
        after = await runtime.refresh(state(), task_run_id="TASK-6")
        return before, after

    before, after = asyncio.run(scenario())
    assert before.phase == ParentTaskPhase.AWAITING_HAO
    assert after.phase == ParentTaskPhase.CLOSED


def test_parent_state_survives_store_restart(tmp_path):
    runtime, store, _, factory = service(tmp_path)
    runtime.start(state(), plan_id="formal.two-step", task_run_id="TASK-7")

    restarted = PostgresParentTaskStore(
        "postgresql://runtime-v2/test", connect_factory=factory
    )
    restored = restarted.get("TASK-7")
    assert restored is not None
    assert restored.required_action_ids == store.get("TASK-7").required_action_ids


def test_parent_plan_must_match_current_runtime_task(tmp_path):
    runtime, _, _, _ = service(tmp_path)
    wrong = ActiveOperationalState(Mode.EXP, "Other task", 7, "EVENT-7")
    with pytest.raises(ValueError, match="PARENT_TASK_PLAN_TASK_MISMATCH"):
        runtime.start(wrong, plan_id="formal.two-step", task_run_id="TASK-8")
