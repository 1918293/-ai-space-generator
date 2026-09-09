from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable, Mapping

from .canonical_semantic_reader import (
    CanonicalRangeReader,
    CanonicalSemanticRangeSource,
    ConfiguredCanonicalSemanticsResolver,
    GoogleWorkspaceCanonicalRangeReader,
    load_canonical_semantic_sources_json,
)
from .context_bound_reasoning import ContextBoundIntentModel, ContextBoundPreModelGateway, ContextBoundReasoningIngress
from .context_bound_responses import build_openai_context_bound_intent_boundary
from .context_reasoning_consumer import ContextBoundReasoningConsumer, OperationalStateSource
from .context_reasoning_observability import (
    ContextReasoningObservation,
    ObservableContextBoundReasoningIngress,
)
from .control_gateway import ControlPlaneGateway, PreModelContextGateway, PreModelContextRequest, PreModelContextResolution
from .operational_state import ActiveOperationalState
from .runtime_observability import RuntimeTelemetry, active_runtime_telemetry


_ALLOWED_REUSE_DISPOSITIONS = frozenset(
    {"REUSE", "CROSSWALK_DELTA", "REFERENCE_ONLY", "NO_REUSABLE_ASSET", "ADMIT"}
)


@dataclass(frozen=True)
class ContextReasoningRoute:
    """Deployment-owned routing metadata for one Runtime TASK.

    This contains only logical refs and reuse routing. Canonical semantic content,
    source versions, Mode, operational version and action authority never live in
    this object. The configured semantic reader must fresh-read every routed ref
    before the first model call.
    """

    task: str
    authority_refs: tuple[str, ...]
    existing_work_refs: tuple[str, ...] = ()
    prior_attempt_refs: tuple[str, ...] = ()
    regression_refs: tuple[str, ...] = ()
    reuse_disposition: str = "NO_REUSABLE_ASSET"


def _refs(raw: object, *, field: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"CONTEXT_ROUTE_{field}_LIST_REQUIRED")
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str):
            raise ValueError(f"CONTEXT_ROUTE_{field}_STRING_REQUIRED")
        ref = value.strip()
        if not ref or ref != value:
            raise ValueError(f"CONTEXT_ROUTE_{field}_EXACT_REF_REQUIRED")
        if ref in seen:
            raise ValueError(f"CONTEXT_ROUTE_{field}_DUPLICATE:{ref}")
        seen.add(ref)
        result.append(ref)
    return tuple(result)


def load_context_reasoning_routes(raw: object) -> tuple[ContextReasoningRoute, ...]:
    if not isinstance(raw, list):
        raise ValueError("CONTEXT_REASONING_ROUTES_LIST_REQUIRED")
    routes: list[ContextReasoningRoute] = []
    seen_tasks: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("CONTEXT_REASONING_ROUTE_OBJECT_REQUIRED")
        raw_task = item.get("task")
        if not isinstance(raw_task, str):
            raise ValueError("CONTEXT_REASONING_ROUTE_TASK_STRING_REQUIRED")
        task = raw_task.strip()
        if not task or task != raw_task:
            raise ValueError("CONTEXT_REASONING_ROUTE_TASK_EXACT_REQUIRED")
        if task in seen_tasks:
            raise ValueError("CONTEXT_REASONING_ROUTE_TASK_DUPLICATE:" + task)
        seen_tasks.add(task)
        authority_refs = _refs(item.get("authority_refs"), field="AUTHORITY_REFS")
        if not authority_refs:
            raise ValueError("CONTEXT_REASONING_ROUTE_AUTHORITY_REQUIRED")
        disposition = str(item.get("reuse_disposition", "NO_REUSABLE_ASSET")).strip().upper()
        if disposition not in _ALLOWED_REUSE_DISPOSITIONS:
            raise ValueError("CONTEXT_REASONING_ROUTE_REUSE_DISPOSITION_INVALID")
        routes.append(
            ContextReasoningRoute(
                task=task,
                authority_refs=authority_refs,
                existing_work_refs=_refs(item.get("existing_work_refs"), field="EXISTING_WORK_REFS"),
                prior_attempt_refs=_refs(item.get("prior_attempt_refs"), field="PRIOR_ATTEMPT_REFS"),
                regression_refs=_refs(item.get("regression_refs"), field="REGRESSION_REFS"),
                reuse_disposition=disposition,
            )
        )
    if not routes:
        raise ValueError("CONTEXT_REASONING_ROUTES_REQUIRED")
    return tuple(routes)


def load_context_reasoning_routes_json(raw: str) -> tuple[ContextReasoningRoute, ...]:
    value = raw.strip()
    if not value:
        raise ValueError("CONTEXT_REASONING_ROUTES_JSON_REQUIRED")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("CONTEXT_REASONING_ROUTES_JSON_INVALID") from exc
    return load_context_reasoning_routes(decoded)


def _source_identities(
    sources: Iterable[CanonicalSemanticRangeSource],
) -> set[tuple[str, str]]:
    return {(source.kind.strip().upper(), source.ref.strip()) for source in tuple(sources)}


