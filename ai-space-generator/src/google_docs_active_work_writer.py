from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .active_work_mutation_plan import ActiveWorkMutationPlan
from .active_work_signal import ActiveWorkSignal, ActiveWorkSlot, ActiveWorkStatus, parse_active_work_signal


_DOCUMENTS_SCOPE = "https://www.googleapis.com/auth/documents"
_SIGNAL_TITLE = "Hao System｜Active Work Signal｜EXP_NON_AUTHORITY｜v0.1"
_CONTROL_LINES = (
    "HAO_ACTIVE_WORK_SIGNAL_V1",
    "ROLE=NON_AUTHORITY_REBUILDABLE_RUNTIME_SIGNAL",
    "PURPOSE=CROSS_CHAT_ACTIVE_WORK_COORDINATION_ONLY",
    "FORMAL_AUTHORITY=GOOGLE_DRIVE_HAO_SYSTEM",
    "HISTORY=NONE",
    "PRIVATE_PAYLOAD=DENY",
    "TASK_DATABASE=NO",
    "WRITE_CONTROL=GOOGLE_DOC_REVISION_CAS",
    "MAX_SLOTS=4",
    "TTL_REQUIRED=TRUE",
    "FULL_CAPACITY=FAIL_CLOSED_OR_WAIT",
)


class ActiveWorkWriteEffect(StrEnum):
    VERIFIED = "VERIFIED"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"
    UNSYNCED = "UNSYNCED"


@dataclass(frozen=True)
class ActiveWorkWriteResult:
    effect: ActiveWorkWriteEffect
    code: str
    slot_id: str
    revision_before: str
    revision_after: str = ""


@dataclass(frozen=True)
class _DocumentSnapshot:
    revision_id: str
    tab_id: str
    body_end_index: int
    signal: ActiveWorkSignal


def _slot_lines(slot: ActiveWorkSlot) -> tuple[str, ...]:
    lines = [
        f"{slot.slot_id}_STATUS={slot.status.value}",
        f"{slot.slot_id}_GENERATION={slot.generation}",
    ]
    if slot.status == ActiveWorkStatus.ACTIVE and slot.work_key is not None:
        if slot.intent_fingerprint is None:
            raise ValueError("ACTIVE_WORK_WRITE_IDENTITY_PAIR_INCOMPLETE")
        lines.extend(
            (
                f"{slot.slot_id}_WORK_KEY={slot.work_key}",
                f"{slot.slot_id}_INTENT_FINGERPRINT={slot.intent_fingerprint}",
            )
        )
    elif slot.intent_fingerprint is not None:
        raise ValueError("ACTIVE_WORK_WRITE_IDENTITY_PAIR_INCOMPLETE")

    def timestamp(value: object) -> str:
        return "NONE" if value is None else value.isoformat()

    lines.extend(
        (
            f"{slot.slot_id}_RUN_KEY={slot.run_key}",
            f"{slot.slot_id}_OBJECTIVE={slot.objective}",
            f"{slot.slot_id}_TARGET={slot.target}",
            f"{slot.slot_id}_EXECUTION_LANE={slot.execution_lane}",
            f"{slot.slot_id}_WORK_STATE={slot.work_state}",
            f"{slot.slot_id}_STARTED_AT={timestamp(slot.started_at)}",
            f"{slot.slot_id}_UPDATED_AT={timestamp(slot.updated_at)}",
            f"{slot.slot_id}_EXPIRES_AT={timestamp(slot.expires_at)}",
            f"{slot.slot_id}_EXPECTED_DELTA={slot.expected_delta}",
            f"{slot.slot_id}_READBACK_STATE={slot.readback_state}",
            f"{slot.slot_id}_OWNER={slot.owner}",
        )
    )
    return tuple(lines)


def render_active_work_signal(signal: ActiveWorkSignal) -> str:
    """Render the bounded plain-text Signal representation deterministically."""

    if len(signal.slots) != 4:
        raise ValueError("ACTIVE_WORK_WRITE_SLOT_COUNT_INVALID")
    expected_ids = tuple(f"S{index}" for index in range(1, 5))
    if tuple(slot.slot_id for slot in signal.slots) != expected_ids:
        raise ValueError("ACTIVE_WORK_WRITE_SLOT_ORDER_INVALID")

    sections = ["\n".join(_CONTROL_LINES)]
    sections.extend("\n".join(_slot_lines(slot)) for slot in signal.slots)
    rendered = "\n\n".join(sections) + "\n"
    # Self-parse before any provider mutation so renderer drift fails closed.
    if parse_active_work_signal(rendered) != signal:
        raise ValueError("ACTIVE_WORK_WRITE_RENDER_ROUNDTRIP_INVALID")
    return rendered


