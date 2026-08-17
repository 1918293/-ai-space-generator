#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(cmd):
    p = subprocess.run(cmd, text=True)
    return p.returncode


def main():
    ap = argparse.ArgumentParser(description="Single-command salon portrait production gate")
    ap.add_argument("--source", required=True)
    ap.add_argument("--candidate")
    ap.add_argument("--matte")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--short-edge-target", type=int, default=1600)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    preflight = out / "preflight.json"
    hairqa = out / "hair_qa.json"

    pre_cmd = [
        sys.executable, str(root / "preflight.py"),
        "--source", args.source,
        "--output", str(preflight),
        "--short-edge-target", str(args.short_edge_target),
    ]
    if args.matte:
        pre_cmd += ["--matte", args.matte]
    pre_rc = run(pre_cmd)
    if pre_rc != 0:
        raise SystemExit(f"preflight failed with exit code {pre_rc}")

    hair_rc = None
    hair_status = "NOT_RUN"
    if args.candidate and args.matte:
        hair_cmd = [
            sys.executable, str(root / "hair_qa.py"),
            "--authority", args.source,
            "--candidate", args.candidate,
            "--matte", args.matte,
            "--output", str(hairqa),
        ]
        hair_rc = run(hair_cmd)
        if hairqa.exists():
            hair_status = json.loads(hairqa.read_text(encoding="utf-8")).get("status", "UNKNOWN")
    elif args.candidate and not args.matte:
        hair_status = "REVIEW_MATTE_REQUIRED"
    elif not args.candidate:
        hair_status = "PREFLIGHT_ONLY_NO_CANDIDATE"

    pre = json.loads(preflight.read_text(encoding="utf-8"))
    if args.candidate and args.matte:
        status = "PASS_PRODUCTION_GATE" if hair_rc == 0 else "REVIEW_PRODUCTION_GATE"
    elif args.candidate:
        status = "REVIEW_PRODUCTION_GATE"
    else:
        status = "PREFLIGHT_COMPLETE"

    summary = {
        "status": status,
        "route": pre.get("route", []),
        "hair_qa_status": hair_status,
        "inputs": {
            "source": str(Path(args.source)),
            "candidate": str(Path(args.candidate)) if args.candidate else None,
            "matte": str(Path(args.matte)) if args.matte else None,
        },
        "policy": {
            "private_local_files_supported": True,
            "no_repo_upload_required": True,
            "no_auto_inpainting_without_explicit_defect_mask": True,
            "skip_unneeded_expensive_stages": True,
            "candidate_promotion_requires_hair_qa_when_candidate_present": True,
            "visual_acceptance_is_separate": True,
        },
    }
    (out / "production_decision.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    md = [
        "# Salon Production Gate",
        "",
        f"Status: **{status}**",
        f"Route: `{' -> '.join(pre.get('route', []))}`",
        f"Hair QA: **{hair_status}**",
        "",
        "## Policy",
        "- Run preflight first and skip unnecessary expensive stages.",
        "- Never auto-inpaint without an explicit defect mask.",
        "- Candidate promotion requires hair-fidelity QA when a candidate is supplied.",
        "- Local/private files can be evaluated without committing them to GitHub.",
        "- Visual acceptance remains separate from automated metrics.",
    ]
    (out / "PRODUCTION_DECISION.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    if status == "REVIEW_PRODUCTION_GATE":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
