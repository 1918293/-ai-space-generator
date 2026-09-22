from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading
import time
from typing import Mapping
from urllib import error as urlerror
from urllib import request as urlrequest

from .action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from .context_bound_reasoning import AdmittedContextItem, ContextBoundPreModelGateway, ContextBoundReasoningIngress
from .context_reasoning_consumer import ContextBoundReasoningConsumer
from .control_gateway import (
    ControlPlaneGateway,
    PreModelContextGateway,
    PreModelContextResolution,
    TaskExecutionPolicy,
)
from .execution_control import ActionArchetype, ActionExternality, Mode
from .mcp_control_bridge import HaoMCPIdentityPolicy, MCPPrincipal, SCOPE_EXECUTE
from .mcp_reasoning_ingress import AuthenticatedMCPReasoningIngress
from .operational_state import ActiveOperationalState, CommandActor
from .groq_free_benchmark import build_groq_free_benchmark_client, run_groq_free_benchmark
from .groq_free_provider import GROQ_GPT_OSS_20B, GroqFreeOnlyStop, groq_api_key_secret_file_status, load_groq_api_key


TASK = "Hao System｜Runtime v2 deployed field consumer"
CURRENT_REF = "CURRENT:ACTION_ADMISSION_BINDING"
EXISTING_REF = "PR17:CURRENT"
REGRESSION_REF = "REG:R051-NON-BYPASSABLE"
EXPECTED_CODE = "MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING"
ARTIFACT_ROLE = "EXP_DEPLOYED_FIELD_CONSUMER"


class StateSource:
    def __init__(self) -> None:
        self._state = ActiveOperationalState(Mode.EXP, TASK, 1, "EVENT-FIELD-CONSUMER")

    def get(self) -> ActiveOperationalState:
        return self._state


class StructuralResolver:
    def resolve(self, state, request, checkpoint_cue):
        del checkpoint_cue
        if state.task != TASK or request.actor != CommandActor.USER:
            return None
        return PreModelContextResolution(
            checkpoint_id=f"R{state.version}",
            task=state.task,
            operational_version=state.version,
            authority_refs=(CURRENT_REF,),
            existing_work_refs=(EXISTING_REF,),
            regression_refs=(REGRESSION_REF,),
            existing_work_lookup_complete=True,
            prior_attempt_lookup_complete=True,
            regression_lookup_complete=True,
            reuse_disposition="REUSE",
        )


class SnapshotSemanticResolver:
    """Deployment-owned bounded snapshot used only for field-consumer proof.

    This is deliberately not represented as the live canonical provider reader.
    Formal mutation remains outside this service and must re-enter the existing
    Single Write Gateway with fresh Authority readback.
    """

    def __init__(self, *, stale: bool = False) -> None:
        self.stale = stale

    def resolve(self, state, request, receipt):
        del state, request
        if receipt.checkpoint_id != "R1":
            return ()
        source_version = os.environ.get(
            "HAO_CANONICAL_SNAPSHOT_VERSION",
            "06_Config!A540:F540@2026-09-16T18:03:23+08:00",
        )
        return (
            AdmittedContextItem(
                ref=CURRENT_REF,
                kind="CURRENT_CONTROL",
                summary=(
                    "No-computer Task Router is an admission selector, not Authority. "
                    "Formal mutation must re-enter ACTION_ADMISSION_BINDING and the "
                    "existing Single Write Gateway; Render/public Actions cannot write Authority."
                ),
                source_version=source_version,
                project_scope="HAO_SYSTEM",
                applicability="STALE" if self.stale else "APPLICABLE",
                disposition="APPLY",
            ),
            AdmittedContextItem(
                ref=EXISTING_REF,
                kind="EXISTING_WORK",
                summary=(
                    "Reuse PR17 Runtime v2 ContextBoundReasoningIngress and ControlPlaneGateway; "
                    "do not create a parallel policy engine or execution authority."
                ),
                source_version=os.environ.get("RENDER_GIT_COMMIT", "unknown"),
                project_scope="HAO_SYSTEM",
                disposition="REUSE",
            ),
            AdmittedContextItem(
                ref=REGRESSION_REF,
                kind="REGRESSION",
                summary=(
                    "Caller-supplied Mode, TASK, Authority, model intent and binding metadata "
                    "must not bypass Runtime-owned admission."
                ),
                source_version="R051-current",
                project_scope="HAO_SYSTEM",
                disposition="APPLY",
            ),
        )


