from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping

from .controlled_runner import PreExecutionGateOutcome
from .execution_control import (
    ActionProposal,
    EvidenceKind,
    EvidenceOrigin,
    EvidenceReceipt,
    ExecutionRecord,
)


GATE_ID = "GATE_V3_SOURCE_BOUND"
SCHEMA = "hao.385.gate-v3-pre-execution.v1"

MIN_TARGET_PNP_RATIO = 0.65
MIN_INDEPENDENT_DONORS = 2
MIN_PLANE_SUPPORT = 10
MIN_SHARED_PLANE_SUPPORT_PER_DONOR = 8
MIN_PROJECTED_PATCH_VALID_FRACTION = 0.98
MAX_VISIBLE_RING_MAE_PER_DONOR = 12.0
MIN_VISIBLE_RING_SSIM_PER_DONOR = 0.91
MIN_DONOR_CONSENSUS_SSIM = 0.90
MAX_DONOR_CONSENSUS_MAE = 8.0

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_KEYS = frozenset(
    {
        "ground_truth_score",
        "ground_truth_success",
        "visible_ring_ssim",
        "visible_ring_structural_ssim",
        "thresholds",
        "semantic_or_naturalness_verified",
        "final_delivery_authorized",
    }
)

_TOP_KEYS = frozenset(
    {
        "schema",
        "frozen_gate_sha256",
        "canonical_scorer_sha256",
        "source_result_sha256",
        "core_model_sha256",
        "target",
        "donors",
        "patch",
        "admitted",
    }
)
_TARGET_KEYS = frozenset({"name", "sha256", "pnp_ratio"})
_DONOR_KEYS = frozenset({"name", "sha256"})
_PATCH_BASE_KEYS = frozenset(
    {
        "center",
        "bbox",
        "plane_support",
        "plane_median_dist",
        "plane_threshold",
        "plane_scale",
        "plane_normalized_median_dist",
        "support_span",
        "candidate_donors",
    }
)
_PATCH_POST_CONSENSUS_KEYS = frozenset(
    set(_PATCH_BASE_KEYS) | {"consensus", "outside_changed_pixels"}
)
_PATCH_DONOR_KEYS = frozenset(
    {
        "donor",
        "ring_mae",
        "ring_ssim",
        "shared_plane",
        "valid_fraction",
    }
)
_CONSENSUS_KEYS = frozenset({"donors", "ssim", "mae"})


@dataclass(frozen=True)
class GateV3ExpectedProvenance:
    """Trusted source identity supplied outside model output.

    The verifier compares every source identity carried by the candidate receipt
    against this trusted envelope before any action can enter EXECUTING.
    """

    receipt_sha256: str
    frozen_gate_sha256: str
    canonical_scorer_sha256: str
    source_result_sha256: str
    core_model_sha256: str
    target_name: str
    target_sha256: str
    donor_sha256s: tuple[tuple[str, str], ...]


def _object(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("OBJECT_REQUIRED:" + field)
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = set(value)
    if actual != set(expected):
        raise ValueError(
            "SCHEMA_KEYS_MISMATCH:"
            + field
            + ":missing="
            + ",".join(sorted(set(expected) - actual))
            + ":extra="
            + ",".join(sorted(actual - set(expected)))
        )


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("STRING_REQUIRED:" + field)
    return value


def _sha(value: object, field: str) -> str:
    text = _string(value, field)
    if not _HEX64.fullmatch(text):
        raise ValueError("SHA256_REQUIRED:" + field)
    return text


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("FINITE_NUMBER_REQUIRED:" + field)
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("FINITE_NUMBER_REQUIRED:" + field)
    return result


def _nonnegative(value: object, field: str) -> float:
    result = _number(value, field)
    if result < 0:
        raise ValueError("NONNEGATIVE_REQUIRED:" + field)
    return result


def _unit(value: object, field: str) -> float:
    result = _number(value, field)
    if result < 0 or result > 1:
        raise ValueError("UNIT_INTERVAL_REQUIRED:" + field)
    return result


def _integer(value: object, field: str) -> int:
    result = _number(value, field)
    if result < 0 or not result.is_integer():
        raise ValueError("NONNEGATIVE_INTEGER_REQUIRED:" + field)
    return int(result)


def _int_list(value: object, length: int, field: str) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("INTEGER_LIST_REQUIRED:" + field)
    result = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"INTEGER_REQUIRED:{field}[{index}]")
        result.append(item)
    return tuple(result)