def _plain_text_body(body: object) -> tuple[str, int]:
    if not isinstance(body, Mapping):
        raise ValueError("ACTIVE_WORK_WRITE_BODY_REQUIRED")
    content = body.get("content")
    if not isinstance(content, list) or not content:
        raise ValueError("ACTIVE_WORK_WRITE_BODY_CONTENT_REQUIRED")

    text: list[str] = []
    end_index = 0
    for position, structural in enumerate(content):
        if not isinstance(structural, Mapping):
            raise ValueError("ACTIVE_WORK_WRITE_STRUCTURE_INVALID")
        raw_end = structural.get("endIndex")
        if isinstance(raw_end, int):
            end_index = max(end_index, raw_end)
        if position == 0 and "sectionBreak" in structural:
            continue
        paragraph = structural.get("paragraph")
        if not isinstance(paragraph, Mapping):
            raise ValueError("ACTIVE_WORK_WRITE_NON_TEXT_STRUCTURE_UNSUPPORTED")
        elements = paragraph.get("elements")
        if not isinstance(elements, list):
            raise ValueError("ACTIVE_WORK_WRITE_PARAGRAPH_ELEMENTS_REQUIRED")
        for element in elements:
            if not isinstance(element, Mapping):
                raise ValueError("ACTIVE_WORK_WRITE_TEXT_ELEMENT_INVALID")
            text_run = element.get("textRun")
            if not isinstance(text_run, Mapping):
                raise ValueError("ACTIVE_WORK_WRITE_NON_TEXT_ELEMENT_UNSUPPORTED")
            value = text_run.get("content")
            if not isinstance(value, str):
                raise ValueError("ACTIVE_WORK_WRITE_TEXT_CONTENT_INVALID")
            text.append(value)

    if end_index <= 2:
        raise ValueError("ACTIVE_WORK_WRITE_BODY_RANGE_INVALID")
    return "".join(text), end_index


def _document_snapshot(document: object) -> _DocumentSnapshot:
    if not isinstance(document, Mapping):
        raise ValueError("ACTIVE_WORK_WRITE_DOCUMENT_REQUIRED")
    if str(document.get("title", "")).strip() != _SIGNAL_TITLE:
        raise ValueError("ACTIVE_WORK_WRITE_TITLE_MISMATCH")
    revision_id = str(document.get("revisionId", "")).strip()
    if not revision_id:
        raise ValueError("ACTIVE_WORK_WRITE_REVISION_REQUIRED")

    tabs = document.get("tabs")
    if not isinstance(tabs, list) or len(tabs) != 1 or not isinstance(tabs[0], Mapping):
        raise ValueError("ACTIVE_WORK_WRITE_EXACT_SINGLE_TAB_REQUIRED")
    tab = tabs[0]
    child_tabs = tab.get("childTabs")
    if isinstance(child_tabs, list) and child_tabs:
        raise ValueError("ACTIVE_WORK_WRITE_CHILD_TABS_UNSUPPORTED")
    properties = tab.get("tabProperties")
    if not isinstance(properties, Mapping):
        raise ValueError("ACTIVE_WORK_WRITE_TAB_PROPERTIES_REQUIRED")
    tab_id = str(properties.get("tabId", "")).strip()
    if not tab_id:
        raise ValueError("ACTIVE_WORK_WRITE_TAB_ID_REQUIRED")
    document_tab = tab.get("documentTab")
    if not isinstance(document_tab, Mapping):
        raise ValueError("ACTIVE_WORK_WRITE_DOCUMENT_TAB_REQUIRED")
    text, body_end_index = _plain_text_body(document_tab.get("body"))
    return _DocumentSnapshot(
        revision_id=revision_id,
        tab_id=tab_id,
        body_end_index=body_end_index,
        signal=parse_active_work_signal(text),
    )


def _slot(signal: ActiveWorkSignal, slot_id: str) -> ActiveWorkSlot:
    matches = tuple(slot for slot in signal.slots if slot.slot_id == slot_id)
    if len(matches) != 1:
        raise ValueError("ACTIVE_WORK_WRITE_PLAN_SLOT_UNRESOLVED")
    return matches[0]


def _replace_slot(signal: ActiveWorkSignal, plan: ActiveWorkMutationPlan) -> ActiveWorkSignal:
    return ActiveWorkSignal(
        slots=tuple(plan.after if slot.slot_id == plan.slot_id else slot for slot in signal.slots)
    )


