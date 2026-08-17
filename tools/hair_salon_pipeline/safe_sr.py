#!/usr/bin/env python3
import argparse, hashlib, json, subprocess, sys
from pathlib import Path
from PIL import Image

PIN='fa4c8a03ae3dbc9ea6ed471a6ab5da94ac15c2ea'

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(cmd,cwd=None): return subprocess.run(cmd,cwd=cwd,text=True,check=True)

def main():
    ap=argparse.ArgumentParser(description='Fail-closed non-destructive conservative SR executor')
    ap.add_argument('--source',required=True); ap.add_argument('--preflight',required=True); ap.add_argument('--output-dir',required=True)
    ap.add_argument('--realesrgan-root',required=True); ap.add_argument('--report',required=True); ap.add_argument('--allow-safe-sr',action='store_true')
    args=ap.parse_args()
    if not args.allow_safe_sr: raise SystemExit('REFUSED: explicit --allow-safe-sr permission required')
    src=Path(args.source).resolve(); out=Path(args.output_dir).resolve(); root=Path(args.realesrgan_root).resolve()
    if not src.is_file(): raise SystemExit('REFUSED: source missing')
    if src.parent==out or src==out: raise SystemExit('REFUSED: output must be separate from source')
    pre=json.loads(Path(args.preflight).read_text(encoding='utf-8'))
    if pre.get('decisions',{}).get('sr')!='RUN': raise SystemExit('REFUSED: preflight does not require SR')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    if head!=PIN: raise SystemExit(f'REFUSED: Real-ESRGAN commit {head} != pinned {PIN}')
    script=root/'inference_realesrgan.py'
    if not script.is_file(): raise SystemExit('REFUSED: inference script missing')
    out.mkdir(parents=True,exist_ok=True)
    before=set(out.glob('*'))
    run([sys.executable,str(script),'-n','realesr-general-x4v3','-dn','0.20','-i',str(src),'-o',str(out),'-s','2','--tile','128','--tile_pad','16','--fp32','--suffix','safe_sr_v01','--ext','png'],cwd=root)
    made=[p for p in out.glob('*.png') if p not in before]
    if not made: raise SystemExit('REFUSED: no new SR output produced')
    result=max(made,key=lambda p:p.stat().st_size)
    sw,sh=Image.open(src).size; ow,oh=Image.open(result).size
    if (ow,oh)!=(sw*2,sh*2): raise SystemExit(f'REFUSED: output geometry {(ow,oh)} != {(sw*2,sh*2)}')
    weights={p.name:sha(p) for p in (root/'weights').glob('*.pth')}
    if not weights: raise SystemExit('REFUSED: model weights not materialized')
    report={'status':'SR_EXECUTION_COMPLETE_PENDING_HAIR_QA','source':str(src),'output':str(result),'source_size':[sw,sh],'output_size':[ow,oh],'source_sha256':sha(src),'output_sha256':sha(result),'executor':{'realesrgan_commit':PIN,'model':'realesr-general-x4v3','denoise_strength':0.20,'scale':2,'fp32':True,'tile':128,'tile_pad':16,'face_enhance':False},'weight_sha256':weights,'policy':{'explicit_permission_present':True,'preflight_sr_run':True,'new_output_only':True,'source_overwrite':False,'hair_qa_required_before_promotion':True,'visual_acceptance_required':True}}
    rp=Path(args.report); rp.parent.mkdir(parents=True,exist_ok=True); rp.write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report,indent=2))

if __name__=='__main__': main()
