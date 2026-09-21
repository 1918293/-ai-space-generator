from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.active_work_mutation_plan import (
    ActiveWorkReleaseReason,
    plan_active_work_claim,
    plan_active_work_refresh,
    plan_active_work_release,
)
from src.active_work_signal import ActiveWorkSignal, parse_active_work_signal
from src.google_docs_active_work_writer import (
    ActiveWorkWriteEffect,
    GoogleDocsActiveWorkSignalWriter,
    render_active_work_signal,
)


CONTROL = """HAO_ACTIVE_WORK_SIGNAL_V1
ROLE=NON_AUTHORITY_REBUILDABLE_RUNTIME_SIGNAL
PURPOSE=CROSS_CHAT_ACTIVE_WORK_COORDINATION_ONLY
FORMAL_AUTHORITY=GOOGLE_DRIVE_HAO_SYSTEM
HISTORY=NONE
PRIVATE_PAYLOAD=DENY
TASK_DATABASE=NO
WRITE_CONTROL=GOOGLE_DOC_REVISION_CAS
MAX_SLOTS=4
TTL_REQUIRED=TRUE
FULL_CAPACITY=FAIL_CLOSED_OR_WAIT
"""
FP_A = "sha256:" + "a" * 64
START = datetime.fromisoformat("2026-09-17T11:22:28+08:00")
EXPIRY = datetime.fromisoformat("2026-09-17T11:42:28+08:00")
TITLE = "Hao System｜Active Work Signal｜EXP_NON_AUTHORITY｜v0.1"


def empty_slot(index, generation=0):
    return f"""S{index}_STATUS=EMPTY
S{index}_GENERATION={generation}
S{index}_RUN_KEY=NONE
S{index}_OBJECTIVE=NONE
S{index}_TARGET=NONE
S{index}_EXECUTION_LANE=NONE
S{index}_WORK_STATE=NONE
S{index}_STARTED_AT=NONE
S{index}_UPDATED_AT=NONE
S{index}_EXPIRES_AT=NONE
S{index}_EXPECTED_DELTA=NONE
S{index}_READBACK_STATE=NONE
S{index}_OWNER=NONE
"""


def legacy_empty_signal():
    return CONTROL + empty_slot(1, 3) + empty_slot(2) + empty_slot(3) + empty_slot(4)


def active_signal():
    s1 = f"""S1_STATUS=ACTIVE
S1_GENERATION=4
S1_WORK_KEY=RV-051
S1_INTENT_FINGERPRINT={FP_A}
S1_RUN_KEY=RUN-RV051-G4
S1_OBJECTIVE=VERIFY_R051
S1_TARGET=REQUIREMENTS:RV-051
S1_EXECUTION_LANE=SINGLE_WRITE_GATEWAY
S1_WORK_STATE=VERIFYING
S1_STARTED_AT={START.isoformat()}
S1_UPDATED_AT={START.isoformat()}
S1_EXPIRES_AT={EXPIRY.isoformat()}
S1_EXPECTED_DELTA=BOUNDED_VERIFICATION
S1_READBACK_STATE=PENDING
S1_OWNER=CHATGPT_CURRENT_CHAT
"""
    return CONTROL + s1 + empty_slot(2) + empty_slot(3) + empty_slot(4)


def document(text, revision="rev-1", *, tab_id="t.0", title=TITLE, child_tabs=None):
    content = [{"endIndex": 1, "sectionBreak": {}}]
    index = 1
    for line in text.splitlines(keepends=True):
        end = index + len(line.encode("utf-16-le")) // 2
        content.append(
            {
                "startIndex": index,
                "endIndex": end,
                "paragraph": {
                    "elements": [
                        {
                            "startIndex": index,
                            "endIndex": end,
                            "textRun": {"content": line},
                        }
                    ]
                },
            }
        )
        index = end
    return {
        "title": title,
        "revisionId": revision,
        "tabs": [
            {
                "tabProperties": {"tabId": tab_id},
                "documentTab": {"body": {"content": content}},
                "childTabs": child_tabs or [],
            }
        ],
    }


class Request:
    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.result


