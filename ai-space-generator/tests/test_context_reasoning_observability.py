from types import SimpleNamespace

from src.action_catalog import ModelActionIntent
from src.context_bound_reasoning import (
    AdmittedContextItem,
    ContextBoundAdmission,
    ContextBoundModelInput,
    ContextBoundReasoningResult,
)
from src.context_reasoning_observability import (
    ObservableContextBoundReasoningIngress,
    observation_from_result,
)
from src.control_gateway import PreModelContextReceipt, PreModelContextRequest
from src.execution_control import Mode
from src.operational_state import ActiveOperationalState, CommandActor


def model_input(items):
    receipt = PreModelContextReceipt(
        checkpoint_id="R187",
        mode=Mode.EXP,
        task="Runtime v2 observability",
        operational_version=187,
        authority_refs=("CURRENT:A540",),
        existing_work_refs=("PR17:HEAD",),
        prior_attempt_refs=("FAIL:1",),
        regression_refs=("REG:1",),
        reuse_disposition="REUSE",
        context_fingerprint="structural-fp",
    )
    return ContextBoundModelInput(
        receipt=receipt,
        admitted_context=tuple(items),
        semantic_fingerprint="semantic-fp",
        user_text="must never enter observation",
    )


def items():
    return (
        AdmittedContextItem(
            "CURRENT:A540",
            "CURRENT_CONTROL",
            "private current semantic content",
            "v1",
        ),
        AdmittedContextItem(
            "PR17:HEAD",
            "EXISTING_WORK",
            "private existing-work semantic content",
            "v2",
            disposition="REUSE",
        ),
        AdmittedContextItem(
            "FAIL:1",
            "PRIOR_ATTEMPT",
            "private failure semantic content",
            "v3",
            disposition="DO_NOT_REPEAT",
            binding_id="legacy.binding",
        ),
        AdmittedContextItem(
            "REG:1",
            "REGRESSION",
            "private regression semantic content",
            "v4",
        ),
    )


def admission(current_items=None):
    value = model_input(items() if current_items is None else current_items)
    return ContextBoundAdmission(True, "PRE_MODEL_SEMANTIC_CONTEXT_ADMITTED", value)


def test_presented_is_not_promoted_to_used_on_normal_model_action():
    intent = ModelActionIntent("INTENT-1", "formal_persistence", "formal.persist")
    prepared = SimpleNamespace(resolution=SimpleNamespace(proposal=object()))
    result = ContextBoundReasoningResult(
        admission=admission(),
        intent=intent,
        prepared=prepared,
        code="MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING",
    )

    observation = observation_from_result(result, run_id="RUN-1")

    assert observation.structural_ref_count == 4
    assert observation.admitted_ref_count == 4
    assert observation.presented_to_model is True
    assert observation.deterministic_used_count == 0
    assert observation.action_selected is True
    assert observation.structural_fingerprint == "structural-fp"
    assert observation.semantic_fingerprint == "semantic-fp"
    assert not hasattr(observation, "user_text")
    assert not hasattr(observation, "summary")


def test_trusted_no_action_is_counted_as_deterministic_use_without_model_call():
    current = list(items())
    current[0] = AdmittedContextItem(
        "CURRENT:A540",
        "CURRENT_CONTROL",
        "private no-action semantic content",
        "v1",
        disposition="NO_ACTION",
    )
    result = ContextBoundReasoningResult(
        admission=admission(tuple(current)),
        code="PRE_MODEL_CONTEXT_NO_ACTION",
    )

    observation = observation_from_result(result, run_id="RUN-2")

    assert observation.presented_to_model is False
    assert observation.deterministic_used_count == 1
    assert observation.action_selected is False


def test_known_failure_block_records_deterministic_use_but_no_action_selection():
    intent = ModelActionIntent("INTENT-2", "formal_persistence", "legacy.binding")
    result = ContextBoundReasoningResult(
        admission=admission(),
        intent=intent,
        code="PRE_MODEL_KNOWN_FAILURE_REPEAT_BLOCKED",
    )

    observation = observation_from_result(result, run_id="RUN-3")

    assert observation.presented_to_model is True
    assert observation.deterministic_used_count == 1
    assert observation.action_selected is False


class Sink:
    def __init__(self):
        self.values = []

    def record(self, observation):
        self.values.append(observation)


class Ingress:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def prepare(self, state, request, *, run_id, sequence=1):
        self.calls.append((state, request, run_id, sequence))
        return self.result


def test_observable_wrapper_emits_once_without_changing_result():
    result = ContextBoundReasoningResult(
        admission=admission(),
        intent=ModelActionIntent("INTENT-3", "formal_persistence", "formal.persist"),
        code="MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING",
    )
    sink = Sink()
    base = Ingress(result)
    observable = ObservableContextBoundReasoningIngress(base, sink)
    state = ActiveOperationalState(Mode.EXP, "Runtime v2 observability", 187, "EVENT-187")
    request = PreModelContextRequest("Auto", CommandActor.USER)

    returned = observable.prepare(state, request, run_id="RUN-4", sequence=2)

    assert returned is result
    assert len(base.calls) == 1
    assert len(sink.values) == 1
    assert sink.values[0].run_id == "RUN-4"