def _number_list(value: object, length: int, field: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("NUMBER_LIST_REQUIRED:" + field)
    return tuple(_number(item, f"{field}[{index}]") for index, item in enumerate(value))


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("STRING_LIST_REQUIRED:" + field)
    return tuple(_string(item, f"{field}[]") for item in value)


def _reject_forbidden(value: object, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in _FORBIDDEN_KEYS:
                raise ValueError("FORBIDDEN_FIELD:" + path + "." + key)
            _reject_forbidden(child, path + "." + str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden(child, f"{path}[{index}]")


def _provenance_map(expected: GateV3ExpectedProvenance) -> dict[str, str]:
    donors: dict[str, str] = {}
    for name, digest in expected.donor_sha256s:
        clean_name = _string(name, "expected.donor.name")
        clean_digest = _sha(digest, "expected.donor.sha256")
        if clean_name in donors:
            raise ValueError("EXPECTED_DONOR_DUPLICATE:" + clean_name)
        donors[clean_name] = clean_digest
    if len(donors) < MIN_INDEPENDENT_DONORS:
        raise ValueError("EXPECTED_DONOR_COUNT_BELOW_MIN")
    return donors


def _mismatch(field: str) -> ValueError:
    return ValueError("PROVENANCE_MISMATCH:" + field)


class GateV3SourceBoundPreExecutionVerifier:
    """Fail-closed verifier for the native Gate-v3 pre-execution interface.

    It consumes an already source-bound normalized receipt. Ground-truth and the
    abstract PR-only ring fields are not admitted into this schema. The verifier
    emits only PRE_EXECUTION_GATE_PASS; it cannot mint completion acceptance.
    """

    def __init__(
        self,
        receipt_json: bytes | str,
        *,
        expected: GateV3ExpectedProvenance,
        source: str,
    ) -> None:
        self._raw = (
            receipt_json.encode("utf-8")
            if isinstance(receipt_json, str)
            else bytes(receipt_json)
        )
        self._expected = expected
        self._source = source.strip()

    def _validate(self) -> tuple[bool, tuple[str, ...]]:
        if not self._source:
            raise ValueError("SOURCE_REQUIRED")

        expected_donors = _provenance_map(self._expected)
        expected_receipt_sha = _sha(
            self._expected.receipt_sha256,
            "expected.receipt_sha256",
        )
        if sha256(self._raw).hexdigest() != expected_receipt_sha:
            raise _mismatch("receipt_sha256")

        try:
            decoded = json.loads(self._raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("RECEIPT_JSON_INVALID") from exc
        root = _object(decoded, "root")
        _reject_forbidden(root)
        _exact_keys(root, _TOP_KEYS, "root")

        if _string(root["schema"], "schema") != SCHEMA:
            raise ValueError("SCHEMA_VERSION_INVALID")

        provenance_pairs = (
            ("frozen_gate_sha256", self._expected.frozen_gate_sha256),
            ("canonical_scorer_sha256", self._expected.canonical_scorer_sha256),
            ("source_result_sha256", self._expected.source_result_sha256),
            ("core_model_sha256", self._expected.core_model_sha256),
        )
        for field, expected_value in provenance_pairs:
            actual = _sha(root[field], field)
            trusted = _sha(expected_value, "expected." + field)
            if actual != trusted:
                raise _mismatch(field)

        target = _object(root["target"], "target")
        _exact_keys(target, _TARGET_KEYS, "target")
        target_name = _string(target["name"], "target.name")
        target_sha = _sha(target["sha256"], "target.sha256")
        if target_name != _string(self._expected.target_name, "expected.target_name"):
            raise _mismatch("target.name")
        if target_sha != _sha(self._expected.target_sha256, "expected.target_sha256"):
            raise _mismatch("target.sha256")
        pnp_ratio = _unit(target["pnp_ratio"], "target.pnp_ratio")

        raw_donors = root["donors"]
        if not isinstance(raw_donors, list):
            raise ValueError("LIST_REQUIRED:donors")
        donor_map: dict[str, str] = {}
        for index, item in enumerate(raw_donors):
            donor = _object(item, f"donors[{index}]")
            _exact_keys(donor, _DONOR_KEYS, f"donors[{index}]")
            name = _string(donor["name"], f"donors[{index}].name")
            digest = _sha(donor["sha256"], f"donors[{index}].sha256")
            if name in donor_map:
                raise ValueError("DONOR_DUPLICATE:" + name)
            donor_map[name] = digest
        if donor_map != expected_donors:
            raise _mismatch("donors")

        patch = _object(root["patch"], "patch")
        patch_keys = set(patch)
        if (
            patch_keys != set(_PATCH_BASE_KEYS)
            and patch_keys != set(_PATCH_POST_CONSENSUS_KEYS)
        ):
            raise ValueError(
                "PATCH_STAGE_SCHEMA_INVALID:keys="
                + ",".join(sorted(patch_keys))
            )
        _int_list(patch["center"], 2, "patch.center")
        bbox = _int_list(patch["bbox"], 4, "patch.bbox")
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise ValueError("BBOX_ORDER_INVALID")
        plane_support = _integer(patch["plane_support"], "patch.plane_support")
        _nonnegative(patch["plane_median_dist"], "patch.plane_median_dist")
        _nonnegative(patch["plane_threshold"], "patch.plane_threshold")
        _nonnegative(patch["plane_scale"], "patch.plane_scale")
        _nonnegative(
            patch["plane_normalized_median_dist"],
            "patch.plane_normalized_median_dist",
        )
        support_span = _number_list(patch["support_span"], 2, "patch.support_span")
        if any(value < 0 for value in support_span):
            raise ValueError("SUPPORT_SPAN_NEGATIVE")

        raw_candidates = patch["candidate_donors"]
        if not isinstance(raw_candidates, list):
            raise ValueError("LIST_REQUIRED:patch.candidate_donors")
        candidate_names: list[str] = []
        donor_reasons: list[str] = []
        for index, item in enumerate(raw_candidates):
            donor = _object(item, f"patch.candidate_donors[{index}]")
            _exact_keys(
                donor,
                _PATCH_DONOR_KEYS,
                f"patch.candidate_donors[{index}]",
            )
            name = _string(
                donor["donor"],
                f"patch.candidate_donors[{index}].donor",
            )
            if name not in donor_map:
                raise ValueError("PATCH_DONOR_NOT_SOURCE_BOUND:" + name)
            if name in candidate_names:
                raise ValueError("PATCH_DONOR_DUPLICATE:" + name)
            candidate_names.append(name)
            ring_mae = _nonnegative(
                donor["ring_mae"],
                f"patch.candidate_donors[{index}].ring_mae",
            )
            ring_ssim = _unit(
                donor["ring_ssim"],
                f"patch.candidate_donors[{index}].ring_ssim",
            )
            shared_plane = _integer(
                donor["shared_plane"],
                f"patch.candidate_donors[{index}].shared_plane",
            )
            valid_fraction = _unit(
                donor["valid_fraction"],
                f"patch.candidate_donors[{index}].valid_fraction",
            )
            if shared_plane < MIN_SHARED_PLANE_SUPPORT_PER_DONOR:
                donor_reasons.append(name + ":SHARED_PLANE_BELOW_MIN")
            if valid_fraction < MIN_PROJECTED_PATCH_VALID_FRACTION:
                donor_reasons.append(name + ":VALID_FRACTION_BELOW_MIN")
            if ring_mae > MAX_VISIBLE_RING_MAE_PER_DONOR:
                donor_reasons.append(name + ":RING_MAE_ABOVE_MAX")
            if ring_ssim < MIN_VISIBLE_RING_SSIM_PER_DONOR:
                donor_reasons.append(name + ":RING_SSIM_BELOW_MIN")

        has_consensus = "consensus" in patch
        has_outside = "outside_changed_pixels" in patch
        if has_consensus != has_outside:
            raise ValueError("PATCH_STAGE_FIELDS_INCOMPLETE")

        consensus_ssim: float | None = None
        consensus_mae: float | None = None
        outside_changed: int | None = None
        if has_consensus:
            consensus = _object(patch["consensus"], "patch.consensus")
            _exact_keys(consensus, _CONSENSUS_KEYS, "patch.consensus")
            consensus_donors = _string_list(
                consensus["donors"],
                "patch.consensus.donors",
            )
            if len(set(consensus_donors)) != len(consensus_donors):
                raise ValueError("CONSENSUS_DONOR_DUPLICATE")
            if tuple(consensus_donors) != tuple(candidate_names):
                raise ValueError("CONSENSUS_DONOR_IDENTITY_MISMATCH")
            consensus_ssim = _unit(consensus["ssim"], "patch.consensus.ssim")
            consensus_mae = _nonnegative(consensus["mae"], "patch.consensus.mae")
            outside_changed = _integer(
                patch["outside_changed_pixels"],
                "patch.outside_changed_pixels",
            )
        elif len(candidate_names) >= MIN_INDEPENDENT_DONORS:
            raise ValueError("CONSENSUS_REQUIRED_AFTER_DONOR_STAGE")

        reasons: list[str] = []
        if pnp_ratio < MIN_TARGET_PNP_RATIO:
            reasons.append("TARGET_PNP_RATIO_BELOW_MIN")
        if plane_support < MIN_PLANE_SUPPORT:
            reasons.append("PLANE_SUPPORT_BELOW_MIN")
        if len(candidate_names) < MIN_INDEPENDENT_DONORS:
            reasons.append("INDEPENDENT_DONOR_COUNT_BELOW_MIN")
        reasons.extend(donor_reasons)
        if consensus_ssim is not None:
            if consensus_ssim < MIN_DONOR_CONSENSUS_SSIM:
                reasons.append("CONSENSUS_SSIM_BELOW_MIN")
            if consensus_mae is None or consensus_mae > MAX_DONOR_CONSENSUS_MAE:
                reasons.append("CONSENSUS_MAE_ABOVE_MAX")
            if outside_changed != 0:
                reasons.append("OUTSIDE_MASK_CHANGED")

        computed_admitted = not reasons
        claimed_admitted = root["admitted"]
        if not isinstance(claimed_admitted, bool):
            raise ValueError("BOOLEAN_REQUIRED:admitted")
        if claimed_admitted != computed_admitted:
            raise ValueError("ADMISSION_DECISION_MISMATCH")
        return computed_admitted, tuple(reasons)

    def verify(
        self,
        record: ExecutionRecord,
        proposal: ActionProposal,
    ) -> PreExecutionGateOutcome:
        del record
        try:
            admitted, reasons = self._validate()
        except ValueError as exc:
            return PreExecutionGateOutcome(
                False,
                error_code="GATE_V3_SOURCE_BOUND_INVALID:" + str(exc),
            )
        if not admitted:
            return PreExecutionGateOutcome(
                False,
                error_code=(
                    "GATE_V3_SOURCE_BOUND_REJECTED:"
                    + ",".join(reasons)
                ),
            )

        digest = sha256(self._raw).hexdigest()
        return PreExecutionGateOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    evidence_id="PREEXEC:GATE_V3:" + digest,
                    kind=EvidenceKind.PRE_EXECUTION_GATE_PASS,
                    passed=True,
                    source=self._source + "#sha256:" + digest,
                    claim_scope=proposal.action_id,
                    origin=EvidenceOrigin.VERIFIER,
                    gate_id=GATE_ID,
                ),
            ),
        )
