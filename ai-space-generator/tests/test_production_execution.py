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
                ),
                ActionBinding(
                    binding_id="message.send",
                    capability="external_message",
                    provider="trusted-message-provider",
                    action_name="send",
                    archetype=ActionArchetype.PUBLISH,
                    externality=ActionExternality.EXTERNAL_REVERSIBLE,
                    authorization_scope_prefix="SEND_EXTERNAL",
                ),
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


def external_request():
    return ModelIngressRequest(
        run_id="RUN-PROD-EXT",
        sequence=1,
        intent=ModelActionIntent(
            intent_id="INTENT-EXT",
            requested_capability="external_message",
            binding_id="message.send",
            expected_state_delta="send one controlled message",
            authorization_target="recipient-1",
        ),
    )


class FakeHandle:
    def __init__(self, run_input, *, close=True):
        self.run_input = run_input
        self._close = close
        self.signals = []

    @property
    def workflow_id(self):
        return self.run_input.record.run_id

    async def authorize(self, scope, approved, reason=""):
        self.signals.append((scope, approved, reason))

    async def current_state(self):
        return self.run_input.record

    async def result(self):
        action = self.run_input.proposal
        if not self._close:
            record = replace(
                self.run_input.record,
                action=action,
                phase=RunPhase.BLOCKED,
            )
            return DurableRunResult(
                record,
                ControlDecision(True, "ADMITTED"),
                ControlDecision(False, "BLOCKED"),
            )

        receipts = [
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
        ]
        if action.archetype in {ActionArchetype.MUTATE, ActionArchetype.PUBLISH}:
            receipts.extend(
                [
                    EvidenceReceipt(
                        "TOOL-1",
                        EvidenceKind.TOOL_RECEIPT,
                        True,
                        "provider",
                        claim_scope=action.action_id,
                        origin=EvidenceOrigin.PROVIDER,
                    ),
                    EvidenceReceipt(
                        "READBACK-1",
                        EvidenceKind.STATE_READBACK,
                        True,
                        "provider-readback",
                        claim_scope=action.action_id,
                        origin=EvidenceOrigin.PROVIDER,
                    ),
                ]
            )
        record = replace(
            self.run_input.record,
            action=action,
            phase=RunPhase.CLOSED,
            evidence=tuple(receipts),
        )
        allowed = ControlDecision(True, "CLAIM_ALLOWED:COMPLETED")
        return DurableRunResult(record, ControlDecision(True, "ADMITTED"), allowed)


class FakeStarter:
    def __init__(self, *, close=True):
        self.close = close
        self.handles = []

    async def start(self, run_input):
        handle = FakeHandle(run_input, close=self.close)
        self.handles.append(handle)
        return handle


def service(tmp_path, *, close=True):
    return ProductionExecutionService(
        gateway=gateway(),
        starter=FakeStarter(close=close),
        attestor=CompletionAttestor(SECRET),
        completion_store=SQLiteAuthoritativeCompletionStore(
            str(tmp_path / "completion.sqlite")
        ),
    )


def test_production_facade_is_the_only_path_that_mints_and_commits_completion(tmp_path):
    import asyncio

    result = asyncio.run(
        service(tmp_path).execute(
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

    result = asyncio.run(
        service(tmp_path, close=False).execute(
            state(),
            request(),
            issued_at="2026-08-30T14:20:00+08:00",
        )
    )
    assert result.authoritative is False
    assert result.attestation is None
    assert result.code == "CONTROLLED_RUN_NOT_CLOSED:BLOCKED"


def test_submit_returns_durable_handle_and_approval_is_a_signal_not_an_open_request(tmp_path):
    import asyncio

    async def scenario():
        svc = service(tmp_path)
        submission = await svc.submit(state(), external_request())
        assert submission.accepted is True
        assert submission.pending is not None
        assert submission.pending.handle.workflow_id == "RUN-PROD-EXT"
        await svc.authorize(
            submission.pending,
            scope="SEND_EXTERNAL:recipient-1",
            approved=True,
            reason="Hao approved exact recipient",
        )
        assert submission.pending.handle.signals == [
            ("SEND_EXTERNAL:recipient-1", True, "Hao approved exact recipient")
        ]
        result = await svc.finalize(
            submission.pending,
            issued_at="2026-08-30T14:21:00+08:00",
        )
        assert result.authoritative is True

    asyncio.run(scenario())


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
