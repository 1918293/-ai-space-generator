from __future__ import annotations

from typing import Any, Iterable, Mapping


_DOCUMENTS_READONLY_SCOPE = "https://www.googleapis.com/auth/documents.readonly"


def _body_text(body: object) -> str:
    if not isinstance(body, Mapping):
        return ""
    content = body.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for structural in content:
        if not isinstance(structural, Mapping):
            continue
        paragraph = structural.get("paragraph")
        if not isinstance(paragraph, Mapping):
            continue
        elements = paragraph.get("elements")
        if not isinstance(elements, list):
            continue
        for element in elements:
            if not isinstance(element, Mapping):
                continue
            text_run = element.get("textRun")
            if not isinstance(text_run, Mapping):
                continue
            value = text_run.get("content")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts)


def _tabs_text(tabs: object) -> str:
    if not isinstance(tabs, list):
        return ""
    parts: list[str] = []
    for tab in tabs:
        if not isinstance(tab, Mapping):
            continue
        document_tab = tab.get("documentTab")
        if isinstance(document_tab, Mapping):
            parts.append(_body_text(document_tab.get("body")))
        child_tabs = tab.get("childTabs")
        if isinstance(child_tabs, list):
            parts.append(_tabs_text(child_tabs))
    return "".join(parts)


class GoogleDocsActiveWorkSignalTextSource:
    """Fresh read-only adapter for the existing Google Docs Active Work Signal.

    Every call performs `documents.get(includeTabsContent=True)`. The adapter
    extracts text only; parsing, lifecycle semantics and fail-closed admission
    remain owned by the existing Active Work reader/resolver. It performs no
    mutation, lease acquisition, caching, identity minting or policy decision.
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
            raise ValueError("ACTIVE_WORK_DOCUMENT_ID_REQUIRED")

        if service is None:
            if credentials is None:
                import google.auth

                credentials, _ = google.auth.default(
                    scopes=[_DOCUMENTS_READONLY_SCOPE]
                )
            from googleapiclient.discovery import build

            service = build(
                "docs",
                "v1",
                credentials=credentials,
                cache_discovery=False,
            )
        self._docs = service

    def read_text(self) -> str | None:
        try:
            document = (
                self._docs.documents()
                .get(
                    documentId=self._document_id,
                    includeTabsContent=True,
                )
                .execute()
            )
        except Exception:
            return None
        if not isinstance(document, Mapping):
            return None

        text = _tabs_text(document.get("tabs"))
        if not text:
            # Defensive compatibility fallback for clients/documents that still
            # populate the legacy first-tab body representation.
            text = _body_text(document.get("body"))
        return text if text.strip() else None