def validate_route_semantic_coverage(
    routes: Iterable[ContextReasoningRoute],
    sources: Iterable[CanonicalSemanticRangeSource],
) -> None:
    identities = _source_identities(sources)
    for route in tuple(routes):
        requirements: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
            (route.existing_work_refs, ("EXISTING_WORK",)),
            (route.prior_attempt_refs, ("PRIOR_ATTEMPT",)),
            (route.regression_refs, ("REGRESSION",)),
        ]
        for refs, kinds in requirements:
            for ref in refs:
                if not any((kind, ref) in identities for kind in kinds):
                    raise ValueError("CONTEXT_ROUTE_SEMANTIC_SOURCE_MISSING:" + ref)
        for ref in route.authority_refs:
            if not any(
                (kind, ref) in identities
                for kind in ("CURRENT_CONTROL", "CONTINUATION")
            ):
                raise ValueError("CONTEXT_ROUTE_SEMANTIC_SOURCE_MISSING:" + ref)


class ConfiguredContextReasoningResolver:
    """Resolve structural refs from deployment routing, never semantic content.

    The returned checkpoint is bound to Runtime operational version. This resolver
    is intentionally used only inside ContextBoundPreModelGateway, where every
    selected ref must then be fresh-read from canonical provider sources before
    the first model can run.
    """

    def __init__(self, routes: Iterable[ContextReasoningRoute]) -> None:
        by_task: dict[str, ContextReasoningRoute] = {}
        for route in tuple(routes):
            if route.task in by_task:
                raise ValueError("CONTEXT_REASONING_ROUTE_TASK_DUPLICATE:" + route.task)
            by_task[route.task] = route
        if not by_task:
            raise ValueError("CONTEXT_REASONING_ROUTES_REQUIRED")
        self._by_task = by_task

    def resolve(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        checkpoint_cue: str,
    ) -> PreModelContextResolution | None:
        del request, checkpoint_cue
        route = self._by_task.get(state.task)
        if route is None:
            return None
        return PreModelContextResolution(
            checkpoint_id=f"R{state.version}",
            task=state.task,
            operational_version=state.version,
            authority_refs=route.authority_refs,
            existing_work_refs=route.existing_work_refs,
            prior_attempt_refs=route.prior_attempt_refs,
            regression_refs=route.regression_refs,
            existing_work_lookup_complete=True,
            prior_attempt_lookup_complete=True,
            regression_lookup_complete=True,
            reuse_disposition=route.reuse_disposition,
        )


def _required_text(values: Mapping[str, str], key: str) -> str:
    value = str(values.get(key, "")).strip()
    if not value:
        raise ValueError("MISSING_CONFIG:" + key)
    return value


class _RuntimeTelemetryReasoningSink:
    """One-way adapter from content-free reasoning observations to RuntimeTelemetry."""

    def __init__(self, telemetry: RuntimeTelemetry) -> None:
        self._telemetry = telemetry

    def record(self, observation: ContextReasoningObservation) -> None:
        self._telemetry.record_context_reasoning_observation(observation)


def build_runtime_reasoning_consumer(
    values: Mapping[str, str],
    *,
    state_source: OperationalStateSource,
    control_plane: ControlPlaneGateway,
    reader: CanonicalRangeReader | None = None,
    model: ContextBoundIntentModel | None = None,
    telemetry: RuntimeTelemetry | None = None,
) -> ContextBoundReasoningConsumer:
    """Compose the production first-model reasoning seam from deployment config.

    No private source ID or canonical semantic text is embedded in public code.
    Route metadata selects logical refs; provider-backed semantic reading remains
    fresh and fail-closed; the interaction-facing consumer accepts only raw Hao
    text plus run/event/sequence identity. If Runtime telemetry is configured for
    the process, the existing ingress is decorated once with content-free stage
    observation; no second reasoning or MCP telemetry path is created.
    """

    sources = load_canonical_semantic_sources_json(
        _required_text(values, "HAO_CANONICAL_SEMANTIC_SOURCES_JSON")
    )
    routes = load_context_reasoning_routes_json(
        _required_text(values, "HAO_CONTEXT_REASONING_ROUTES_JSON")
    )
    validate_route_semantic_coverage(routes, sources)

    semantic_reader = reader or GoogleWorkspaceCanonicalRangeReader()
    intent_model = model or build_openai_context_bound_intent_boundary(
        model=_required_text(values, "HAO_REASONING_MODEL")
    )
    pre_model = ContextBoundPreModelGateway(
        PreModelContextGateway(ConfiguredContextReasoningResolver(routes)),
        ConfiguredCanonicalSemanticsResolver(semantic_reader, sources),
    )
    ingress = ContextBoundReasoningIngress(
        pre_model=pre_model,
        model=intent_model,
        control_plane=control_plane,
    )

    effective_telemetry = telemetry
    telemetry_configured = bool(str(values.get("HAO_OTEL_ENDPOINT", "")).strip())
    if effective_telemetry is None and telemetry_configured:
        effective_telemetry = active_runtime_telemetry()
        if effective_telemetry is None:
            raise ValueError("RUNTIME_REASONING_TELEMETRY_NOT_CONFIGURED")
    if effective_telemetry is not None:
        ingress = ObservableContextBoundReasoningIngress(
            ingress,
            _RuntimeTelemetryReasoningSink(effective_telemetry),
        )

    return ContextBoundReasoningConsumer(
        state_source=state_source,
        ingress=ingress,
    )