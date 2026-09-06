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


telemetry = ROOT / "src" / "runtime_observability.py"
replace_once(
    telemetry,
    '''    def record_run_event(\n        self,\n        event: str,\n        *,\n        phase: str,\n        run_id: str = "",\n        provider: str = "",\n        failure_stage: str = "",\n        failure_code: str = "",\n    ) -> None:\n''',
    '''    def record_run_event(\n        self,\n        event: str,\n        *,\n        phase: str,\n        run_id: str = "",\n        provider: str = "",\n        failure_stage: str = "",\n        failure_code: str = "",\n        decision_id: str = "",\n        policy_fingerprint: str = "",\n        admission_result: str = "",\n    ) -> None:\n''',
)
replace_once(
    telemetry,
    '''        if provider:\n            metric_attributes["hao.provider"] = provider\n        if failure_stage:\n            metric_attributes["hao.failure.stage"] = failure_stage\n''',
    '''        if provider:\n            metric_attributes["hao.provider"] = provider\n        if admission_result:\n            metric_attributes["hao.admission.result"] = admission_result.strip().upper()\n        if failure_stage:\n            metric_attributes["hao.failure.stage"] = failure_stage\n''',
)
replace_once(
    telemetry,
    '''        if run_id:\n            # IDs are high-cardinality and therefore trace-only.\n            trace_attributes["hao.run.id"] = run_id\n        if failure_code:\n            trace_attributes["hao.failure.code"] = failure_code\n''',
    '''        if run_id:\n            # IDs and fingerprints are high-cardinality and therefore trace-only.\n            trace_attributes["hao.run.id"] = run_id\n        if decision_id:\n            trace_attributes["hao.decision.id"] = decision_id\n        if policy_fingerprint:\n            trace_attributes["hao.policy.fingerprint"] = policy_fingerprint\n        if failure_code:\n            trace_attributes["hao.failure.code"] = failure_code\n''',
)

bridge = ROOT / "src" / "mcp_control_bridge.py"
replace_once(
    bridge,
    '''class MCPSubmissionView:\n    workflow_id: str\n    code: str\n    mode: str\n    task: str\n    phase: str\n    authorization_scope: str = ""\n''',
    '''class MCPSubmissionView:\n    workflow_id: str\n    code: str\n    mode: str\n    task: str\n    phase: str\n    authorization_scope: str = ""\n    decision_id: str = ""\n    policy_fingerprint: str = ""\n''',
)
replace_once(
    bridge,
    '''class MCPFinalizeView:\n    workflow_id: str\n    authoritative: bool\n    code: str\n    phase: str\n''',
    '''class MCPFinalizeView:\n    workflow_id: str\n    authoritative: bool\n    code: str\n    phase: str\n    decision_id: str = ""\n    policy_fingerprint: str = ""\n''',
)
replace_once(
    bridge,
    '''        return MCPSubmissionView(\n            workflow_id=submission.pending.handle.workflow_id,\n            code=submission.code,\n            mode=state.mode.value,\n            task=state.task,\n            phase=phase,\n            authorization_scope=authorization_scope,\n        )\n''',
    '''        return MCPSubmissionView(\n            workflow_id=submission.pending.handle.workflow_id,\n            code=submission.code,\n            mode=state.mode.value,\n            task=state.task,\n            phase=phase,\n            authorization_scope=authorization_scope,\n            decision_id=submission.record.decision_id if submission.record is not None else "",\n            policy_fingerprint=(\n                submission.record.policy_fingerprint if submission.record is not None else ""\n            ),\n        )\n''',
)
replace_once(
    bridge,
    '''        return MCPFinalizeView(workflow_id, result.authoritative, result.code, phase)\n''',
    '''        return MCPFinalizeView(\n            workflow_id,\n            result.authoritative,\n            result.code,\n            phase,\n            decision_id=result.record.decision_id if result.record is not None else "",\n            policy_fingerprint=(\n                result.record.policy_fingerprint if result.record is not None else ""\n            ),\n        )\n''',
)

