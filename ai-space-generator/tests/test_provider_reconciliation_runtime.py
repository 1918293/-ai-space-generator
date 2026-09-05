import asyncio
import pytest

from src.provider_reconciliation_runtime import (
    DirectReadback,
    EffectState,
    MutationIntent,
    ProviderBinding,
    ProviderBindingCatalog,
    ProviderReceipt,
    ProviderReconciliationRuntime,
    ReconciliationState,
    RetryPolicy,
    SQLiteProviderRuntimeStore,
)


class FakeSheetProvider:
    def __init__(self):
        self.state = {}
        self.calls = 0
        self.mode = "success"
        self.receipts = []

    async def mutate(self, binding, intent):
        self.calls += 1
        key = binding.target_ref
        if self.mode == "network_loss_after_effect":
            self.state[key] = intent.expected_state_delta
            raise ConnectionError("lost after effect")
        if self.mode == "known_no_effect_retryable":
            if self.calls == 1:
                return ProviderReceipt(False, source="fake:sheet", no_effect_confirmed=True, retryable=True, error_code="TRANSIENT_503_NO_EFFECT")
        if self.mode == "known_no_effect":
            return ProviderReceipt(False, source="fake:sheet", no_effect_confirmed=True, retryable=False, error_code="REJECTED_NO_EFFECT")
        if self.mode == "success_without_receipt":
            self.state[key] = intent.expected_state_delta
            return ProviderReceipt(True)
        if self.mode == "partial_mismatch":
            self.state[key] = "partial"
            rid = f"r-{self.calls}"
            self.receipts.append(rid)
            return ProviderReceipt(True, rid, "fake:sheet:update")
        self.state[key] = intent.expected_state_delta
        rid = f"r-{self.calls}"
        self.receipts.append(rid)
        return ProviderReceipt(True, rid, "fake:sheet:update")

    async def readback(self, binding, intent):
        value = self.state.get(binding.target_ref, "")
        return DirectReadback(
            matched=value == intent.expected_state_delta,
            observed_digest=f"digest:{value}",
            source="fake:sheet:get",
            error_code="" if value == intent.expected_state_delta else "READBACK_MISMATCH",
        )


def make_runtime(tmp_path, provider=None, retry_policy=None):
    provider = provider or FakeSheetProvider()
    store = SQLiteProviderRuntimeStore(str(tmp_path / "runtime.db"))
    catalog = ProviderBindingCatalog([ProviderBinding("sheets-current", "google-sheets", "sheet-1:Current!A1:B2")])
    sleeps = []
    async def sleeper(delay):
        sleeps.append(delay)
    runtime = ProviderReconciliationRuntime(catalog=catalog, provider=provider, store=store, retry_policy=retry_policy or RetryPolicy(max_attempts=2, backoff_seconds=(0.0,)), sleeper=sleeper)
    return runtime, provider, store, sleeps


def intent(**kwargs):
    values = dict(run_id="run-1", owner_subject="hao-sub", binding_id="sheets-current", idempotency_key="idem-1", expected_state_delta="sha256:new", payload={"values": [["new"]]})
    values.update(kwargs)
    return MutationIntent(**values)


@pytest.mark.asyncio
async def test_provider_success_requires_direct_readback_and_never_completes_task(tmp_path):
    runtime, provider, _, _ = make_runtime(tmp_path)
    result = await runtime.execute(intent())
    assert result.effect_state == EffectState.KNOWN_APPLIED
    assert result.verification_passed is True
    assert result.task_completed is False
    assert result.provider_receipt.startswith("r-")
    assert result.readback_source == "fake:sheet:get"


@pytest.mark.asyncio
async def test_idempotency_restart_does_not_duplicate_effect(tmp_path):
    runtime, provider, store, _ = make_runtime(tmp_path)
    first = await runtime.execute(intent())
    second_runtime = ProviderReconciliationRuntime(catalog=ProviderBindingCatalog([ProviderBinding("sheets-current", "google-sheets", "sheet-1:Current!A1:B2")]), provider=provider, store=store)
    second = await second_runtime.execute(intent())
    assert first.effect_state == second.effect_state == EffectState.KNOWN_APPLIED
    assert provider.calls == 1
    assert second.code == "IDEMPOTENT_KNOWN_APPLIED"


@pytest.mark.asyncio
async def test_network_loss_after_effect_becomes_unknown_and_never_blind_retries(tmp_path):
    provider = FakeSheetProvider()
    provider.mode = "network_loss_after_effect"
    runtime, provider, _, _ = make_runtime(tmp_path, provider)
    first = await runtime.execute(intent())
    assert first.effect_state == EffectState.UNKNOWN_EFFECT
    assert first.reconciliation_case_id
    calls_after_first = provider.calls
    second = await runtime.execute(intent())
    assert second.effect_state == EffectState.UNKNOWN_EFFECT
    assert provider.calls == calls_after_first == 1


