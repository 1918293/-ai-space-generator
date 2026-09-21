from src.google_docs_active_work_source import GoogleDocsActiveWorkSignalTextSource


class ExecuteRequest:
    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.result


class DocumentsResource:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            return ExecuteRequest(error=response)
        return ExecuteRequest(result=response)


class DocsService:
    def __init__(self, *responses):
        self.resource = DocumentsResource(responses)

    def documents(self):
        return self.resource


def paragraph(text):
    return {
        "paragraph": {
            "elements": [
                {"textRun": {"content": text}},
            ]
        }
    }


def tab(text, *, children=None):
    return {
        "documentTab": {
            "body": {
                "content": [paragraph(text)],
            }
        },
        "childTabs": children or [],
    }


def test_reads_all_tab_content_with_include_tabs_content_true():
    service = DocsService(
        {
            "tabs": [
                tab("HAO_ACTIVE_WORK_SIGNAL_V1\n", children=[tab("ROLE=NON_AUTHORITY_REBUILDABLE_RUNTIME_SIGNAL\n")]),
                tab("PURPOSE=CROSS_CHAT_ACTIVE_WORK_COORDINATION_ONLY\n"),
            ]
        }
    )
    source = GoogleDocsActiveWorkSignalTextSource("doc-1", service=service)

    text = source.read_text()

    assert text == (
        "HAO_ACTIVE_WORK_SIGNAL_V1\n"
        "ROLE=NON_AUTHORITY_REBUILDABLE_RUNTIME_SIGNAL\n"
        "PURPOSE=CROSS_CHAT_ACTIVE_WORK_COORDINATION_ONLY\n"
    )
    assert service.resource.calls == [
        {"documentId": "doc-1", "includeTabsContent": True}
    ]


def test_each_read_is_fresh_and_does_not_cache_document_text():
    service = DocsService(
        {"tabs": [tab("S1_STATUS=EMPTY\n")]},
        {"tabs": [tab("S1_STATUS=ACTIVE\n")]},
    )
    source = GoogleDocsActiveWorkSignalTextSource("doc-1", service=service)

    assert source.read_text() == "S1_STATUS=EMPTY\n"
    assert source.read_text() == "S1_STATUS=ACTIVE\n"
    assert len(service.resource.calls) == 2


def test_read_failure_returns_none_for_fail_closed_resolver():
    service = DocsService(RuntimeError("provider unavailable"))
    source = GoogleDocsActiveWorkSignalTextSource("doc-1", service=service)

    assert source.read_text() is None


def test_empty_document_returns_none():
    source = GoogleDocsActiveWorkSignalTextSource(
        "doc-1",
        service=DocsService({"tabs": []}),
    )

    assert source.read_text() is None


def test_legacy_body_is_supported_only_as_defensive_read_fallback():
    source = GoogleDocsActiveWorkSignalTextSource(
        "doc-1",
        service=DocsService(
            {
                "tabs": [],
                "body": {"content": [paragraph("HAO_ACTIVE_WORK_SIGNAL_V1\n")]},
            }
        ),
    )

    assert source.read_text() == "HAO_ACTIVE_WORK_SIGNAL_V1\n"


def test_blank_document_id_is_rejected():
    try:
        GoogleDocsActiveWorkSignalTextSource("   ", service=DocsService())
    except ValueError as exc:
        assert str(exc) == "ACTIVE_WORK_DOCUMENT_ID_REQUIRED"
    else:
        raise AssertionError("blank document id was accepted")
