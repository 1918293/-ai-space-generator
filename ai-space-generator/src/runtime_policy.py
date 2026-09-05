from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .control_gateway import TaskExecutionPolicy
from .execution_control import AuthorityStamp
from .google_workspace_adapter import AuthorityFileSource, GoogleWorkspaceSheetsClient
from .operational_state import ActiveOperationalState


@dataclass(frozen=True)
class ConfiguredTaskPolicySpec:
    task: str
    acceptance_criteria: tuple[str, ...]
    authority_sources: tuple[AuthorityFileSource, ...]
    required_acceptance_gate_ids: tuple[str, ...] = ()
    hao_acceptance_required: bool = False
    required_action_tags: tuple[str, ...] = ()
    forbidden_action_tags: tuple[str, ...] = ()


class GoogleAuthorityTaskPolicyProvider:
    """Resolve trusted action policy from exact task config + live Drive versions.

    This intentionally performs a small synchronous metadata read at submission
    time because ControlPlaneGateway's policy seam is synchronous. Correct fresh
    Authority binding is preferred over caching stale versions in a private,
    low-QPS control plane. Execution still performs a second fresh preflight
    immediately before any side effect.
    """

    def __init__(
        self,
        client: GoogleWorkspaceSheetsClient,
        specs: Iterable[ConfiguredTaskPolicySpec],
    ) -> None:
        by_task: dict[str, ConfiguredTaskPolicySpec] = {}
        for spec in specs:
            task = spec.task.strip()
            if not task or task in by_task:
                raise ValueError("INVALID_OR_DUPLICATE_TASK_POLICY")
            if not spec.acceptance_criteria:
                raise ValueError("TASK_POLICY_ACCEPTANCE_REQUIRED")
            refs = [source.ref.strip() for source in spec.authority_sources]
            if not refs or any(not ref for ref in refs) or len(set(refs)) != len(refs):
                raise ValueError("TASK_POLICY_AUTHORITY_SOURCES_REQUIRED")
            by_task[task] = spec
        self._client = client
        self._by_task = by_task

    def resolve(self, state: ActiveOperationalState) -> TaskExecutionPolicy:
        spec = self._by_task.get(state.task.strip())
        if spec is None:
            raise ValueError("TASK_POLICY_UNRESOLVED")
        stamps: tuple[AuthorityStamp, ...] = self._client.current_authority_stamps_sync(
            spec.authority_sources
        )
        refs = tuple(source.ref.strip() for source in spec.authority_sources)
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=spec.acceptance_criteria,
            required_acceptance_gate_ids=spec.required_acceptance_gate_ids,
            hao_acceptance_required=spec.hao_acceptance_required,
            authority_refs=refs,
            authority_stamps=stamps,
            required_action_authority_refs=refs,
            required_action_tags=spec.required_action_tags,
            forbidden_action_tags=spec.forbidden_action_tags,
        )
