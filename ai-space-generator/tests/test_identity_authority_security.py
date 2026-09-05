import json
import pytest

from src.identity_authority_security import (
    AuthorityActor,
    AuthoritySnapshot,
    ConfiguredTaskPolicyProvider,
    DeploymentAuthorityContracts,
    HaoSemanticAuthorityAdapter,
    HaoSemanticSecurityPolicy,
    Permission,
    SQLiteIdentitySecurityStore,
    VerifiedIdentity,
)


def identity(*, subject="hao-sub", scopes=None, exp=2000, token_id="jti-1", raw_token="secret-bearer"):
    return VerifiedIdentity(subject, frozenset(scopes or {"hao:access", "hao:read", "hao:execute", "hao:approve"}), exp, token_id, raw_token)


def test_wrong_subject_scope_expiry_and_revocation_fail_closed(tmp_path):
    store = SQLiteIdentitySecurityStore(str(tmp_path / "identity.db"))
    policy = HaoSemanticSecurityPolicy("hao-sub", store)
    with pytest.raises(PermissionError, match="HAO_IDENTITY_REQUIRED"):
        policy.require_identity(identity(subject="attacker"), Permission.READ, now_epoch=1000)
    with pytest.raises(PermissionError, match="MISSING_SCOPE:hao:execute"):
        policy.require_identity(identity(scopes={"hao:access", "hao:read"}), Permission.EXECUTE, now_epoch=1000)
    with pytest.raises(PermissionError, match="TOKEN_EXPIRED"):
        policy.require_identity(identity(exp=1000), Permission.READ, now_epoch=1000)
    policy.revoke("jti-1")
    with pytest.raises(PermissionError, match="TOKEN_REVOKED_OR_UNIDENTIFIED"):
        policy.require_identity(identity(), Permission.READ, now_epoch=1000)


def test_cross_session_identity_continuity_and_run_owner_theft_blocked(tmp_path):
    store = SQLiteIdentitySecurityStore(str(tmp_path / "identity.db"))
    policy = HaoSemanticSecurityPolicy("hao-sub", store)
    a = identity(token_id="jti-a")
    b = identity(token_id="jti-b")
    store.bind_session("session-a", a)
    store.bind_session("session-b", b)
    store.bind_run("run-1", a.subject)
    policy.require_identity(b, Permission.READ, now_epoch=1000, session_id="session-b", run_id="run-1")
    with pytest.raises(PermissionError, match="SESSION_OWNER_MISMATCH"):
        store.require_session("session-a", "attacker")
    with pytest.raises(PermissionError, match="RUN_OWNER_MISMATCH"):
        store.require_run_owner("run-1", "attacker")
    with pytest.raises(PermissionError, match="UNKNOWN_RUN_IDENTITY"):
        store.require_run_owner("stolen-run-id", "hao-sub")


def test_token_credential_isolation_persists_only_fingerprint(tmp_path):
    db = tmp_path / "identity.db"
    store = SQLiteIdentitySecurityStore(str(db))
    store.bind_session("s1", identity(raw_token="BEARER-DO-NOT-STORE"))
    owner, fingerprint = store.stored_session_material("s1")
    assert owner == "hao-sub"
    assert fingerprint and fingerprint != "jti-1"
    raw = db.read_bytes()
    assert b"BEARER-DO-NOT-STORE" not in raw


def test_approval_and_attestation_replay_are_one_time_and_owner_scope_authority_bound(tmp_path):
    store = SQLiteIdentitySecurityStore(str(tmp_path / "identity.db"))
    for kind, artifact in (("approval", "approval-1"), ("attestation", "attest-1")):
        store.register_one_time_artifact(
            replay_kind=kind,
            artifact_id=artifact,
            owner_subject="hao-sub",
            scope="run-1:exact-action",
            authority_fingerprint="sha256:abc",
        )
        with pytest.raises(PermissionError, match="OWNER_MISMATCH"):
            store.consume_one_time_artifact(replay_kind=kind, artifact_id=artifact, owner_subject="attacker", scope="run-1:exact-action", authority_fingerprint="sha256:abc")
        with pytest.raises(PermissionError, match="SCOPE_MISMATCH"):
            store.consume_one_time_artifact(replay_kind=kind, artifact_id=artifact, owner_subject="hao-sub", scope="other", authority_fingerprint="sha256:abc")
        with pytest.raises(PermissionError, match="STALE_AUTHORITY"):
            store.consume_one_time_artifact(replay_kind=kind, artifact_id=artifact, owner_subject="hao-sub", scope="run-1:exact-action", authority_fingerprint="sha256:stale")
        store.consume_one_time_artifact(replay_kind=kind, artifact_id=artifact, owner_subject="hao-sub", scope="run-1:exact-action", authority_fingerprint="sha256:abc")
        with pytest.raises(PermissionError, match="ALREADY_CONSUMED"):
            store.consume_one_time_artifact(replay_kind=kind, artifact_id=artifact, owner_subject="hao-sub", scope="run-1:exact-action", authority_fingerprint="sha256:abc")


