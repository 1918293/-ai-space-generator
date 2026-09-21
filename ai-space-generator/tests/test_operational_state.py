from dataclasses import replace
from pathlib import Path
import sqlite3

from src.execution_control import Mode
from src.operational_state import (
    CommandActor,
    OperationalCommand,
    SQLiteOperationalStateStore,
    TaskChangeAuthority,
    execution_record_from_operational_state,
    explicit_user_mode,
    explicit_user_task,
)


_TASK_CHANGE_KEY = b"hao-runtime-v2-task-change-test-key-32-bytes+"


def authority():
    return TaskChangeAuthority(_TASK_CHANGE_KEY)


def store(tmp_path: Path):
    return SQLiteOperationalStateStore(
        str(tmp_path / "operational.sqlite3"),
        task_change_authority=authority(),
    )


def test_only_explicit_leading_mode_tokens_are_mode_commands():
    assert explicit_user_mode("EXP > explore") == Mode.EXP
    assert explicit_user_mode("SYS Auto > record") == Mode.SYS
    assert explicit_user_mode("Auto > continue") is None
    assert explicit_user_mode("Auto Loop") is None
    assert explicit_user_mode("繼續") is None
    assert explicit_user_mode("quoted [MODE=SYS] does not switch") is None
    assert explicit_user_mode("The word SYS later is not a command") is None


def test_only_explicit_task_directives_are_durable_task_change_candidates():
    assert explicit_user_task("TASK=New task")[0] == "New task"
    assert explicit_user_task("Auto > TASK: New task")[0] == "New task"
    assert explicit_user_task("SYS Auto > TASK：New task")[0] == "New task"
    assert explicit_user_task("新 TASK：新的工作")[0] == "新的工作"

    assert explicit_user_task("Auto > continue the current task") is None
    assert explicit_user_task("Auto Loop >") is None
    assert explicit_user_task("繼續") is None
    assert explicit_user_task("[attachment:image] rooftop photo") is None
    assert explicit_user_task("[TASK=Other] quoted output") is None
    assert explicit_user_task("The word TASK appears later: Other") is None


def test_auto_and_continue_cannot_change_active_mode_or_task(tmp_path):
    state_store = store(tmp_path)
    state_store.initialize(mode=Mode.EXP, task="Stable task")
    for index, text in enumerate(
        ("Auto", "Auto Loop >", "繼續", "Auto > next", "[attachment:image] rooftop photo"),
        start=1,
    ):
        update = state_store.apply(
            OperationalCommand(f"E-{index}", CommandActor.USER, text)
        )
        assert update.state.mode == Mode.EXP
        assert update.state.task == "Stable task"
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


def test_quoted_or_rendered_header_cannot_feed_mode_or_task_back_into_state(tmp_path):
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


def test_non_user_cannot_mint_or_reuse_task_change_receipt(tmp_path):
    state_store = store(tmp_path)
    current = state_store.initialize(mode=Mode.EXP, task="Stable task")
    auth = authority()
    text = "TASK: Hao task"
    receipt = auth.issue(
        current,
        event_id="E-USER-SOURCE",
        actor=CommandActor.USER,
        text=text,
    )
    assert receipt is not None
    assert auth.issue(
        current,
        event_id="E-MODEL",
        actor=CommandActor.MODEL,
        text=text,
    ) is None

    update = state_store.apply(
        OperationalCommand(
            "E-USER-SOURCE",
            CommandActor.MODEL,
            text,
            task_change_receipt=receipt,
        )
    )
    assert update.applied is False
    assert update.state.task == "Stable task"
    assert update.code == "NON_USER_TASK_CHANGE_IGNORED"


def test_explicit_task_directive_without_runtime_receipt_fails_closed(tmp_path):
    state_store = store(tmp_path)
    state_store.initialize(mode=Mode.EXP, task="Stable task")
    update = state_store.apply(
        OperationalCommand(
            "E-MISSING-RECEIPT",
            CommandActor.USER,
            "SYS > TASK: New task",
        )
    )
    assert update.applied is False
    assert update.state.mode == Mode.EXP
    assert update.state.task == "Stable task"
    assert update.code == "USER_TASK_CHANGE_RECEIPT_REQUIRED"


