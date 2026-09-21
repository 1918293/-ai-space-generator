from __future__ import annotations

import json
import statistics
import time

from src.control_gateway import PreModelContextReceipt, VerifiedModelInput
from src.execution_control import Mode
from src.responses_model_boundary import ResponsesModelBoundary


class NoopResponses:
    def create(self, **kwargs):
        return kwargs


class NoopClient:
    def __init__(self):
        self.responses = NoopResponses()


def model_input() -> VerifiedModelInput:
    return VerifiedModelInput(
        receipt=PreModelContextReceipt(
            checkpoint_id="R1",
            mode=Mode.EXP,
            task="reasoning-effort benchmark",
            operational_version=1,
            authority_refs=("CURRENT:BENCHMARK",),
            existing_work_refs=("PR17:MERGED",),
            prior_attempt_refs=(),
            regression_refs=("REG:R051",),
            reuse_disposition="REUSE",
            context_fingerprint="sha256:benchmark",
        ),
        user_text="Auto > benchmark",
    )


def run_profile(effort: str, *, iterations: int = 20000, rounds: int = 5) -> dict[str, object]:
    boundary = ResponsesModelBoundary(
        NoopClient(),
        model="gpt-test",
        reasoning_effort=effort,
        text_verbosity="low",
    )
    item = model_input()

    for _ in range(500):
        boundary.invoke(item)

    samples = []
    for _ in range(rounds):
        started = time.perf_counter_ns()
        for _ in range(iterations):
            boundary.invoke(item)
        elapsed = time.perf_counter_ns() - started
        samples.append(elapsed / iterations)

    return {
        "profile": effort,
        "rounds": rounds,
        "iterations_per_round": iterations,
        "median_ns_per_call": round(statistics.median(samples), 1),
        "min_ns_per_call": round(min(samples), 1),
        "max_ns_per_call": round(max(samples), 1),
    }


def main() -> None:
    results = [run_profile(effort) for effort in ("none", "low", "medium")]
    print(
        json.dumps(
            {
                "benchmark": "responses_model_boundary_zero_cost_request_overhead",
                "provider": "NOOP_LOCAL_CLIENT_NO_API_CALL",
                "meaning": (
                    "Measures Runtime request-construction/invocation overhead only; "
                    "does not measure OpenAI provider latency or model quality."
                ),
                "profiles": results,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
