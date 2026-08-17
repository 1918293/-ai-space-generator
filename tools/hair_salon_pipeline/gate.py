#!/usr/bin/env python3
import argparse, json, subprocess, sys
from pathlib import Path


def run(cmd):
    return subprocess.run(cmd, text=True).returncode


def main():
    ap=argparse.ArgumentParser(description='Single-command salon portrait production gate')
    ap.add_argument('--source',required=True); ap.add_argument('--candidate'); ap.add_argument('--matte')
    ap.add_argument('--hair-mask',help='Optional dedicated source-derived hair mask')
    ap.add_argument('--defect-mask',help='Optional explicit defect mask authority')
    ap.add_argument('--object-removal-requested',action='store_true',help='Declare object removal as part of the current job intent')
    ap.add_argument('--allow-safe-sr',action='store_true',help='Allow conservative non-destructive SR to become auto-execute eligible')
    ap.add_argument('--output-dir',required=True); ap.add_argument('--short-edge-target',type=int,default=1600)
    args=ap.parse_args()

    root=Path(__file__).resolve().parent; out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    preflight=out/'preflight.json'; plan=out/'orchestration_plan.json'; hairqa=out/'hair_qa.json'
    object_removal_requested=bool(args.object_removal_requested or args.defect_mask)

    pre_cmd=[sys.executable,str(root/'preflight.py'),'--source',args.source,'--output',str(preflight),'--short-edge-target',str(args.short_edge_target)]
    if args.matte: pre_cmd += ['--matte',args.matte]
    if object_removal_requested: pre_cmd += ['--object-removal-requested']
    if run(pre_cmd)!=0: raise SystemExit('preflight failed')

    orch=[sys.executable,str(root/'orchestrator.py'),'--preflight',str(preflight),'--output',str(plan)]
    if args.defect_mask: orch += ['--defect-mask',args.defect_mask]
    if args.candidate: orch += ['--candidate-available']
    if args.allow_safe_sr: orch += ['--allow-safe-sr']
    if run(orch)!=0: raise SystemExit('orchestration planning failed')

    hair_rc=None; hair_status='NOT_RUN'
    if args.candidate and args.matte:
        cmd=[sys.executable,str(root/'hair_qa.py'),'--authority',args.source,'--candidate',args.candidate,'--matte',args.matte,'--output',str(hairqa)]
        if args.hair_mask: cmd += ['--hair-mask',args.hair_mask]
        hair_rc=run(cmd)
        if hairqa.exists(): hair_status=json.loads(hairqa.read_text(encoding='utf-8')).get('status','UNKNOWN')
    elif args.candidate: hair_status='REVIEW_MATTE_REQUIRED'
    else: hair_status='PREFLIGHT_ONLY_NO_CANDIDATE'

    pre=json.loads(preflight.read_text(encoding='utf-8')); execution=json.loads(plan.read_text(encoding='utf-8'))
    if args.candidate and args.matte: status='PASS_PRODUCTION_GATE' if hair_rc==0 else 'REVIEW_PRODUCTION_GATE'
    elif args.candidate: status='REVIEW_PRODUCTION_GATE'
    else: status='PREFLIGHT_COMPLETE'

    summary={
      'status':status,'route':pre.get('route',[]),'hair_qa_status':hair_status,
      'orchestration_status':execution.get('status'),'auto_execute_eligible_stages':execution.get('auto_execute_eligible_stages',[]),
      'review_stages':execution.get('review_stages',[]),'blocked_stages':execution.get('blocked_stages',[]),
      'intent':{'object_removal_requested':object_removal_requested},
      'inputs':{'source':str(Path(args.source)),'candidate':str(Path(args.candidate)) if args.candidate else None,'matte':str(Path(args.matte)) if args.matte else None,'hair_mask':str(Path(args.hair_mask)) if args.hair_mask else None,'defect_mask':str(Path(args.defect_mask)) if args.defect_mask else None},
      'hair_mask_mode':'DEDICATED' if args.hair_mask else 'PROXY_FALLBACK',
      'policy':{'private_local_files_supported':True,'no_repo_upload_required':True,'router_never_equals_execution_permission':True,'undeclared_object_removal_is_skipped_not_blocked':True,'no_auto_inpainting_without_explicit_defect_mask':True,'bokeh_and_tone_remain_visual_review':True,'skip_unneeded_expensive_stages':True,'dedicated_source_derived_hair_mask_preferred':True,'candidate_promotion_requires_hair_qa_when_candidate_present':True,'visual_acceptance_is_separate':True}
    }
    (out/'production_decision.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    md=['# Salon Production Gate','',f"Status: **{status}**",f"Route: `{' -> '.join(pre.get('route',[]))}`",f"Hair QA: **{hair_status}**",f"Hair mask mode: **{summary['hair_mask_mode']}**",f"Object removal requested: **{object_removal_requested}**",f"Auto-eligible: `{', '.join(summary['auto_execute_eligible_stages']) or 'NONE'}`",f"Review: `{', '.join(summary['review_stages']) or 'NONE'}`",f"Blocked: `{', '.join(summary['blocked_stages']) or 'NONE'}`",'', 'Router decisions never equal execution permission; source overwrite and blind generative edits remain prohibited.']
    (out/'PRODUCTION_DECISION.md').write_text('\n'.join(md),encoding='utf-8'); print(json.dumps(summary,indent=2))
    if status=='REVIEW_PRODUCTION_GATE': raise SystemExit(2)

if __name__=='__main__': main()