def test_semantic_authority_snapshot_excludes_projection_and_rejects_stale():
    base = AuthoritySnapshot("EXP", "Task A", 7, ("drive:current",), ("v12",), projection_ref="xmemo:projection-99")
    same_authority_different_projection = AuthoritySnapshot("EXP", "Task A", 7, ("drive:current",), ("v12",), projection_ref="chat:header")
    assert base.fingerprint() == same_authority_different_projection.fingerprint()
    base.require_fresh(same_authority_different_projection)
    stale = AuthoritySnapshot("EXP", "Task A", 7, ("drive:current",), ("v13",), projection_ref="same")
    with pytest.raises(PermissionError, match="STALE_AUTHORITY_SNAPSHOT"):
        base.require_fresh(stale)

def test_direct_semantic_authority_adapter_readback_rejects_projection_and_stale_changes():
    class Reader:
        def __init__(self):
            self.version = "v12"

        def read_snapshot(self, task):
            return AuthoritySnapshot("EXP", task, 7, ("drive:current",), (self.version,), projection_ref="chat:projection")

    reader = Reader()
    adapter = HaoSemanticAuthorityAdapter(reader)
    admitted = adapter.snapshot("Task A")
    assert admitted.projection_ref == "chat:projection"
    adapter.readback(admitted)
    reader.version = "v13"
    with pytest.raises(PermissionError, match="STALE_AUTHORITY_SNAPSHOT"):
        adapter.readback(admitted)


def test_mode_task_mutation_remains_human_authoritative():
    HaoSemanticSecurityPolicy.require_human_authority(AuthorityActor.HUMAN)
    for actor in (AuthorityActor.MODEL, AuthorityActor.SYSTEM, AuthorityActor.PROJECTION):
        with pytest.raises(PermissionError, match="HUMAN_AUTHORITY_REQUIRED"):
            HaoSemanticSecurityPolicy.require_human_authority(actor)


def deployment_values():
    return {
        "HAO_SHEETS_TARGETS_JSON": json.dumps({"sheets-main": {"spreadsheet_id": "sheet-1", "range_a1": "Current!A1:B2"}}),
        "HAO_TASK_POLICIES_JSON": json.dumps({"Task A": {"task": "Task A", "authority_sources": ["drive:current"], "acceptance_criteria": ["readback matches"], "required_gates": ["verification"], "hao_acceptance_required": True}}),
        "HAO_PARENT_TASK_PLANS_JSON": json.dumps({"plan-a": {"task": "Task A", "children": [{"slot_id": "persist", "requested_capability": "semantic_persist", "binding_id": "sheets-main", "authorization_target": "Current!A1:B2"}]}}),
    }


def test_deployment_owned_contracts_are_required_and_model_cannot_supply_missing_values():
    values = deployment_values()
    contracts = DeploymentAuthorityContracts.from_mapping(values)
    assert contracts.task_policies["Task A"].hao_acceptance_required is True
    for missing in values:
        broken = dict(values)
        del broken[missing]
        with pytest.raises(ValueError, match="MISSING_CONFIG"):
            DeploymentAuthorityContracts.from_mapping(broken)
    malformed = dict(values)
    malformed["HAO_TASK_POLICIES_JSON"] = "{not-json"
    with pytest.raises(ValueError, match="MALFORMED_CONFIG"):
        DeploymentAuthorityContracts.from_mapping(malformed)


def test_task_policy_provider_fails_closed_for_unknown_task():
    values = deployment_values()
    provider = ConfiguredTaskPolicyProvider(values["HAO_TASK_POLICIES_JSON"])
    assert provider.get("Task A").authority_sources == ("drive:current",)
    with pytest.raises(PermissionError, match="TASK_POLICY_NOT_CONFIGURED"):
        provider.get("Model Invented Task")
