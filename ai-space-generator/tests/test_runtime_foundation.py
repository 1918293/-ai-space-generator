from src.runtime_observability import RuntimeTelemetry, otlp_http_signal_endpoints


class Provider:
    def __init__(self):
        self.flush = []
        self.shutdown_calls = 0

    def force_flush(self, timeout_millis):
        self.flush.append(timeout_millis)
        return True

    def shutdown(self):
        self.shutdown_calls += 1


class Noop:
    def add(self, *args, **kwargs):
        pass

    def start_as_current_span(self, *args, **kwargs):
        raise AssertionError("not used")


def test_telemetry_shutdown_is_bounded_and_drains_both_providers():
    trace_provider = Provider()
    meter_provider = Provider()
    telemetry = RuntimeTelemetry(
        tracer=Noop(),
        run_events=Noop(),
        failures=Noop(),
        authoritative_completions=Noop(),
        reconciliation_cases=Noop(),
        trace_provider=trace_provider,
        meter_provider=meter_provider,
    )
    telemetry.shutdown(timeout_millis=1500)
    assert trace_provider.flush == [1500]
    assert meter_provider.flush == [1500]
    assert trace_provider.shutdown_calls == 1
    assert meter_provider.shutdown_calls == 1


def test_otlp_signal_endpoints_do_not_invent_logging_or_payload_exporters():
    assert otlp_http_signal_endpoints("https://otel.example.com") == (
        "https://otel.example.com/v1/traces",
        "https://otel.example.com/v1/metrics",
    )
