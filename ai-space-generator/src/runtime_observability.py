from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator


def otlp_http_signal_endpoints(endpoint: str) -> tuple[str, str]:
    """Resolve one configured OTLP/HTTP endpoint into trace + metric endpoints."""
    value = endpoint.strip().rstrip("/")
    if not value:
        raise ValueError("OTEL_ENDPOINT_REQUIRED")
    if value.endswith("/v1/traces"):
        return value, value[: -len("/v1/traces")] + "/v1/metrics"
    if value.endswith("/v1/metrics"):
        return value[: -len("/v1/metrics")] + "/v1/traces", value
    return value + "/v1/traces", value + "/v1/metrics"


@dataclass(frozen=True)
class RuntimeTelemetry:
    tracer: Any
    run_events: Any
    failures: Any
    authoritative_completions: Any
    reconciliation_cases: Any

    @contextmanager
    def span(
        self,
        name: str,
        *,
        run_id: str = "",
        binding_id: str = "",
        provider: str = "",
        phase: str = "",
    ) -> Iterator[Any]:
        attributes: dict[str, str] = {}
        if run_id:
            attributes["hao.run.id"] = run_id
        if binding_id:
            attributes["hao.action.binding"] = binding_id
        if provider:
            attributes["hao.provider"] = provider
        if phase:
            attributes["hao.run.phase"] = phase
        with self.tracer.start_as_current_span(name, attributes=attributes) as span:
            yield span

    def record_run_event(
        self,
        event: str,
        *,
        phase: str,
        run_id: str = "",
        provider: str = "",
        failure_stage: str = "",
        failure_code: str = "",
    ) -> None:
        metric_attributes: dict[str, str] = {
            "hao.event": event,
            "hao.run.phase": phase or "UNKNOWN",
        }
        if provider:
            metric_attributes["hao.provider"] = provider
        if failure_stage:
            metric_attributes["hao.failure.stage"] = failure_stage
        self.run_events.add(1, metric_attributes)

        if failure_stage or failure_code:
            failure_attributes = dict(metric_attributes)
            if failure_code:
                failure_attributes["hao.failure.code"] = failure_code
            self.failures.add(1, failure_attributes)

        trace_attributes = dict(metric_attributes)
        if run_id:
            trace_attributes["hao.run.id"] = run_id
        if failure_code:
            trace_attributes["hao.failure.code"] = failure_code
        with self.tracer.start_as_current_span(
            f"hao.runtime.{event}",
            attributes=trace_attributes,
        ):
            pass

    def record_authoritative_completion(self) -> None:
        self.authoritative_completions.add(1)

    def record_reconciliation(
        self,
        *,
        kind: str,
        run_id: str = "",
        error_code: str = "",
    ) -> None:
        metric_attributes = {"hao.reconciliation.kind": kind}
        if error_code:
            metric_attributes["hao.failure.code"] = error_code
        self.reconciliation_cases.add(1, metric_attributes)

        trace_attributes = dict(metric_attributes)
        if run_id:
            trace_attributes["hao.run.id"] = run_id
        with self.tracer.start_as_current_span(
            "hao.runtime.reconciliation_opened",
            attributes=trace_attributes,
        ):
            pass


def configure_runtime_telemetry(
    *,
    endpoint: str,
    role: str,
    region: str,
) -> RuntimeTelemetry:
    """Initialize production OTLP/HTTP traces + metrics without logging payloads.

    High-cardinality run IDs belong only on spans. Metrics intentionally use
    bounded operational labels and never include action arguments, Google data,
    OAuth tokens, signing material, or other secret/content payloads.
    """
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    trace_endpoint, metric_endpoint = otlp_http_signal_endpoints(endpoint)
    role = role.strip() or "unknown"
    region = region.strip() or "unknown"
    resource = Resource.create(
        {
            "service.name": f"hao-runtime-v2-{role}",
            "service.namespace": "hao-system",
            "deployment.environment.name": "production",
            "cloud.region": region,
        }
    )

    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=trace_endpoint))
    )
    trace.set_tracer_provider(trace_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=metric_endpoint)
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    tracer = trace_provider.get_tracer("hao.runtime.v2")
    meter = meter_provider.get_meter("hao.runtime.v2")
    return RuntimeTelemetry(
        tracer=tracer,
        run_events=meter.create_counter(
            "hao.runtime.run.events",
            unit="{event}",
            description="Runtime-owned controlled run state observations.",
        ),
        failures=meter.create_counter(
            "hao.runtime.failures",
            unit="{failure}",
            description="Typed Runtime v2 failures by stage/code.",
        ),
        authoritative_completions=meter.create_counter(
            "hao.runtime.authoritative_completions",
            unit="{completion}",
            description="Authoritative completion attestations committed.",
        ),
        reconciliation_cases=meter.create_counter(
            "hao.runtime.reconciliation_cases",
            unit="{case}",
            description="Reconciliation cases opened by typed ambiguity.",
        ),
    )
