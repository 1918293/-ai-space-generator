from pathlib import Path

ROOT = Path("ai-space-generator")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"PATCH_ANCHOR_COUNT:{path}:{count}")
    path.write_text(text.replace(old, new, 1))


execution_control = ROOT / "src" / "execution_control.py"
replace_once(
    execution_control,
    '    forbidden_action_tags: tuple[str, ...] = ()\n    phase: RunPhase = RunPhase.RESOLVED\n',
    '    forbidden_action_tags: tuple[str, ...] = ()\n    policy_fingerprint: str = ""\n    decision_id: str = ""\n    phase: RunPhase = RunPhase.RESOLVED\n',
)

control_gateway = ROOT / "src" / "control_gateway.py"
replace_once(
    control_gateway,
    "from dataclasses import dataclass\n",
    "from dataclasses import dataclass, replace\n",
)
replace_once(
    control_gateway,
    '''class TaskPolicyProvider(Protocol):\n    def resolve(self, state: ActiveOperationalState) -> TaskExecutionPolicy: ...\n''',
    '''def _task_policy_fingerprint(policy: TaskExecutionPolicy) -> str:\n    payload = {\n        "goal_valid": policy.goal_valid,\n        "acceptance_criteria": policy.acceptance_criteria,\n        "required_acceptance_gate_ids": policy.required_acceptance_gate_ids,\n        "hao_acceptance_required": policy.hao_acceptance_required,\n        "authority_refs": tuple(sorted(ref.strip() for ref in policy.authority_refs if ref.strip())),\n        "authority_stamps": tuple(\n            sorted(\n                (stamp.ref.strip(), stamp.version.strip())\n                for stamp in policy.authority_stamps\n                if stamp.ref.strip() and stamp.version.strip()\n            )\n        ),\n        "required_action_authority_refs": tuple(\n            sorted(ref.strip() for ref in policy.required_action_authority_refs if ref.strip())\n        ),\n        "required_action_tags": tuple(sorted(tag.strip().upper() for tag in policy.required_action_tags if tag.strip())),\n        "forbidden_action_tags": tuple(sorted(tag.strip().upper() for tag in policy.forbidden_action_tags if tag.strip())),\n    }\n    material = json.dumps(\n        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")\n    ).encode("utf-8")\n    return "sha256:" + hashlib.sha256(material).hexdigest()\n\n\ndef _mint_decision_id(\n    state: ActiveOperationalState,\n    request: ModelIngressRequest,\n    *,\n    policy_fingerprint: str,\n    action_id: str,\n    authority_snapshot_fingerprint: str,\n    resolution_code: str,\n) -> str:\n    payload = {\n        "run_id": request.run_id.strip(),\n        "sequence": request.sequence,\n        "mode": state.mode.value,\n        "task": state.task,\n        "operational_version": state.version,\n        "policy_fingerprint": policy_fingerprint,\n        "action_id": action_id.strip(),\n        "authority_snapshot_fingerprint": authority_snapshot_fingerprint.strip(),\n        "resolution_code": resolution_code.strip(),\n    }\n    material = json.dumps(\n        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")\n    ).encode("utf-8")\n    return "DECISION:" + hashlib.sha256(material).hexdigest()\n\n\nclass TaskPolicyProvider(Protocol):\n    def resolve(self, state: ActiveOperationalState) -> TaskExecutionPolicy: ...\n''',
)
replace_once(
    control_gateway,
    '''        policy = self._policy_provider.resolve(state)\n        record = execution_record_from_operational_state(\n            state,\n            run_id=request.run_id,\n            goal_valid=policy.goal_valid,\n            acceptance_criteria=policy.acceptance_criteria,\n''',
    '''        policy = self._policy_provider.resolve(state)\n        policy_fingerprint = _task_policy_fingerprint(policy)\n        record = execution_record_from_operational_state(\n            state,\n            run_id=request.run_id,\n            goal_valid=policy.goal_valid,\n            acceptance_criteria=policy.acceptance_criteria,\n            policy_fingerprint=policy_fingerprint,\n''',
)
replace_once(
    control_gateway,
    '''        if resolution.proposal is None:\n            return PreparedControlledAction(None, resolution)\n        return PreparedControlledAction(record, resolution)\n''',
    '''        if resolution.proposal is None:\n            return PreparedControlledAction(None, resolution)\n        decision_id = _mint_decision_id(\n            state,\n            request,\n            policy_fingerprint=policy_fingerprint,\n            action_id=resolution.proposal.action_id,\n            authority_snapshot_fingerprint=resolution.proposal.authority_snapshot_fingerprint,\n            resolution_code=resolution.decision.code,\n        )\n        return PreparedControlledAction(replace(record, decision_id=decision_id), resolution)\n''',
)

test_gateway = ROOT / "tests" / "test_control_gateway.py"
replace_once(
    test_gateway,
    "from dataclasses import fields\n",
    "from dataclasses import fields, replace\n",
)
append_marker = '''\n\ndef test_gateway_mints_runtime_owned_decision_provenance():\n'''
if append_marker not in test_gateway.read_text():
    with test_gateway.open("a") as handle:
        handle.write(
            '''\n\ndef test_gateway_mints_runtime_owned_decision_provenance():\n    gateway = ControlPlaneGateway(catalog(), PolicyProvider())\n    request = ModelIngressRequest(\n        "RUN-GW-PROVENANCE",\n        1,\n        ModelActionIntent(\n            "INTENT-PROVENANCE",\n            "image_edit",\n            "image.local_edit",\n            expected_state_delta="bounded edit",\n        ),\n    )\n    first = gateway.prepare(state(), request)\n    second = gateway.prepare(state(), request)\n    assert first.record is not None and second.record is not None\n    assert first.record.policy_fingerprint.startswith("sha256:")\n    assert first.record.decision_id.startswith("DECISION:")\n    assert first.record.policy_fingerprint == second.record.policy_fingerprint\n    assert first.record.decision_id == second.record.decision_id\n    ingress_fields = {field.name for field in fields(ModelIngressRequest)}\n    assert "decision_id" not in ingress_fields\n    assert "policy_fingerprint" not in ingress_fields\n\n\ndef test_authority_revision_change_changes_policy_and_decision_identity():\n    class RevisedAuthorityPolicyProvider(PolicyProvider):\n        def resolve(self, state):\n            policy = super().resolve(state)\n            return replace(\n                policy,\n                authority_stamps=(\n                    AuthorityStamp("SOURCE-ORIGINAL", "sha256:def"),\n                ),\n            )\n\n    request = ModelIngressRequest(\n        "RUN-GW-PROVENANCE-REVISION",\n        1,\n        ModelActionIntent(\n            "INTENT-PROVENANCE",\n            "image_edit",\n            "image.local_edit",\n            expected_state_delta="bounded edit",\n        ),\n    )\n    original = ControlPlaneGateway(catalog(), PolicyProvider()).prepare(state(), request)\n    revised = ControlPlaneGateway(catalog(), RevisedAuthorityPolicyProvider()).prepare(state(), request)\n    assert original.record is not None and revised.record is not None\n    assert original.resolution.proposal is not None and revised.resolution.proposal is not None\n    assert original.resolution.proposal.action_id == revised.resolution.proposal.action_id\n    assert original.record.policy_fingerprint != revised.record.policy_fingerprint\n    assert original.record.decision_id != revised.record.decision_id\n    assert (\n        original.resolution.proposal.authority_snapshot_fingerprint\n        != revised.resolution.proposal.authority_snapshot_fingerprint\n    )\n'''
        )