class DocumentsResource:
    def __init__(self, gets, *, batch_error=None):
        self.gets = list(gets)
        self.batch_error = batch_error
        self.get_calls = []
        self.batch_calls = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        item = self.gets.pop(0)
        if isinstance(item, Exception):
            return Request(error=item)
        return Request(result=item)

    def batchUpdate(self, **kwargs):
        self.batch_calls.append(kwargs)
        if self.batch_error is not None:
            return Request(error=self.batch_error)
        return Request(result={"writeControl": {"requiredRevisionId": "rev-1"}})


class DocsService:
    def __init__(self, *gets, batch_error=None):
        self.resource = DocumentsResource(gets, batch_error=batch_error)

    def documents(self):
        return self.resource


def claim_plan():
    before = parse_active_work_signal(legacy_empty_signal()).slots[0]
    return plan_active_work_claim(
        before,
        expected_generation=3,
        run_key="RUN-RV051-G4",
        objective="VERIFY_R051",
        target="REQUIREMENTS:RV-051",
        execution_lane="SINGLE_WRITE_GATEWAY",
        work_state="VERIFYING",
        started_at=START,
        expires_at=EXPIRY,
        expected_delta="BOUNDED_VERIFICATION",
        readback_state="PENDING",
        owner="CHATGPT_CURRENT_CHAT",
        work_key="RV-051",
        intent_fingerprint=FP_A,
    )


def signal_after(plan):
    before = parse_active_work_signal(legacy_empty_signal())
    return ActiveWorkSignal(
        tuple(plan.after if slot.slot_id == plan.slot_id else slot for slot in before.slots)
    )


def test_renderer_roundtrips_legacy_and_identity_signals():
    legacy = parse_active_work_signal(legacy_empty_signal())
    identity = parse_active_work_signal(active_signal())
    assert parse_active_work_signal(render_active_work_signal(legacy)) == legacy
    assert parse_active_work_signal(render_active_work_signal(identity)) == identity


def test_claim_uses_atomic_batch_revision_cas_then_readback_verifies():
    plan = claim_plan()
    expected = signal_after(plan)
    service = DocsService(
        document(legacy_empty_signal(), "rev-1"),
        document(render_active_work_signal(expected), "rev-2"),
    )
    writer = GoogleDocsActiveWorkSignalWriter("doc-1", service=service)

    result = writer.apply(plan)

    assert result.effect == ActiveWorkWriteEffect.VERIFIED
    assert result.code == "ACTIVE_WORK_WRITE_VERIFIED"
    assert result.revision_before == "rev-1"
    assert result.revision_after == "rev-2"
    assert service.resource.get_calls == [
        {"documentId": "doc-1", "includeTabsContent": True},
        {"documentId": "doc-1", "includeTabsContent": True},
    ]
    assert len(service.resource.batch_calls) == 1
    call = service.resource.batch_calls[0]
    assert call["documentId"] == "doc-1"
    body = call["body"]
    assert body["writeControl"] == {"requiredRevisionId": "rev-1"}
    assert [next(iter(item)) for item in body["requests"]] == [
        "deleteContentRange",
        "insertText",
    ]
    delete_range = body["requests"][0]["deleteContentRange"]["range"]
    insert = body["requests"][1]["insertText"]
    assert delete_range["tabId"] == "t.0"
    assert insert["location"] == {"index": 1, "tabId": "t.0"}
    assert "S1_WORK_KEY=RV-051" in insert["text"]
    assert f"S1_INTENT_FINGERPRINT={FP_A}" in insert["text"]


def test_stale_same_generation_before_snapshot_blocks_before_provider_mutation():
    original = parse_active_work_signal(active_signal()).slots[0]
    plan = plan_active_work_release(
        original,
        expected_generation=4,
        expected_ref=original.ref,
        reason=ActiveWorkReleaseReason.COMPLETE,
    )
    refreshed = replace(
        original,
        updated_at=START + timedelta(minutes=2),
        expires_at=EXPIRY + timedelta(minutes=2),
        work_state="READBACK",
    )
    current = parse_active_work_signal(active_signal())
    stale_signal = ActiveWorkSignal(
        tuple(refreshed if slot.slot_id == "S1" else slot for slot in current.slots)
    )
    service = DocsService(document(render_active_work_signal(stale_signal), "rev-new"))
    writer = GoogleDocsActiveWorkSignalWriter("doc-1", service=service)

    with pytest.raises(ValueError, match="ACTIVE_WORK_WRITE_STALE_PLAN_SNAPSHOT"):
        writer.apply(plan)
    assert service.resource.batch_calls == []


