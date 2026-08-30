from dataclasses import replace

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from src.authoritative_completion import CompletionAttestor, SQLiteAuthoritativeCompletionStore
from src.control_gateway import (
    ControlPlaneGateway,
    ModelIngressRequest,
    TaskExecutionPolicy,
)
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ControlDecision,
    EvidenceKind,
    EvidenceOrigin,
    EvidenceReceipt,
    Mode,
    RunPhase,
)
from src.operational_state import ActiveOperationalState
from src.production_execution import (
    ProductionExecutionService,
    UncontrolledEffectReport,
    quarantine_uncontrolled_effect,
)
from src.temporal_control import DurableRunResult


SECRET = b"hao-production-control-secret-minimum-32-bytes"


class PolicyProvider:
    def resolve(self, state):
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=("verified result",),
        )


def gateway():
    return ControlPlaneGateway(
        ActionCatalog(
            [
                ActionBinding(
                    binding_id="research.read",
                    capability="research_read",
                    provider="trusted-read-provider",
                    action_name="read",
                    archetype=ActionArchetype.READ,
                    externality=ActionExternality.READ_ONLY,
                )
            ]
        ),
        PolicyProvider(),
    )


def state():
    return ActiveOperationalState(
        mode=Mode.EXP,
        task="Production cutover",
        version=12,
        last_event_id="EVT-12",
    )


def request():
    return ModelIngressRequest(
        run_id="RUN-PROD-1",
        sequence=1,
        intent=ModelActionIntent(
            intent_id="INTENT-1",
            requested_capability="research_read",
            binding_id="research.read",
        ),
    )


class ClosedRunner:
    async def run(self, run_input):
        action = run_input.proposal
        record = replace(
            run_input.record,
            action=action,
            phase=RunPhase.CLOSED,
            evidence=(
                EvidenceReceipt(
                    "VERIFY-1",
                    EvidenceKind.VERIFICATION_PASS,
                    True,
                    "runtime-verifier",
                    claim_scope=action.action_id,
                    origin=EvidenceOrigin.VERIFIER,
                ),
                EvidenceReceipt(
                    "GATE-1",
                    EvidenceKind.ACCEPTANCE_GATE_PASS,
                    True,
                    "runtime-verifier",
                    claim_scope=action.action_id,
                    origin=EvidenceOrigin.VERIFIER,
                ),
            ),
        )
        allowed = ControlDecision(True, "CLAIM_ALLOWED:COMPLETED")
        return DurableRunResult(record, ControlDecision(True, "ADMITTED"), allowed)


class BlockedRunner:
    async def run(self, run_input):
        record = replace(
            run_input.record,
            action=run_input.proposal,
            phase=RunPhase.BLOCKED,
        )
        return DurableRunResult(
            record,
            ControlDecision(True, "ADMITTED"),
            ControlDecision(False, "BLOCKED"),
        )


def test_production_facade_is_the_only_path_that_mints_and_commits_completion(tmp_path):
    import asyncio

    attestor = CompletionAttestor(SECRET)
    store = SQLiteAuthoritativeCompletionStore(str(tmp_path / "completion.sqlite"))
    service = ProductionExecutionService(
        gateway=gateway(),
        runner=ClosedRunner(),
        attestor=attestor,
        completion_store=store,
    )
    result = asyncio.run(
        service.execute(
            state(),
            request(),
            issued_at="2026-08-30T14:20:00+08:00",
        )
    )
    assert result.authoritative is True
    assert result.attestation is not None
    assert result.attestation.operational_version == 12
    assert result.attestation.task == "Production cutover"
    assert result.code == "AUTHORITATIVE_COMPLETION_COMMITTED"


def test_nonclosed_controlled_run_cannot_mint_authoritative_completion(tmp_path):
    import asyncio

    service = ProductionExecutionService(
        gateway=gateway(),
        runner=BlockedRunner(),
        attestor=CompletionAttestor(SECRET),
        completion_store=SQLiteAuthoritativeCompletionStore(
            str(tmp_path / "completion.sqlite")
        ),
    )
    result = asyncio.run(
        service.execute(
            state(),
            request(),
            issued_at="2026-08-30T14:20:00+08:00",
        )
    )
    assert result.authoritative is False
    assert result.attestation is None
    assert result.code == "CONTROLLED_RUN_NOT_CLOSED:BLOCKED"


def test_out_of_band_native_or_manual_effect_is_quarantined_not_promoted():
    disposition = quarantine_uncontrolled_effect(
        UncontrolledEffectReport(
            source="native-chatgpt-connector",
            receipt_id="TOOL-DIRECT-123",
            description="provider may have changed external state",
        )
    )
    assert disposition.authoritative is False
    assert disposition.requires_reconciliation is True
    assert disposition.code == "UNCONTROLLED_EFFECT_REQUIRES_RECONCILIATION"


def test_uncontrolled_effect_without_direct_receipt_is_even_weaker_evidence():
    disposition = quarantine_uncontrolled_effect(
        UncontrolledEffectReport(source="native-chatgpt-connector", receipt_id="")
    )
    assert disposition.authoritative is False
    assert disposition.requires_reconciliation is True
    assert disposition.code == "UNCONTROLLED_EFFECT_UNVERIFIABLE"
