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
    trace_provider: Any | None = None
    meter_provider: Any | None = None

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
        decision_id: str = "",
        policy_fingerprint: str = "",
        admission_result: str = "",
    ) -> None:
        metric_attributes: dict[str, str] = {
            "hao.event": event,
            "hao.run.phase": phase or "UNKNOWN",
        }
        if provider:
            metric_attributes["hao.provider"] = provider
        if admission_result:
            metric_attributes["hao.admission.result"] = admission_result.strip().upper()
        if failure_stage:
            metric_attributes["hao.failure.stage"] = failure_stage
        self.run_events.add(1, metric_attributes)

        if failure_stage or failure_code:
            failure_attributes = dict(metric_attributes)
            if failure_code:
                # Failure codes are runtime-owned enums/codes, never provider/model payloads.
                failure_attributes["hao.failure.code"] = failure_code
            self.failures.add(1, failure_attributes)

        trace_attributes = dict(metric_attributes)
        if run_id:
            # IDs and fingerprints are high-cardinality and therefore trace-only.
            trace_attributes["hao.run.id"] = run_id
        if decision_id:
            trace_attributes["hao.decision.id"] = decision_id
        if policy_fingerprint:
            trace_attributes["hao.policy.fingerprint"] = policy_fingerprint
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

    def force_flush(self, timeout_millis: int = 2000) -> None:
        for provider in (self.trace_provider, self.meter_provider):
            if provider is None:
                continue
            flush = getattr(provider, "force_flush", None)
            if callable(flush):
                flush(timeout_millis=timeout_millis)

    def shutdown(self, timeout_millis: int = 2000) -> None:
        """Bounded exporter drain for Cloud Run SIGTERM handling."""
        self.force_flush(timeout_millis=timeout_millis)
        for provider in (self.trace_provider, self.meter_provider):
            if provider is None:
                continue
            shutdown = getattr(provider, "shutdown", None)
            if callable(shutdown):
                shutdown()


def configure_runtime_telemetry(
    *,
    endpoint: str,
    role: str,
    region: str,
    environment: str = "production",
    release_id: str = "",
    deployment_id: str = "",
) -> RuntimeTelemetry:
    """Initialize OTLP/HTTP traces + metrics using a strict metadata allowlist.

    No model prompts, model outputs, action arguments, expected-state deltas,
    provider payloads, OAuth tokens, signing material, subject identifiers, or
    secret values are accepted by this API. High-cardinality run IDs are span
    attributes only, never metric labels.
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
    resource_attributes = {
        "service.name": f"hao-runtime-v2-{role}",
        "service.namespace": "hao-system",
        "deployment.environment.name": environment.strip() or "unknown",
        "cloud.region": region,
    }
    if release_id.strip():
        resource_attributes["service.version"] = release_id.strip()
    if deployment_id.strip():
        resource_attributes["service.instance.group"] = deployment_id.strip()
    resource = Resource.create(resource_attributes)

    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=trace_endpoint),
            max_queue_size=2048,
            max_export_batch_size=512,
            schedule_delay_millis=5000,
            export_timeout_millis=3000,
        )
    )
    trace.set_tracer_provider(trace_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=metric_endpoint),
        export_interval_millis=30000,
        export_timeout_millis=3000,
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
        trace_provider=trace_provider,
        meter_provider=meter_provider,
    )