def test_batch_transport_error_is_unknown_effect_and_not_auto_replayed():
    plan = claim_plan()
    service = DocsService(
        document(legacy_empty_signal(), "rev-1"),
        batch_error=RuntimeError("connection dropped after dispatch"),
    )
    writer = GoogleDocsActiveWorkSignalWriter("doc-1", service=service)

    result = writer.apply(plan)

    assert result.effect == ActiveWorkWriteEffect.UNKNOWN_EFFECT
    assert result.code == "ACTIVE_WORK_WRITE_UNKNOWN_EFFECT:RuntimeError"
    assert len(service.resource.batch_calls) == 1
    assert len(service.resource.get_calls) == 1


def test_successful_batch_with_readback_mismatch_is_unsynced():
    plan = claim_plan()
    service = DocsService(
        document(legacy_empty_signal(), "rev-1"),
        document(legacy_empty_signal(), "rev-2"),
    )
    writer = GoogleDocsActiveWorkSignalWriter("doc-1", service=service)

    result = writer.apply(plan)

    assert result.effect == ActiveWorkWriteEffect.UNSYNCED
    assert result.code == "ACTIVE_WORK_WRITE_READBACK_MISMATCH"


def test_successful_batch_without_revision_advance_is_unsynced():
    plan = claim_plan()
    expected = signal_after(plan)
    service = DocsService(
        document(legacy_empty_signal(), "rev-1"),
        document(render_active_work_signal(expected), "rev-1"),
    )
    writer = GoogleDocsActiveWorkSignalWriter("doc-1", service=service)

    result = writer.apply(plan)

    assert result.effect == ActiveWorkWriteEffect.UNSYNCED
    assert result.code == "ACTIVE_WORK_WRITE_REVISION_NOT_ADVANCED"


def test_release_write_removes_identity_pair_on_readback():
    before_signal = parse_active_work_signal(active_signal())
    before = before_signal.slots[0]
    plan = plan_active_work_release(
        before,
        expected_generation=4,
        expected_ref=before.ref,
        reason=ActiveWorkReleaseReason.EXPIRE,
    )
    expected = ActiveWorkSignal(
        tuple(plan.after if slot.slot_id == "S1" else slot for slot in before_signal.slots)
    )
    rendered = render_active_work_signal(expected)
    assert "S1_WORK_KEY=" not in rendered
    assert "S1_INTENT_FINGERPRINT=" not in rendered

    service = DocsService(
        document(active_signal(), "rev-1"),
        document(rendered, "rev-2"),
    )
    result = GoogleDocsActiveWorkSignalWriter("doc-1", service=service).apply(plan)
    assert result.effect == ActiveWorkWriteEffect.VERIFIED


def test_writer_rejects_wrong_title_or_child_tabs_before_batch():
    plan = claim_plan()
    for invalid in (
        document(legacy_empty_signal(), title="Wrong document"),
        document(legacy_empty_signal(), child_tabs=[{"tabProperties": {"tabId": "child"}}]),
    ):
        service = DocsService(invalid)
        writer = GoogleDocsActiveWorkSignalWriter("doc-1", service=service)
        with pytest.raises(ValueError):
            writer.apply(plan)
        assert service.resource.batch_calls == []


def test_refresh_plan_cannot_write_changed_identity_because_before_snapshot_and_plan_lock_it():
    before_signal = parse_active_work_signal(active_signal())
    before = before_signal.slots[0]
    plan = plan_active_work_refresh(
        before,
        expected_generation=4,
        expected_ref=before.ref,
        updated_at=START + timedelta(minutes=5),
        expires_at=EXPIRY + timedelta(minutes=5),
        work_state="READBACK",
    )
    assert plan.before.work_key == plan.after.work_key == "RV-051"
    assert plan.before.intent_fingerprint == plan.after.intent_fingerprint == FP_A
