from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .control_gateway import (
    PreModelContextRequest,
    PreModelContextResolution,
)
from .operational_state import ActiveOperationalState


@dataclass(frozen=True)
class HaoAuthorityRoutes:
    """Runtime-configured logical routes to existing Hao System authorities.

    Values are supplied by deployment configuration. The public codebase must
    not embed Hao's private Drive file IDs or credentials.
    """

    current_owner: str
    requirements_owner: str
    continuation_projection: str
    regression_owner: str
    prior_attempt_owner: str


@dataclass(frozen=True)
class HaoCanonicalCurrent:
    checkpoint_id: str
    task: str
    operational_version: int
    authority_refs: tuple[str, ...]
    verified: bool


@dataclass(frozen=True)
class HaoLookupResult:
    refs: tuple[str, ...] = ()
    complete: bool = False


@dataclass(frozen=True)
class HaoExistingWorkResult:
    refs: tuple[str, ...] = ()
    complete: bool = False
    reuse_disposition: str = ""


@dataclass(frozen=True)
class HaoCanonicalContextSnapshot:
    checkpoint_id: str
    task: str
    operational_version: int
    authority_refs: tuple[str, ...]
    existing_work_refs: tuple[str, ...]
    prior_attempt_refs: tuple[str, ...]
    regression_refs: tuple[str, ...]
    existing_work_lookup_complete: bool
    prior_attempt_lookup_complete: bool
    regression_lookup_complete: bool
    reuse_disposition: str


class HaoDriveAuthorityReader(Protocol):
    """Read-only adapter contract for existing canonical Hao Drive sources.

    Implementations may use Google Drive/Docs/Sheets APIs or another approved
    read surface, but must return direct source references rather than turning a
    projection or model summary into Authority.
    """

    def resolve_current(
        self,
        routes: HaoAuthorityRoutes,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        checkpoint_cue: str,
    ) -> HaoCanonicalCurrent | None: ...

    def lookup_existing_work(
        self,
        routes: HaoAuthorityRoutes,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
    ) -> HaoExistingWorkResult: ...

    def lookup_prior_attempts(
        self,
        routes: HaoAuthorityRoutes,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
    ) -> HaoLookupResult: ...

    def lookup_regressions(
        self,
        routes: HaoAuthorityRoutes,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
    ) -> HaoLookupResult: ...


class HaoCanonicalAuthoritySource(Protocol):
    def read_context(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        checkpoint_cue: str,
    ) -> HaoCanonicalContextSnapshot | None: ...


class HaoDriveCanonicalAuthoritySource:
    """Existing-first orchestration over Hao's canonical Drive authorities.

    The continuation document can help resolve a checkpoint, but `verified=True`
    is only valid when the reader has cross-checked task-matched canonical
    Authority according to the configured routes. This class never promotes the
    continuation projection into a second Authority owner.
    """

    def __init__(self, reader: HaoDriveAuthorityReader, routes: HaoAuthorityRoutes) -> None:
        self._reader = reader
        self._routes = routes

    def read_context(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        checkpoint_cue: str,
    ) -> HaoCanonicalContextSnapshot | None:
        current = self._reader.resolve_current(
            self._routes,
            state,
            request,
            checkpoint_cue,
        )
        if current is None or not current.verified:
            return None

        existing = self._reader.lookup_existing_work(self._routes, state, request)
        prior = self._reader.lookup_prior_attempts(self._routes, state, request)
        regressions = self._reader.lookup_regressions(self._routes, state, request)
        return HaoCanonicalContextSnapshot(
            checkpoint_id=current.checkpoint_id,
            task=current.task,
            operational_version=current.operational_version,
            authority_refs=current.authority_refs,
            existing_work_refs=existing.refs,
            prior_attempt_refs=prior.refs,
            regression_refs=regressions.refs,
            existing_work_lookup_complete=existing.complete,
            prior_attempt_lookup_complete=prior.complete,
            regression_lookup_complete=regressions.complete,
            reuse_disposition=existing.reuse_disposition,
        )


class HaoCanonicalPreModelResolver:
    """Production resolver for Runtime v2's first-model admission seam."""

    def __init__(self, source: HaoCanonicalAuthoritySource) -> None:
        self._source = source

    def resolve(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        checkpoint_cue: str,
    ) -> PreModelContextResolution | None:
        snapshot = self._source.read_context(state, request, checkpoint_cue)
        if snapshot is None:
            return None
        return PreModelContextResolution(
            checkpoint_id=snapshot.checkpoint_id,
            task=snapshot.task,
            operational_version=snapshot.operational_version,
            authority_refs=snapshot.authority_refs,
            existing_work_refs=snapshot.existing_work_refs,
            prior_attempt_refs=snapshot.prior_attempt_refs,
            regression_refs=snapshot.regression_refs,
            existing_work_lookup_complete=snapshot.existing_work_lookup_complete,
            prior_attempt_lookup_complete=snapshot.prior_attempt_lookup_complete,
            regression_lookup_complete=snapshot.regression_lookup_complete,
            reuse_disposition=snapshot.reuse_disposition,
        )
