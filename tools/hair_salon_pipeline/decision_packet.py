#!/usr/bin/env python3
import argparse, json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8')) if path else None


def main():
    ap=argparse.ArgumentParser(description='Collapse salon production evidence into the minimum necessary human decision packet')
    ap.add_argument('--production-decision',required=True)
    ap.add_argument('--orchestration',required=True)
    ap.add_argument('--hair-qa')
    ap.add_argument('--multi-image-qa')
    ap.add_argument('--output',required=True)
    ap.add_argument('--markdown-output')
    args=ap.parse_args()

    prod=read(args.production_decision); orch=read(args.orchestration); hair=read(args.hair_qa); multi=read(args.multi_image_qa)
    blocked=orch.get('blocked_stages',[]) or []
    reviews=orch.get('review_stages',[]) or []
    hair_status=(hair or {}).get('status',prod.get('hair_qa_status','NOT_RUN'))
    multi_status=(multi or {}).get('status')

    resolved=[]
    for item in orch.get('plan',[]):
        if item.get('action')=='SKIP' or item.get('auto_execute_eligible'):
            resolved.append({'stage':item.get('stage'),'result':item.get('action')})
    if hair_status=='PASS_HAIR_FIDELITY':
        resolved.append({'stage':'HAIR_QA','result':hair_status})

    decision=None; status='NO_USER_DECISION_REQUIRED'
    if blocked:
        status='INPUT_REQUIRED_BEFORE_CONTINUE'
        decision={
          'id':'RESOLVE_BLOCKED_INPUT',
          'type':'INPUT_OR_SCOPE',
          'question':'目前有要求執行但缺少必要 authority 的階段；請補齊輸入，或確認取消該階段。',
          'blocked_stages':blocked
        }
    elif hair_status not in ('PASS_HAIR_FIDELITY','NOT_RUN','PREFLIGHT_ONLY_NO_CANDIDATE'):
        status='SYSTEM_REVIEW_REQUIRED'
        decision=None
    elif multi_status=='REVIEW_SET_CONSISTENCY':
        status='DECISION_REQUIRED'
        decision={
          'id':'SET_CONSISTENCY_VISUAL_REVIEW',
          'type':'VISUAL_ACCEPTANCE',
          'question':'同組作品的髮色一致性 gate 要求人工確認；是否保留目前各張差異作為真實拍攝差異，或回到調色階段？'
        }
    elif reviews and prod.get('inputs',{}).get('candidate'):
        status='DECISION_REQUIRED'
        decision={
          'id':'FINAL_VISUAL_ACCEPTANCE',
          'type':'VISUAL_ACCEPTANCE',
          'question':'是否接受目前 candidate 的整體商業視覺方向作為正式完成版本？',
          'covers_review_stages':reviews,
          'options':[
            {'id':'ACCEPT_CURRENT','effect':'Freeze current candidate; do not reopen resolved technical stages.'},
            {'id':'REVISE_VISUAL_DIRECTION','effect':'Reopen only the specifically named visual stage(s); preserve existing technical authorities.'}
          ]
        }
    elif prod.get('inputs',{}).get('candidate') and hair_status=='PASS_HAIR_FIDELITY':
        status='DECISION_REQUIRED'
        decision={
          'id':'FINAL_VISUAL_ACCEPTANCE',
          'type':'VISUAL_ACCEPTANCE',
          'question':'技術 gate 已通過；是否接受目前 candidate 作為正式完成版本？',
          'covers_review_stages':[]
        }

    packet={
      'status':status,
      'production_status':prod.get('status'),
      'resolved_without_user':resolved,
      'blocked_stages':blocked,
      'collapsed_review_stages':reviews,
      'decision_count':1 if decision else 0,
      'next_decision':decision,
      'system_can_continue_without_user':status in ('NO_USER_DECISION_REQUIRED','SYSTEM_REVIEW_REQUIRED'),
      'policy':{
        'expose_minimum_necessary_user_decisions':True,
        'collapse_visual_stage_reviews_into_final_acceptance_when_safe':True,
        'technical_failures_remain_system_review_work_not_aesthetic_user_decisions':True,
        'do_not_reopen_resolved_technical_stages_without_new_failure':True,
        'final_visual_acceptance_remains_human':True
      }
    }
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(packet,indent=2,ensure_ascii=False),encoding='utf-8')
    if args.markdown_output:
        lines=['# Salon Decision Packet','',f"Status: **{status}**",f"Decision count: **{packet['decision_count']}**",'', '## Resolved without user']
        lines += [f"- {x['stage']}: `{x['result']}`" for x in resolved] or ['- NONE']
        if decision:
            lines += ['', '## Next required decision', f"**{decision['question']}**", '', f"Decision ID: `{decision['id']}`"]
            if decision.get('covers_review_stages'):
                lines.append('Covers: `' + ', '.join(decision['covers_review_stages']) + '`')
        elif status=='SYSTEM_REVIEW_REQUIRED':
            lines += ['', 'No user decision yet. The system must investigate the technical failure first.']
        else:
            lines += ['', 'No user decision is currently required.']
        Path(args.markdown_output).write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(packet,indent=2,ensure_ascii=False))

if __name__=='__main__': main()
