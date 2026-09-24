from src.native_response_detection import (
    ClientSurface,
    DetectionOutcome,
    NativeContinuationEvidence,
    NativeErrorClass,
    ToolEffectState,
    admit_diagnostic_retest,
    classify_native_outcome,
)


def evidence(**overrides):
    values = dict(
        task_id="execution-surface-isolation",
        mode="SYS",
        client_surface=ClientSurface.IOS,
        observed_at="2026-09-24T17:00:00+08:00",
        execution_started=True,
        tool_dispatch_observed=True,
        tool_effect_state=ToolEffectState.VERIFIED,
        readback_observed=True,
        terminal_message_observed=False,
        native_error_class=NativeErrorClass.STREAM_INTERRUPTED,
        failure_signature="native-stream-interruption",
        environment_fingerprint="ios-current",
    )
    values.update(overrides)
    return NativeContinuationEvidence(**values)


def test_missing_terminal_after_verified_readback_is_ambiguous_delivery():
    assert classify_native_outcome(evidence(), persistence_required=True) == DetectionOutcome.AMBIGUOUS_DELIVERY


def test_verified_terminal_and_required_readback_is_complete_verified():
    sample = evidence(
        terminal_message_observed=True,
        native_error_class=NativeErrorClass.NONE,
    )
    assert classify_native_outcome(sample, persistence_required=True) == DetectionOutcome.COMPLETE_VERIFIED


def test_missing_required_readback_fails_closed_even_with_terminal_message():
    sample = evidence(
        readback_observed=False,
        terminal_message_observed=True,
        native_error_class=NativeErrorClass.NONE,
    )
    assert classify_native_outcome(sample, persistence_required=True) == DetectionOutcome.UNKNOWN_EFFECT


def test_unknown_consequential_effect_fails_closed_before_delivery_classification():
    sample = evidence(tool_effect_state=ToolEffectState.UNKNOWN)
    assert classify_native_outcome(sample, persistence_required=True) == DetectionOutcome.UNKNOWN_EFFECT


def test_same_surface_signature_and_environment_blocks_synthetic_retest():
    decision = admit_diagnostic_retest(evidence(), evidence())
    assert decision.allowed is False
    assert decision.code == "NO_MATERIAL_DELTA_DO_NOT_RETEST"


def test_different_client_surface_is_material_delta():
    decision = admit_diagnostic_retest(
        evidence(),
        evidence(client_surface=ClientSurface.WEB),
    )
    assert decision.allowed is True
    assert "client_surface" in decision.basis


def test_natural_use_recurrence_is_admitted_without_forcing_synthetic_delta():
    decision = admit_diagnostic_retest(
        evidence(),
        evidence(observed_at="2026-09-24T17:30:00+08:00"),
        natural_use_recurrence=True,
    )
    assert decision.allowed is True
    assert decision.code == "NATURAL_USE_EVIDENCE_ADMITTED"


def test_green_public_status_does_not_erase_field_failure():
    sample = evidence(public_status_snapshot="operational")
    assert classify_native_outcome(sample, persistence_required=True) == DetectionOutcome.AMBIGUOUS_DELIVERY


def test_controlled_runtime_surface_does_not_imply_native_field_delivery():
    sample = evidence(
        client_surface=ClientSurface.CONTROLLED_RUNTIME,
        terminal_message_observed=False,
    )
    assert classify_native_outcome(sample) == DetectionOutcome.AMBIGUOUS_DELIVERY
