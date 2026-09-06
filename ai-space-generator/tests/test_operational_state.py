from pathlib import Path

from src.execution_control import Mode
from src.operational_state import (
    CommandActor,
    OperationalCommand,
    SQLiteOperationalStateStore,
    execution_record_from_operational_state,
    explicit_user_mode,
)


def store(tmp_path: Path):
    return SQLiteOperationalStateStore(str(tmp_path / "operational.sqlite3"))


def test_only_explicit_leading_mode_tokens_are_mode_commands():
    assert explicit_user_mode("EXP > explore") == Mode.EXP
    assert explicit_user_mode("SYS Auto > record") == Mode.SYS
    assert explicit_user_mode("Auto > continue") is None
    assert explicit_user_mode("Auto Loop") is None
    assert explicit_user_mode("繼續") is None
    assert explicit_user_mode("quoted [MODE=SYS] does not switch") is None
    assert explicit_user_mode("The word SYS later is not a command") is None


def test_auto_and_continue_cannot_change_active_mode(tmp_path):
    state_store = store(tmp_path)
    state_store.initialize(mode=Mode.EXP, task="Stable task")
    for index, text in enumerate(("Auto", "Auto Loop >", "繼續", "Auto > next"), start=1):
        update = state_store.apply(
            OperationalCommand(f"E-{index}", CommandActor.USER, text)
        )
        assert update.state.mode == Mode.EXP
        assert update.applied is False


def test_only_user_actor_can_switch_mode(tmp_path):
    state_store = store(tmp_path)
    state_store.initialize(mode=Mode.EXP, task="Stable task")
    model = state_store.apply(
        OperationalCommand("E-MODEL", CommandActor.MODEL, "SYS > switch")
    )
    system = state_store.apply(
        OperationalCommand("E-SYSTEM", CommandActor.SYSTEM, "SYS > switch")
    )
    projection = state_store.apply(
        OperationalCommand("E-PROJ", CommandActor.PROJECTION, "SYS > switch")
    )
    assert model.state.mode == Mode.EXP
    assert system.state.mode == Mode.EXP
    assert projection.state.mode == Mode.EXP

    user = state_store.apply(
        OperationalCommand("E-USER", CommandActor.USER, "SYS Auto > formalize")
    )
    assert user.applied is True
    assert user.state.mode == Mode.SYS


def test_quoted_or_rendered_header_cannot_feed_mode_back_into_state(tmp_path):
    state_store = store(tmp_path)
    state_store.initialize(mode=Mode.EXP, task="Stable task")
    update = state_store.apply(
        OperationalCommand(
            "E-HEADER",
            CommandActor.USER,
            "[MODE=SYS][TASK=Other] this is quoted output",
        )
    )
    assert update.state.mode == Mode.EXP
    assert update.state.task == "Stable task"


def test_non_user_task_change_is_ignored(tmp_path):
    state_store = store(tmp_path)
    state_store.initialize(mode=Mode.EXP, task="Stable task")
    update = state_store.apply(
        OperationalCommand(
            "E-MODEL-TASK",
            CommandActor.MODEL,
            "new task suggestion",
            explicit_task="Model invented task",
        )
    )
    assert update.applied is False
    assert update.state.task == "Stable task"
    assert update.code == "NON_USER_TASK_CHANGE_IGNORED"


def test_explicit_user_task_and_mode_change_is_durable_across_store_restart(tmp_path):
    first = store(tmp_path)
    initial = first.initialize(mode=Mode.EXP, task="Old task")
    update = first.apply(
        OperationalCommand(
            "E-CHANGE",
            CommandActor.USER,
            "EXE > execute",
            explicit_task="New task",
            expected_version=initial.version,
        )
    )
    assert update.state.mode == Mode.EXE
    assert update.state.task == "New task"
    assert update.state.version == 2

    restarted = store(tmp_path)
    restored = restarted.get()
    assert restored.mode == Mode.EXE
    assert restored.task == "New task"
    assert restored.version == 2


def test_duplicate_event_is_idempotent(tmp_path):
    state_store = store(tmp_path)
    state_store.initialize(mode=Mode.EXP, task="Stable task")
    command = OperationalCommand("E-ONCE", CommandActor.USER, "SYS > switch")
    first = state_store.apply(command)
    second = state_store.apply(command)
    assert first.state.version == 2
    assert second.state.version == 2
    assert second.code == "EVENT_ALREADY_APPLIED"


def test_stale_expected_version_cannot_overwrite_newer_state(tmp_path):
    state_store = store(tmp_path)
    initial = state_store.initialize(mode=Mode.EXP, task="Stable task")
    state_store.apply(
        OperationalCommand(
            "E-1",
            CommandActor.USER,
            "SYS > switch",
            expected_version=initial.version,
        )
    )
    stale = state_store.apply(
        OperationalCommand(
            "E-2",
            CommandActor.USER,
            "EXE > stale writer",
            expected_version=initial.version,
        )
    )
    assert stale.applied is False
    assert stale.code == "STALE_OPERATIONAL_STATE"
    assert stale.state.mode == Mode.SYS


def test_execution_record_uses_runtime_owned_mode_and_task(tmp_path):
    state_store = store(tmp_path)
    state_store.initialize(mode=Mode.INT, task="Runtime task")
    current = state_store.get()
    run = execution_record_from_operational_state(
        current,
        run_id="RUN-STATE",
        goal_valid=True,
        acceptance_criteria=("runtime state wins",),
    )
    assert run.mode == Mode.INT
    assert run.task == "Runtime task"