class GoogleDocsActiveWorkSignalWriter:
    """Revision-CAS provider adapter for one already-planned Signal mutation.

    The adapter fresh-reads the exact document, requires the complete planned
    `before` slot snapshot to still match, renders the whole bounded plain-text
    Signal, then performs one atomic Docs `batchUpdate` guarded by
    `writeControl.requiredRevisionId`. A second fresh read verifies the complete
    parsed Signal. This class is not wired to production by this EXP change.
    """

    def __init__(
        self,
        document_id: str,
        *,
        credentials: Any | None = None,
        service: Any | None = None,
    ) -> None:
        self._document_id = document_id.strip()
        if not self._document_id:
            raise ValueError("ACTIVE_WORK_WRITE_DOCUMENT_ID_REQUIRED")
        if service is None:
            if credentials is None:
                import google.auth

                credentials, _ = google.auth.default(scopes=[_DOCUMENTS_SCOPE])
            from googleapiclient.discovery import build

            service = build("docs", "v1", credentials=credentials, cache_discovery=False)
        self._docs = service

    def _get_document(self) -> object:
        return (
            self._docs.documents()
            .get(documentId=self._document_id, includeTabsContent=True)
            .execute()
        )

    def apply(self, plan: ActiveWorkMutationPlan) -> ActiveWorkWriteResult:
        # PREWRITE: provider fresh-read + exact plan snapshot precondition.
        before = _document_snapshot(self._get_document())
        current_slot = _slot(before.signal, plan.slot_id)
        if current_slot != plan.before:
            raise ValueError("ACTIVE_WORK_WRITE_STALE_PLAN_SNAPSHOT")
        if current_slot.generation != plan.expected_generation:
            raise ValueError("ACTIVE_WORK_WRITE_STALE_PLAN_GENERATION")
        if plan.expected_ref:
            if current_slot.status != ActiveWorkStatus.ACTIVE or current_slot.ref != plan.expected_ref:
                raise ValueError("ACTIVE_WORK_WRITE_STALE_PLAN_REF")

        expected_signal = _replace_slot(before.signal, plan)
        rendered = render_active_work_signal(expected_signal).rstrip("\n")
        body = {
            "requests": [
                {
                    "deleteContentRange": {
                        "range": {
                            "segmentId": "",
                            "startIndex": 1,
                            "endIndex": before.body_end_index - 1,
                            "tabId": before.tab_id,
                        }
                    }
                },
                {
                    "insertText": {
                        "location": {"index": 1, "tabId": before.tab_id},
                        "text": rendered,
                    }
                },
            ],
            "writeControl": {"requiredRevisionId": before.revision_id},
        }

        try:
            (
                self._docs.documents()
                .batchUpdate(documentId=self._document_id, body=body)
                .execute()
            )
        except Exception as exc:
            # Transport/provider errors after dispatch have unknown effect; never
            # auto-replay the same mutation solely because an exception occurred.
            return ActiveWorkWriteResult(
                ActiveWorkWriteEffect.UNKNOWN_EFFECT,
                f"ACTIVE_WORK_WRITE_UNKNOWN_EFFECT:{type(exc).__name__}",
                plan.slot_id,
                before.revision_id,
            )

        try:
            readback = _document_snapshot(self._get_document())
        except Exception as exc:
            return ActiveWorkWriteResult(
                ActiveWorkWriteEffect.UNSYNCED,
                f"ACTIVE_WORK_WRITE_READBACK_FAILED:{type(exc).__name__}",
                plan.slot_id,
                before.revision_id,
            )

        if readback.revision_id == before.revision_id:
            return ActiveWorkWriteResult(
                ActiveWorkWriteEffect.UNSYNCED,
                "ACTIVE_WORK_WRITE_REVISION_NOT_ADVANCED",
                plan.slot_id,
                before.revision_id,
                readback.revision_id,
            )
        if readback.signal != expected_signal:
            return ActiveWorkWriteResult(
                ActiveWorkWriteEffect.UNSYNCED,
                "ACTIVE_WORK_WRITE_READBACK_MISMATCH",
                plan.slot_id,
                before.revision_id,
                readback.revision_id,
            )

        return ActiveWorkWriteResult(
            ActiveWorkWriteEffect.VERIFIED,
            "ACTIVE_WORK_WRITE_VERIFIED",
            plan.slot_id,
            before.revision_id,
            readback.revision_id,
        )