class PolicyProvider:
    def resolve(self, state):
        if state.task != TASK:
            return None
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=(
                "raw user text enters through Runtime-owned context admission",
                "formal mutation is only prepared, never executed by this field consumer",
            ),
        )


class DeterministicIntentModel:
    """Bounded deterministic model double for deployed enforcement proof.

    The proof target is consumer/admission non-bypassability, not model quality or
    live OpenAI API access. No provider mutation is possible from this model.
    """

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, model_input):
        self.calls += 1
        return ModelActionIntent(
            "INTENT-FIELD-CONSUMER",
            "formal_persistence",
            "formal.persist",
            expected_state_delta="projection-only; no provider mutation",
            model_reported_used_refs=(CURRENT_REF, EXISTING_REF),
        )


def _catalog() -> ActionCatalog:
    return ActionCatalog(
        (
            ActionBinding(
                "formal.persist",
                "formal_persistence",
                "google-drive",
                "update_cells",
                ActionArchetype.MUTATE,
                ActionExternality.PRIVATE_REVERSIBLE,
            ),
        )
    )


@dataclass
class FieldRuntime:
    token: str
    expected_subject: str
    ingress: AuthenticatedMCPReasoningIngress
    model: DeterministicIntentModel

    @classmethod
    def build(
        cls,
        *,
        token: str,
        expected_subject: str = "hao-field-exp",
        stale: bool = False,
    ) -> "FieldRuntime":
        token = token.strip()
        expected_subject = expected_subject.strip()
        if not token:
            raise ValueError("FIELD_CONSUMER_TOKEN_REQUIRED")
        if not expected_subject:
            raise ValueError("FIELD_CONSUMER_SUBJECT_REQUIRED")

        model = DeterministicIntentModel()
        pre_model = ContextBoundPreModelGateway(
            PreModelContextGateway(StructuralResolver()),
            SnapshotSemanticResolver(stale=stale),
        )
        reasoning = ContextBoundReasoningIngress(
            pre_model=pre_model,
            model=model,
            control_plane=ControlPlaneGateway(_catalog(), PolicyProvider()),
        )
        consumer = ContextBoundReasoningConsumer(
            state_source=StateSource(),
            ingress=reasoning,
        )
        identity = HaoMCPIdentityPolicy(expected_subject)
        ingress = AuthenticatedMCPReasoningIngress(
            consumer=consumer,
            identity_policy=identity,
        )
        return cls(token, expected_subject, ingress, model)

    def handle(self, headers: Mapping[str, str], payload: object) -> tuple[int, dict[str, object]]:
        supplied = str(headers.get("authorization", ""))
        expected = "Bearer " + self.token
        if not secrets.compare_digest(supplied, expected):
            return 401, {"ok": False, "code": "AUTHENTICATION_REQUIRED"}

        if not isinstance(payload, dict) or set(payload) != {"user_text"}:
            return 400, {"ok": False, "code": "REQUEST_SCHEMA_EXACT_USER_TEXT_ONLY"}
        user_text = payload.get("user_text")
        if not isinstance(user_text, str) or not user_text.strip():
            return 400, {"ok": False, "code": "USER_TEXT_REQUIRED"}
        if len(user_text) > 4000:
            return 400, {"ok": False, "code": "USER_TEXT_TOO_LARGE"}

        principal = MCPPrincipal(self.expected_subject, frozenset({SCOPE_EXECUTE}))
        try:
            view = self.ingress.prepare_user_turn(principal, user_text=user_text)
        except PermissionError as exc:
            return 403, {"ok": False, "code": str(exc)}
        except ValueError as exc:
            return 400, {"ok": False, "code": str(exc)}

        return 200, {
            "ok": True,
            "code": view.code,
            "reasoning_run_id": view.reasoning_run_id,
            "action_selected": view.action_selected,
            "decision_id": view.decision_id,
            "action_id": view.action_id,
            "authorization_scope": view.authorization_scope,
            "provider_mutation_performed": False,
        }


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: Mapping[str, object]) -> None:
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def build_handler(runtime: FieldRuntime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "hao-runtime-v2-field-exp/1"

        def log_message(self, format, *args):
            return

        def do_GET(self):
            if self.path != "/healthz":
                return _json_response(self, 404, {"ok": False, "code": "NOT_FOUND"})
            return _json_response(
                self,
                200,
                {
                    "ok": True,
                    "artifactRole": ARTIFACT_ROLE,
                    "releaseCommit": os.environ.get("RENDER_GIT_COMMIT", "unknown"),
                    "formalAuthority": "Google Drive",
                    "authorityInput": "STATIC_CANONICAL_SNAPSHOT_EXP",
                    "consumer": "ContextBoundReasoningIngress->ControlPlaneGateway",
                    "providerMutation": False,
                    "gcpProduction": False,
                },
            )

        def do_POST(self):
            if self.path != "/v1/reason":
                return _json_response(self, 404, {"ok": False, "code": "NOT_FOUND"})
            try:
                size = int(self.headers.get("content-length", "0"))
            except ValueError:
                return _json_response(self, 400, {"ok": False, "code": "INVALID_CONTENT_LENGTH"})
            if size < 1 or size > 65536:
                return _json_response(self, 400, {"ok": False, "code": "INVALID_BODY_SIZE"})
            try:
                payload = json.loads(self.rfile.read(size).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return _json_response(self, 400, {"ok": False, "code": "INVALID_JSON"})
            status, body = runtime.handle(
                {"authorization": self.headers.get("authorization", "")},
                payload,
            )
            return _json_response(self, status, body)

    return Handler


def _post(base: str, token: str | None, payload: object) -> tuple[int, dict[str, object]]:
    headers = {"content-type": "application/json"}
    if token is not None:
        headers["authorization"] = "Bearer " + token
    req = urlrequest.Request(
        base + "/v1/reason",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def run_startup_selftest(port: int, runtime: FieldRuntime) -> None:
    time.sleep(1.5)
    base = f"http://127.0.0.1:{port}"
    results: list[bool] = []

    def record(name: str, passed: bool, **detail: object) -> None:
        results.append(bool(passed))
        print(json.dumps({"event": "hao_field_consumer_selftest", "name": name, "pass": bool(passed), **detail}, sort_keys=True), flush=True)

    status, body = _post(base, runtime.token, {"user_text": "Auto > bounded deployed consumer field proof"})
    record(
        "authorized_raw_user_turn",
        status == 200 and body.get("code") == EXPECTED_CODE and body.get("action_selected") is True and body.get("provider_mutation_performed") is False,
        status=status,
        code=body.get("code", ""),
    )

    status, body = _post(base, None, {"user_text": "Auto"})
    record("unauthenticated_blocked", status == 401 and body.get("code") == "AUTHENTICATION_REQUIRED", status=status)

    status, body = _post(base, runtime.token, {"user_text": "Auto", "mode": "SYS"})
    record("caller_mode_spoof_blocked", status == 400 and body.get("code") == "REQUEST_SCHEMA_EXACT_USER_TEXT_ONLY", status=status)

    status, body = _post(base, runtime.token, {"user_text": "Auto", "binding_id": "formal.persist.legacy"})
    record("caller_binding_spoof_blocked", status == 400 and body.get("code") == "REQUEST_SCHEMA_EXACT_USER_TEXT_ONLY", status=status)

    try:
        runtime.ingress.prepare_user_turn(
            MCPPrincipal("wrong-subject", frozenset({SCOPE_EXECUTE})),
            user_text="Auto",
        )
    except PermissionError as exc:
        record("wrong_identity_blocked", str(exc) == "HAO_IDENTITY_REQUIRED", code=str(exc))
    else:
        record("wrong_identity_blocked", False)

    stale = FieldRuntime.build(token="internal-stale-token", expected_subject=runtime.expected_subject, stale=True)
    view = stale.ingress.prepare_user_turn(
        MCPPrincipal(runtime.expected_subject, frozenset({SCOPE_EXECUTE})),
        user_text="Auto > stale semantic proof",
    )
    record(
        "stale_semantics_fail_closed_before_model",
        view.code == "PRE_MODEL_SEMANTIC_STALE" and view.action_selected is False and stale.model.calls == 0,
        code=view.code,
        model_calls=stale.model.calls,
    )

    print(
        json.dumps(
            {
                "event": "hao_field_consumer_selftest_summary",
                "passed": sum(results),
                "total": len(results),
                "result": "PASS" if results and all(results) else "FAIL",
                "provider_mutation": False,
                "gcp_production": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )



def run_optional_groq_free_benchmark() -> None:
    if os.environ.get("GROQ_BENCHMARK_ON_STARTUP", "").strip().lower() not in {"1", "true", "yes"}:
        return

    run_id = os.environ.get("GROQ_BENCHMARK_RUN_ID", "").strip()
    if not run_id:
        print(
            json.dumps(
                {
                    "event": "hao_groq_free_benchmark",
                    "result": "BLOCK",
                    "code": "GROQ_BENCHMARK_RUN_ID_REQUIRED",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return

    key, key_source = load_groq_api_key()
    if not key:
        print(
            json.dumps(
                {
                    "event": "hao_groq_free_benchmark",
                    "runId": run_id,
                    "result": "BLOCK",
                    "code": "GROQ_API_KEY_REQUIRED",
                    "groqEnvKeys": sorted(name for name in os.environ if name.startswith("GROQ")),
                    "groqApiKeySource": key_source,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return

    try:
        client = build_groq_free_benchmark_client(key)
        results = run_groq_free_benchmark(client)
    except GroqFreeOnlyStop as exc:
        print(
            json.dumps(
                {
                    "event": "hao_groq_free_benchmark",
                    "runId": run_id,
                    "result": "STOP",
                    "code": str(exc),
                    "freeOnly": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "hao_groq_free_benchmark",
                    "runId": run_id,
                    "result": "FAIL",
                    "code": type(exc).__name__,
                    "freeOnly": True,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return

    print(
        json.dumps(
            {
                "event": "hao_groq_free_benchmark",
                "runId": run_id,
                "result": "PASS" if all(item.get("quality_pass") is True for item in results) else "QUALITY_FAIL",
                "freeOnly": True,
                "calls": len(results),
                "results": results,
            },
            sort_keys=True,
        ),
        flush=True,
    )

def main() -> None:
    token = os.environ.get("HAO_FIELD_TOKEN", "").strip()
    subject = os.environ.get("HAO_FIELD_EXPECTED_SUBJECT", "hao-field-exp").strip()
    runtime = FieldRuntime.build(token=token, expected_subject=subject)
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), build_handler(runtime))
    print(
        json.dumps(
            {
                "event": "hao_field_consumer_startup",
                "port": port,
                "artifactRole": ARTIFACT_ROLE,
                "releaseCommit": os.environ.get("RENDER_GIT_COMMIT", "unknown"),
                "formalAuthority": "Google Drive",
                "authorityInput": "STATIC_CANONICAL_SNAPSHOT_EXP",
                "providerMutation": False,
                "gcpProduction": False,
                "groqCandidateModel": GROQ_GPT_OSS_20B,
                "groqApiKeyConfigured": bool(load_groq_api_key()[0]),
                "groqApiKeySource": load_groq_api_key()[1],
                "groqSecretFileStatus": groq_api_key_secret_file_status(),
                "groqEnvKeys": sorted(name for name in os.environ if name.startswith("GROQ")),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    threading.Thread(target=run_startup_selftest, args=(port, runtime), daemon=True).start()
    threading.Thread(target=run_optional_groq_free_benchmark, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