observable = ROOT / "src" / "runtime_observability_bridge.py"
replace_once(
    observable,
    '''        self._telemetry.record_run_event(\n            "submitted" if result.workflow_id else "submit_rejected",\n            run_id=result.workflow_id,\n            phase=result.phase,\n            failure_stage="" if result.workflow_id else "ADMISSION",\n            failure_code="" if result.workflow_id else result.code,\n        )\n''',
    '''        self._telemetry.record_run_event(\n            "submitted" if result.workflow_id else "submit_rejected",\n            run_id=result.workflow_id,\n            phase=result.phase,\n            failure_stage="" if result.workflow_id else "ADMISSION",\n            failure_code="" if result.workflow_id else result.code,\n            decision_id=getattr(result, "decision_id", ""),\n            policy_fingerprint=getattr(result, "policy_fingerprint", ""),\n            admission_result="ADMITTED" if result.workflow_id else "REJECTED",\n        )\n''',
)
replace_once(
    observable,
    '''        self._telemetry.record_run_event(\n            "finalized",\n            run_id=workflow_id,\n            phase=result.phase,\n            failure_stage="" if result.authoritative else "COMPLETION",\n            failure_code="" if result.authoritative else result.code,\n        )\n''',
    '''        self._telemetry.record_run_event(\n            "finalized",\n            run_id=workflow_id,\n            phase=result.phase,\n            failure_stage="" if result.authoritative else "COMPLETION",\n            failure_code="" if result.authoritative else result.code,\n            decision_id=getattr(result, "decision_id", ""),\n            policy_fingerprint=getattr(result, "policy_fingerprint", ""),\n        )\n''',
)

obs_test = ROOT / "tests" / "test_runtime_observability.py"
replace_once(
    obs_test,
    '''        failure_stage="VERIFICATION",\n        failure_code="READBACK_MISMATCH",\n    )\n''',
    '''        failure_stage="VERIFICATION",\n        failure_code="READBACK_MISMATCH",\n        decision_id="DECISION:high-cardinality",\n        policy_fingerprint="sha256:policy-high-cardinality",\n        admission_result="ADMITTED",\n    )\n''',
)
replace_once(
    obs_test,
    '''    assert "hao.run.id" not in metric_attributes\n    assert "hao.run.id" not in failure_attributes\n    assert span_attributes["hao.run.id"] == "RUN-SECRETLY-HIGH-CARDINALITY"\n''',
    '''    assert "hao.run.id" not in metric_attributes\n    assert "hao.run.id" not in failure_attributes\n    assert "hao.decision.id" not in metric_attributes\n    assert "hao.decision.id" not in failure_attributes\n    assert "hao.policy.fingerprint" not in metric_attributes\n    assert "hao.policy.fingerprint" not in failure_attributes\n    assert metric_attributes["hao.admission.result"] == "ADMITTED"\n    assert span_attributes["hao.run.id"] == "RUN-SECRETLY-HIGH-CARDINALITY"\n    assert span_attributes["hao.decision.id"] == "DECISION:high-cardinality"\n    assert span_attributes["hao.policy.fingerprint"] == "sha256:policy-high-cardinality"\n''',
)
replace_once(
    obs_test,
    '''        return SimpleNamespace(\n            workflow_id="RUN-1", code="CONTROLLED_RUN_STARTED", phase="ADMITTED"\n        )\n''',
    '''        return SimpleNamespace(\n            workflow_id="RUN-1",\n            code="CONTROLLED_RUN_STARTED",\n            phase="ADMITTED",\n            decision_id="DECISION:RUN-1",\n            policy_fingerprint="sha256:POLICY-1",\n        )\n''',
)
replace_once(
    obs_test,
    '''        return SimpleNamespace(\n            workflow_id=workflow_id,\n            authoritative=True,\n            code="AUTHORITATIVE_COMPLETION_COMMITTED",\n            phase="CLOSED",\n        )\n''',
    '''        return SimpleNamespace(\n            workflow_id=workflow_id,\n            authoritative=True,\n            code="AUTHORITATIVE_COMPLETION_COMMITTED",\n            phase="CLOSED",\n            decision_id="DECISION:RUN-1",\n            policy_fingerprint="sha256:POLICY-1",\n        )\n''',
)
replace_once(
    obs_test,
    '''    assert any(attrs.get("hao.run.id") == "RUN-1" for attrs in all_attributes)\n''',
    '''    assert any(attrs.get("hao.run.id") == "RUN-1" for attrs in all_attributes)\n    assert any(attrs.get("hao.decision.id") == "DECISION:RUN-1" for attrs in all_attributes)\n    assert any(attrs.get("hao.policy.fingerprint") == "sha256:POLICY-1" for attrs in all_attributes)\n''',
)

mcp_test = ROOT / "tests" / "test_mcp_control_bridge.py"
replace_once(
    mcp_test,
    '''            acceptance_criteria=("verified",),\n            phase=phase,\n            action=action,\n        )\n''',
    '''            acceptance_criteria=("verified",),\n            policy_fingerprint="sha256:FAKE-POLICY",\n            decision_id="DECISION:" + workflow_id,\n            phase=phase,\n            action=action,\n        )\n''',
)
replace_once(
    mcp_test,
    '''        assert view.task == "MCP controlled task"\n        assert production.records[view.workflow_id].mode == Mode.EXP\n''',
    '''        assert view.task == "MCP controlled task"\n        assert view.decision_id == "DECISION:" + view.workflow_id\n        assert view.policy_fingerprint == "sha256:FAKE-POLICY"\n        assert production.records[view.workflow_id].mode == Mode.EXP\n''',
)
