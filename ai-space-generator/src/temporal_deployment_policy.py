from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkerRoutingState(StrEnum):
    INACTIVE = "inactive"
    RAMPING = "ramping"
    CURRENT = "current"
    DRAINING = "draining"
    DRAINED = "drained"


@dataclass(frozen=True)
class WorkerVersionEvidence:
    build_id: str
    routing_state: WorkerRoutingState
    has_pollers: bool
    missing_task_queues: tuple[str, ...] = ()
    supported_storage_epochs: tuple[int, ...] = ()


@dataclass(frozen=True)
class DeploymentGateDecision:
    allowed: bool
    code: str


def admit_new_worker_version(
    *,
    existing_version_count: int,
    platform_version_limit: int,
    required_headroom: int = 0,
) -> DeploymentGateDecision:
    """Fail closed before registering a version that would consume reserved capacity."""
    if existing_version_count < 0 or platform_version_limit < 1 or required_headroom < 0:
        return DeploymentGateDecision(False, "INVALID_VERSION_CAPACITY_INPUT")
    usable_limit = platform_version_limit - required_headroom
    if usable_limit < 1:
        return DeploymentGateDecision(False, "VERSION_CAPACITY_HEADROOM_EXHAUSTED")
    if existing_version_count + 1 > usable_limit:
        return DeploymentGateDecision(False, "WORKER_VERSION_CAPACITY_EXHAUSTED")
    return DeploymentGateDecision(True, "WORKER_VERSION_CAPACITY_AVAILABLE")


def admit_ramp(candidate: WorkerVersionEvidence) -> DeploymentGateDecision:
    """A candidate cannot receive Temporal routing until direct server evidence is ready."""
    if not candidate.build_id.strip():
        return DeploymentGateDecision(False, "WORKER_BUILD_ID_REQUIRED")
    if candidate.routing_state in {WorkerRoutingState.CURRENT, WorkerRoutingState.DRAINING}:
        return DeploymentGateDecision(False, "WORKER_VERSION_NOT_RAMP_CANDIDATE")
    if candidate.routing_state == WorkerRoutingState.DRAINED:
        return DeploymentGateDecision(False, "DRAINED_VERSION_CANNOT_RAMP")
    if not candidate.has_pollers:
        return DeploymentGateDecision(False, "WORKER_POLLER_REQUIRED")
    if candidate.missing_task_queues:
        return DeploymentGateDecision(False, "EXPECTED_TASK_QUEUES_NOT_POLLING")
    return DeploymentGateDecision(True, "WORKER_READY_FOR_RAMP")


def admit_storage_contract_migration(
    versions: tuple[WorkerVersionEvidence, ...],
    *,
    target_epoch: int,
) -> DeploymentGateDecision:
    """Destructive storage contract changes require every non-drained version to support the target."""
    if target_epoch < 1:
        return DeploymentGateDecision(False, "STORAGE_TARGET_EPOCH_INVALID")
    for version in versions:
        if version.routing_state == WorkerRoutingState.DRAINED:
            continue
        if target_epoch not in version.supported_storage_epochs:
            return DeploymentGateDecision(
                False,
                f"ACTIVE_WORKER_STORAGE_EPOCH_INCOMPATIBLE:{version.build_id}",
            )
    return DeploymentGateDecision(True, "STORAGE_CONTRACT_MIGRATION_ALLOWED")


def admit_scale_to_zero(
    version: WorkerVersionEvidence,
    *,
    consecutive_drained_observations: int,
    required_stable_observations: int = 2,
) -> DeploymentGateDecision:
    """Do not remove compute from a pinned version on the first eventually-consistent drained read."""
    if required_stable_observations < 1 or consecutive_drained_observations < 0:
        return DeploymentGateDecision(False, "INVALID_DRAINAGE_OBSERVATION_INPUT")
    if version.routing_state != WorkerRoutingState.DRAINED:
        return DeploymentGateDecision(False, "WORKER_VERSION_NOT_DRAINED")
    if version.has_pollers:
        return DeploymentGateDecision(False, "DRAINED_VERSION_STILL_HAS_POLLERS")
    if consecutive_drained_observations < required_stable_observations:
        return DeploymentGateDecision(False, "DRAINAGE_STABILITY_NOT_PROVEN")
    return DeploymentGateDecision(True, "WORKER_COMPUTE_CAN_SCALE_ZERO")


def admit_version_delete(
    version: WorkerVersionEvidence,
    *,
    consecutive_drained_observations: int,
    retention_elapsed: bool,
    required_stable_observations: int = 2,
) -> DeploymentGateDecision:
    scale = admit_scale_to_zero(
        version,
        consecutive_drained_observations=consecutive_drained_observations,
        required_stable_observations=required_stable_observations,
    )
    if not scale.allowed:
        return scale
    if not retention_elapsed:
        return DeploymentGateDecision(False, "WORKER_VERSION_RETENTION_NOT_ELAPSED")
    return DeploymentGateDecision(True, "WORKER_VERSION_DELETE_ALLOWED")