@pytest.mark.asyncio
async def test_unknown_effect_reconciles_only_from_direct_provider_readback_not_model_evidence(tmp_path):
    provider = FakeSheetProvider()
    provider.mode = "network_loss_after_effect"
    runtime, provider, _, _ = make_runtime(tmp_path, provider)
    result = await runtime.execute(intent())
    case = runtime.inspect_reconciliation(result.reconciliation_case_id, "hao-sub")
    assert case.state == ReconciliationState.OPEN
    with pytest.raises(PermissionError, match="RECONCILIATION_OWNER_MISMATCH"):
        runtime.inspect_reconciliation(result.reconciliation_case_id, "attacker")
    provider.mode = "success"
    resolved = await runtime.reconcile_from_direct_provider_readback(result.reconciliation_case_id, "hao-sub", intent())
    assert resolved.state == ReconciliationState.VERIFIED_APPLIED
    assert resolved.trusted_evidence_digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_partial_mutation_readback_mismatch_is_unknown_not_success(tmp_path):
    provider = FakeSheetProvider()
    provider.mode = "partial_mismatch"
    runtime, _, _, _ = make_runtime(tmp_path, provider)
    result = await runtime.execute(intent())
    assert result.effect_state == EffectState.UNKNOWN_EFFECT
    assert result.verification_passed is False
    assert result.task_completed is False
    assert result.reconciliation_case_id


@pytest.mark.asyncio
async def test_bounded_retry_only_when_no_effect_is_confirmed(tmp_path):
    provider = FakeSheetProvider()
    provider.mode = "known_no_effect_retryable"
    runtime, provider, _, sleeps = make_runtime(tmp_path, provider, RetryPolicy(max_attempts=2, backoff_seconds=(0.01,)))
    result = await runtime.execute(intent())
    assert result.effect_state == EffectState.KNOWN_APPLIED
    assert result.attempts == 2
    assert provider.calls == 2
    assert sleeps == [0.01]


@pytest.mark.asyncio
async def test_known_not_applied_is_terminal_for_same_idempotency_key(tmp_path):
    provider = FakeSheetProvider()
    provider.mode = "known_no_effect"
    runtime, provider, _, _ = make_runtime(tmp_path, provider)
    first = await runtime.execute(intent())
    assert first.effect_state == EffectState.KNOWN_NOT_APPLIED
    second = await runtime.execute(intent())
    assert second.effect_state == EffectState.KNOWN_NOT_APPLIED
    assert provider.calls == 1


def test_runtime_owned_binding_blocks_model_selected_target(tmp_path):
    runtime, _, _, _ = make_runtime(tmp_path)
    with pytest.raises(PermissionError, match="PROVIDER_BINDING_NOT_CONFIGURED"):
        asyncio.run(runtime.execute(intent(binding_id="model-invented-target")))


@pytest.mark.asyncio
async def test_retry_with_changed_delta_requires_terminal_trusted_resolution_and_new_controlled_identity(tmp_path):
    provider = FakeSheetProvider()
    provider.mode = "network_loss_after_effect"
    runtime, provider, _, _ = make_runtime(tmp_path, provider)
    result = await runtime.execute(intent())
    with pytest.raises(PermissionError, match="RECONCILIATION_NOT_RETRY_SAFE"):
        runtime.retry_with_changed_delta(result.reconciliation_case_id, "hao-sub", new_run_id="run-2", new_idempotency_key="idem-2", new_expected_state_delta="sha256:changed")
    provider.mode = "success"
    await runtime.reconcile_from_direct_provider_readback(result.reconciliation_case_id, "hao-sub", intent())
    with pytest.raises(ValueError, match="CHANGED_EXPECTED_STATE_DELTA_REQUIRED"):
        runtime.retry_with_changed_delta(result.reconciliation_case_id, "hao-sub", new_run_id="run-2", new_idempotency_key="idem-2", new_expected_state_delta="sha256:new")
    plan = runtime.retry_with_changed_delta(result.reconciliation_case_id, "hao-sub", new_run_id="run-2", new_idempotency_key="idem-2", new_expected_state_delta="sha256:changed")
    assert plan.new_run_id == "run-2"
    assert plan.new_idempotency_key == "idem-2"
    assert plan.new_expected_state_delta == "sha256:changed"


@pytest.mark.asyncio
async def test_idempotency_owner_isolation(tmp_path):
    runtime, provider, _, _ = make_runtime(tmp_path)
    await runtime.execute(intent())
    with pytest.raises(PermissionError, match="IDEMPOTENCY_OWNER_MISMATCH"):
        await runtime.execute(intent(owner_subject="attacker"))
