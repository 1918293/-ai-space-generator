from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.context_bound_reasoning import ContextBoundAdmission, ContextBoundReasoningResult
from src.context_reasoning_consumer import ContextBoundReasoningConsumer
from src.execution_control import Mode
from src.operational_state import ActiveOperationalState, CommandActor


class StateSource:
    def __init__(self):
        self.calls = 0
        self.value = ActiveOperationalState(
            Mode.EXP,
            "Runtime v2 consumer ingress",
            188,
            "EVENT-188",
        )

    def get(self):
        self.calls += 1
        return self.value


class ChangingStateSource(StateSource):
    def get(self):
        self.calls += 1
        if self.calls == 1:
            return self.value
        return replace(self.value, version=self.value.version + 1)


class Ingress:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def prepare(self, state, request, *, run_id, sequence=1):
        self.calls.append((state, request, run_id, sequence))
        return self.result


def blocked_result():
    return ContextBoundReasoningResult(
        admission=ContextBoundAdmission(False, "PRE_MODEL_CURRENT_UNRESOLVED"),
        code="PRE_MODEL_CURRENT_UNRESOLVED",
    )


def selected_result():
    receipt = SimpleNamespace(
        mode=Mode.EXP,
        task="Runtime v2 consumer ingress",
        operational_version=188,
    )
    model_input = SimpleNamespace(receipt=receipt)
    proposal = SimpleNamespace(
        action_id="RUN-2:A0001:formal.persist",
        authorization_scope="HAO_DRIVE_WRITE:canonical",
    )
    prepared = SimpleNamespace(
        record=SimpleNamespace(decision_id="DECISION:abc"),
        resolution=SimpleNamespace(proposal=proposal),
    )
    return ContextBoundReasoningResult(
        admission=ContextBoundAdmission(
            True,
            "PRE_MODEL_SEMANTIC_CONTEXT_ADMITTED",
            model_input=model_input,
        ),
        prepared=prepared,
        code="MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING",
    )


def test_consumer_accepts_only_raw_turn_and_runtime_state_owns_mode_task():
    state_source = StateSource()
    ingress = Ingress(blocked_result())
    consumer = ContextBoundReasoningConsumer(
        state_source=state_source,
        ingress=ingress,
    )

    result = consumer.prepare_user_turn(
        "Auto > continue",
        run_id="RUN-1",
        event_id="EVENT-USER-1",
    )

    assert result.code == "PRE_MODEL_CURRENT_UNRESOLVED"
    assert result.action_selected is False
    assert state_source.calls == 1
    assert len(ingress.calls) == 1
    current_state, request, run_id, sequence = ingress.calls[0]
    assert current_state.mode == Mode.EXP
    assert current_state.task == "Runtime v2 consumer ingress"
    assert request.user_text == "Auto > continue"
    assert request.actor == CommandActor.USER
    assert request.event_id == "EVENT-USER-1"
    assert run_id == "RUN-1"
    assert sequence == 1


def test_consumer_returns_only_prepared_control_plane_projection_after_fresh_current_recheck():
    state_source = StateSource()
    consumer = ContextBoundReasoningConsumer(
        state_source=state_source,
        ingress=Ingress(selected_result()),
    )

    result = consumer.prepare_user_turn("Auto", run_id="RUN-2")

    assert result.code == "MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING"
    assert result.action_selected is True
    assert result.decision_id == "DECISION:abc"
    assert result.action_id == "RUN-2:A0001:formal.persist"
    assert result.authorization_scope == "HAO_DRIVE_WRITE:canonical"
    assert state_source.calls == 2
    assert not hasattr(result, "mode")
    assert not hasattr(result, "task")
    assert not hasattr(result, "authority_refs")
    assert not hasattr(result, "model_intent")


def test_consumer_discards_prepared_action_if_current_changes_during_reasoning():
    state_source = ChangingStateSource()
    consumer = ContextBoundReasoningConsumer(
        state_source=state_source,
        ingress=Ingress(selected_result()),
    )

    result = consumer.prepare_user_turn("Auto", run_id="RUN-STALE")

    assert result.code == "PRE_MODEL_CURRENT_CHANGED_BEFORE_ACTION"
    assert result.action_selected is False
    assert result.decision_id == ""
    assert result.action_id == ""
    assert result.authorization_scope == ""
    assert state_source.calls == 2


def test_consumer_fails_closed_if_prepared_action_lacks_current_receipt():
    result = selected_result()
    invalid = replace(
        result,
        admission=ContextBoundAdmission(True, "PRE_MODEL_SEMANTIC_CONTEXT_ADMITTED"),
    )
    state_source = StateSource()
    consumer = ContextBoundReasoningConsumer(
        state_source=state_source,
        ingress=Ingress(invalid),
    )

    projected = consumer.prepare_user_turn("Auto", run_id="RUN-MISSING-RECEIPT")

    assert projected.code == "PRE_MODEL_CURRENT_RECEIPT_MISSING_BEFORE_ACTION"
    assert projected.action_selected is False
    assert state_source.calls == 1


def test_consumer_rejects_missing_run_id_before_ingress():
    state_source = StateSource()
    ingress = Ingress(blocked_result())
    consumer = ContextBoundReasoningConsumer(
        state_source=state_source,
        ingress=ingress,
    )

    with pytest.raises(ValueError, match="RUN_ID_REQUIRED"):
        consumer.prepare_user_turn("Auto", run_id="  ")

    assert state_source.calls == 0
    assert ingress.calls == []


def test_consumer_rejects_nonpositive_sequence_before_ingress():
    state_source = StateSource()
    ingress = Ingress(blocked_result())
    consumer = ContextBoundReasoningConsumer(
        state_source=state_source,
        ingress=ingress,
    )

    with pytest.raises(ValueError, match="ACTION_SEQUENCE_MUST_BE_POSITIVE"):
        consumer.prepare_user_turn("Auto", run_id="RUN-3", sequence=0)

    assert state_source.calls == 0
    assert ingress.calls == []