def test_attachment_or_reactive_upload_cannot_mint_task_change(tmp_path):
    state_store = store(tmp_path)
    current = state_store.initialize(mode=Mode.EXP, task="整理未完成任務")
    auth = authority()

    reactive_text = "[attachment:image] rooftop photo uploaded in reaction to prior output"
    assert auth.issue(
        current,
        event_id="E-REACTIVE-IMAGE",
        actor=CommandActor.USER,
        text=reactive_text,
    ) is None

    update = state_store.apply(
        OperationalCommand(
            "E-REACTIVE-IMAGE",
            CommandActor.USER,
            reactive_text,
        )
    )
    assert update.applied is False
    assert update.state.task == "整理未完成任務"


def test_forged_or_tampered_task_change_receipt_fails_closed_atomically(tmp_path):
    state_store = store(tmp_path)
    current = state_store.initialize(mode=Mode.EXP, task="Stable task")
    auth = authority()
    text = "SYS > TASK: Verified task"
    receipt = auth.issue(
        current,
        event_id="E-FORGE",
        actor=CommandActor.USER,
        text=text,
    )
    assert receipt is not None

    forged = replace(receipt, requested_task="Inferred task")
    update = state_store.apply(
        OperationalCommand(
            "E-FORGE",
            CommandActor.USER,
            text,
            task_change_receipt=forged,
            expected_version=current.version,
        )
    )
    assert update.applied is False
    assert update.state.mode == Mode.EXP
    assert update.state.task == "Stable task"
    assert update.code == "TASK_CHANGE_RECEIPT_INVALID"


def test_receipt_is_bound_to_exact_event_prior_task_and_raw_user_text(tmp_path):
    state_store = store(tmp_path)
    current = state_store.initialize(mode=Mode.EXP, task="Stable task")
    auth = authority()
    text = "Auto > TASK: New task"
    receipt = auth.issue(
        current,
        event_id="E-BOUND",
        actor=CommandActor.USER,
        text=text,
    )
    assert receipt is not None

    wrong_event = state_store.apply(
        OperationalCommand(
            "E-OTHER",
            CommandActor.USER,
            text,
            task_change_receipt=receipt,
        )
    )
    assert wrong_event.applied is False
    assert wrong_event.code == "TASK_CHANGE_RECEIPT_INVALID"
    assert wrong_event.state.task == "Stable task"


def test_explicit_user_task_and_mode_change_requires_signed_receipt_and_is_durable(tmp_path):
    db_path = tmp_path / "operational.sqlite3"
    auth = authority()
    first = SQLiteOperationalStateStore(
        str(db_path),
        task_change_authority=auth,
    )
    initial = first.initialize(mode=Mode.EXP, task="Old task")
    text = "EXE > TASK: New task"
    receipt = auth.issue(
        initial,
        event_id="E-CHANGE",
        actor=CommandActor.USER,
        text=text,
    )
    assert receipt is not None

    update = first.apply(
        OperationalCommand(
            "E-CHANGE",
            CommandActor.USER,
            text,
            task_change_receipt=receipt,
            expected_version=initial.version,
        )
    )
    assert update.applied is True
    assert update.code == "USER_MODE_AND_TASK_RECEIPT_APPLIED"
    assert update.state.mode == Mode.EXE
    assert update.state.task == "New task"
    assert update.state.version == 2

    with sqlite3.connect(db_path) as conn:
        stored_fingerprint = conn.execute(
            "SELECT task_change_receipt_fingerprint "
            "FROM operational_events WHERE event_id = ?",
            ("E-CHANGE",),
        ).fetchone()[0]
    assert stored_fingerprint == receipt.receipt_fingerprint

    restarted = SQLiteOperationalStateStore(
        str(db_path),
        task_change_authority=authority(),
    )
    restored = restarted.get()
    assert restored.mode == Mode.EXE
    assert restored.task == "New task"
    assert restored.version == 2


def test_explicit_task_receipt_requires_runtime_authority_at_apply_boundary(tmp_path):
    db_path = tmp_path / "operational.sqlite3"
    auth = authority()
    unverified_store = SQLiteOperationalStateStore(str(db_path))
    current = unverified_store.initialize(mode=Mode.EXP, task="Stable task")
    text = "TASK: New task"
    receipt = auth.issue(
        current,
        event_id="E-NO-AUTHORITY",
        actor=CommandActor.USER,
        text=text,
    )
    assert receipt is not None

    update = unverified_store.apply(
        OperationalCommand(
            "E-NO-AUTHORITY",
            CommandActor.USER,
            text,
            task_change_receipt=receipt,
        )
    )
    assert update.applied is False
    assert update.state.task == "Stable task"
    assert update.code == "TASK_CHANGE_AUTHORITY_REQUIRED"


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
