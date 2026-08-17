#!/usr/bin/env python3
import argparse, json
from pathlib import Path


def stage(name, decision, action, reason, auto=False):
    return {"stage": name, "preflight": decision, "action": action, "auto_execute_eligible": bool(auto), "reason": reason}


def main():
    ap=argparse.ArgumentParser(description='Safety-first salon portrait execution planner')
    ap.add_argument('--preflight',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--defect-mask')
    ap.add_argument('--candidate-available',action='store_true')
    ap.add_argument('--allow-safe-sr',action='store_true',help='Permit conservative SR execution into a new output only')
    args=ap.parse_args()

    p=json.loads(Path(args.preflight).read_text(encoding='utf-8'))
    d=p.get('decisions',{})
    plan=[]

    sr=d.get('sr','UNKNOWN')
    if sr=='SKIP':
        plan.append(stage('SR',sr,'SKIP','Source already meets short-edge target.'))
    elif sr=='RUN' and args.allow_safe_sr:
        plan.append(stage('SR',sr,'AUTO_EXECUTE_CONSERVATIVE_SR','Allowed only as a new non-destructive output; no face enhancer; post-SR source-relative fidelity QA required.',True))
    elif sr=='RUN':
        plan.append(stage('SR',sr,'READY_WAITING_EXPLICIT_SAFE_SR_PERMISSION','Preflight recommends SR, but execution permission is separate from routing.'))
    else:
        plan.append(stage('SR',sr,'REVIEW','Unknown SR route.'))

    obj=d.get('object_removal','UNKNOWN')
    if args.defect_mask:
        plan.append(stage('OBJECT_REMOVAL',obj,'CONTROLLED_REVIEW_WITH_MASK','Explicit defect-mask authority exists, but inpainting/background reconstruction remains visual-review only.'))
    else:
        plan.append(stage('OBJECT_REMOVAL',obj,'BLOCK_NO_EXPLICIT_DEFECT_MASK','Never infer object-removal masks from generic pixel statistics.'))

    b=d.get('bokeh','UNKNOWN')
    if b=='SKIP':
        plan.append(stage('BOKEH',b,'SKIP','Background separation does not trigger review heuristic.'))
    else:
        plan.append(stage('BOKEH',b,'VISUAL_REVIEW_REQUIRED','Bokeh is an aesthetic/subject-edge decision; router cannot auto-apply it.'))

    t=d.get('tone','UNKNOWN')
    if t=='SKIP':
        plan.append(stage('TONE',t,'SKIP','No clipping heuristic requiring review.'))
    else:
        plan.append(stage('TONE',t,'VISUAL_REVIEW_REQUIRED','Clipping statistics can flag review but cannot choose creative exposure/color changes.'))

    if args.candidate_available:
        plan.append(stage('HAIR_QA','REQUIRED','AUTO_RUN_SOURCE_RELATIVE_HAIR_QA','Deterministic QA may run automatically; promotion still requires visual acceptance.',True))
    else:
        plan.append(stage('HAIR_QA','PENDING_CANDIDATE','WAIT_FOR_CANDIDATE','Run after any transform produces a candidate.'))

    auto=[x['stage'] for x in plan if x['auto_execute_eligible']]
    blocked=[x['stage'] for x in plan if x['action'].startswith('BLOCK')]
    reviews=[x['stage'] for x in plan if 'REVIEW' in x['action']]
    report={
      'status':'ORCHESTRATION_PLAN_READY',
      'source_preflight_status':p.get('status'),
      'plan':plan,
      'auto_execute_eligible_stages':auto,
      'review_stages':reviews,
      'blocked_stages':blocked,
      'policy':{
        'router_never_equals_permission':True,
        'only_non_destructive_sr_and_deterministic_qa_can_be_auto_eligible':True,
        'object_removal_requires_explicit_mask_and_visual_review':True,
        'bokeh_and_tone_remain_visual_review':True,
        'never_overwrite_source':True,
        'candidate_promotion_requires_fidelity_qa_and_visual_acceptance':True
      }
    }
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report,indent=2))

if __name__=='__main__': main()
