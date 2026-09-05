import asyncio
from pathlib import Path

from src.controlled_runner import ToolOutcome
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    FailureStage,
)
from src.idempotent_broker import (
    IdempotencyState,
    IdempotentAsyncBroker,
    SQLiteIdempotencyStore,
)


def mutation(**overrides):
    values = dict(
        action_id="RUN-IDEM:A0001:drive.update",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="formal_persistence",
        provider="drive",
        action_name="update",
        expected_state_delta="write one row",
        idempotency_key="RUN-IDEM:A0001:drive.update",
    )
    values.update(overrides)
    return ActionProposal(**values)


class Provider:
    def __init__(self, outcome=None, exc=None, delay=0):
        self.calls = 0
        self.outcome = outcome or ToolOutcome(True, "RECEIPT-1", "drive")
        self.exc = exc
        self.delay = delay

    async def execute(self, proposal):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.outcome


def store(tmp_path: Path):
    return SQLiteIdempotencyStore(str(tmp_path / "idempotency.sqlite3"))


def test_success_is_replayed_from_durable_store_without_second_provider_call(tmp_path):
    first_provider = Provider()
    first_store = store(tmp_path)
    first = asyncio.run(IdempotentAsyncBroker(first_provider, first_store).execute(mutation()))
    assert first.success is True
    assert first_provider.calls == 1

    second_provider = Provider(ToolOutcome(True, "SHOULD-NOT-RUN", "drive"))
    second_store = store(tmp_path)
    replay = asyncio.run(IdempotentAsyncBroker(second_provider, second_store).execute(mutation()))
    assert replay.success is True
    assert replay.receipt_id == "RECEIPT-1"
    assert second_provider.calls == 0
    assert second_store.get(mutation().idempotency_key).state == IdempotencyState.SUCCEEDED


def test_same_key_with_different_action_fingerprint_is_rejected(tmp_path):
    provider = Provider()
    broker = IdempotentAsyncBroker(provider, store(tmp_path))
    asyncio.run(broker.execute(mutation()))
    conflict = asyncio.run(
        broker.execute(mutation(expected_state_delta="different write"))
    )
    assert conflict.success is False
    assert conflict.error_code == "IDEMPOTENCY_KEY_CONFLICT"
    assert conflict.failure_stage == FailureStage.POLICY
    assert provider.calls == 1


def test_provider_exception_becomes_unknown_effect_and_is_never_auto_replayed(tmp_path):
    first_provider = Provider(exc=RuntimeError("connection dropped"))
    durable_store = store(tmp_path)
    first = asyncio.run(IdempotentAsyncBroker(first_provider, durable_store).execute(mutation()))
    assert first.success is False
    assert first.error_code == "PROVIDER_EXCEPTION_EFFECT_UNKNOWN"
    assert durable_store.get(mutation().idempotency_key).state == IdempotencyState.UNKNOWN_EFFECT

    second_provider = Provider()
    second = asyncio.run(IdempotentAsyncBroker(second_provider, durable_store).execute(mutation()))
    assert second.success is False
    assert second.error_code == "IDEMPOTENCY_EFFECT_UNKNOWN"
    assert second_provider.calls == 0


def test_provider_success_without_receipt_is_treated_as_unknown_effect(tmp_path):
    provider = Provider(ToolOutcome(True, "", ""))
    durable_store = store(tmp_path)
    outcome = asyncio.run(IdempotentAsyncBroker(provider, durable_store).execute(mutation()))
    assert outcome.success is False
    assert outcome.error_code == "PROVIDER_SUCCESS_WITHOUT_RECEIPT_EFFECT_UNKNOWN"
    assert durable_store.get(mutation().idempotency_key).state == IdempotencyState.UNKNOWN_EFFECT


def test_explicit_provider_failure_is_remembered_as_known_no_effect(tmp_path):
    provider = Provider(
        ToolOutcome(
            False,
            error_code="QUOTA_BLOCKED",
            failure_stage=FailureStage.TOOL_EXECUTION,
        )
    )
    durable_store = store(tmp_path)
    first = asyncio.run(IdempotentAsyncBroker(provider, durable_store).execute(mutation()))
    assert first.error_code == "QUOTA_BLOCKED"
    assert durable_store.get(mutation().idempotency_key).state == IdempotencyState.FAILED_NO_EFFECT

    second_provider = Provider()
    second = asyncio.run(IdempotentAsyncBroker(second_provider, durable_store).execute(mutation()))
    assert second.success is False
    assert second.error_code == "QUOTA_BLOCKED"
    assert second_provider.calls == 0


def test_concurrent_duplicate_calls_reserve_once_and_never_double_execute(tmp_path):
    provider = Provider(delay=0.05)
    broker = IdempotentAsyncBroker(provider, store(tmp_path))

    async def run_both():
        return await asyncio.gather(
            broker.execute(mutation()),
            broker.execute(mutation()),
        )

    results = asyncio.run(run_both())
    assert provider.calls == 1
    assert sum(result.success for result in results) == 1
    blocked = [result for result in results if not result.success]
    assert len(blocked) == 1
    assert blocked[0].error_code == "IDEMPOTENCY_EFFECT_UNKNOWN"
