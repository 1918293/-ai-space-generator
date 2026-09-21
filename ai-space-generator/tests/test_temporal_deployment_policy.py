from src.temporal_deployment_policy import (
    WorkerRoutingState,
    WorkerVersionEvidence,
    admit_new_worker_version,
    admit_ramp,
    admit_scale_to_zero,
    admit_storage_contract_migration,
    admit_version_delete,
)


def version(
    build_id="build-v1",
    *,
    state=WorkerRoutingState.INACTIVE,
    pollers=True,
    missing=(),
    epochs=(1,),
):
    return WorkerVersionEvidence(
        build_id=build_id,
        routing_state=state,
        has_pollers=pollers,
        missing_task_queues=missing,
        supported_storage_epochs=epochs,
    )


def test_ramp_requires_pollers_and_all_expected_task_queues():
    assert admit_ramp(version(pollers=False)).code == "WORKER_POLLER_REQUIRED"
    assert (
        admit_ramp(version(missing=("hao-runtime-v2",))).code
        == "EXPECTED_TASK_QUEUES_NOT_POLLING"
    )
    decision = admit_ramp(version())
    assert decision.allowed is True
    assert decision.code == "WORKER_READY_FOR_RAMP"


def test_current_draining_and_drained_versions_cannot_be_reused_as_candidates():
    for state in (
        WorkerRoutingState.CURRENT,
        WorkerRoutingState.DRAINING,
        WorkerRoutingState.DRAINED,
    ):
        assert admit_ramp(version(state=state)).allowed is False


def test_version_capacity_blocks_before_platform_limit_or_reserved_headroom():
    assert admit_new_worker_version(
        existing_version_count=98,
        platform_version_limit=100,
        required_headroom=1,
    ).allowed
    blocked = admit_new_worker_version(
        existing_version_count=99,
        platform_version_limit=100,
        required_headroom=1,
    )
    assert blocked.allowed is False
    assert blocked.code == "WORKER_VERSION_CAPACITY_EXHAUSTED"


def test_storage_contract_migration_ignores_drained_but_not_active_incompatible_versions():
    active_old = version("build-v1", state=WorkerRoutingState.DRAINING, epochs=(1,))
    drained_old = version(
        "build-v0",
        state=WorkerRoutingState.DRAINED,
        pollers=False,
        epochs=(1,),
    )
    current_new = version("build-v2", state=WorkerRoutingState.CURRENT, epochs=(1, 2))

    blocked = admit_storage_contract_migration(
        (drained_old, active_old, current_new),
        target_epoch=2,
    )
    assert blocked.allowed is False
    assert blocked.code == "ACTIVE_WORKER_STORAGE_EPOCH_INCOMPATIBLE:build-v1"

    allowed = admit_storage_contract_migration(
        (drained_old, current_new),
        target_epoch=2,
    )
    assert allowed.allowed is True


def test_first_drained_observation_is_not_enough_to_remove_compute():
    drained = version(
        state=WorkerRoutingState.DRAINED,
        pollers=False,
    )
    first = admit_scale_to_zero(drained, consecutive_drained_observations=1)
    assert first.allowed is False
    assert first.code == "DRAINAGE_STABILITY_NOT_PROVEN"

    stable = admit_scale_to_zero(drained, consecutive_drained_observations=2)
    assert stable.allowed is True


def test_drained_version_with_poller_fails_closed():
    drained_with_poller = version(state=WorkerRoutingState.DRAINED, pollers=True)
    decision = admit_scale_to_zero(
        drained_with_poller,
        consecutive_drained_observations=3,
    )
    assert decision.allowed is False
    assert decision.code == "DRAINED_VERSION_STILL_HAS_POLLERS"


def test_delete_requires_drainage_stability_and_retention():
    drained = version(state=WorkerRoutingState.DRAINED, pollers=False)
    blocked = admit_version_delete(
        drained,
        consecutive_drained_observations=2,
        retention_elapsed=False,
    )
    assert blocked.allowed is False
    assert blocked.code == "WORKER_VERSION_RETENTION_NOT_ELAPSED"

    allowed = admit_version_delete(
        drained,
        consecutive_drained_observations=2,
        retention_elapsed=True,
    )
    assert allowed.allowed is True
    assert allowed.code == "WORKER_VERSION_DELETE_ALLOWED"
