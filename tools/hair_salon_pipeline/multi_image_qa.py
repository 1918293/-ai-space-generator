#!/usr/bin/env python3
import argparse, json, math
from pathlib import Path
import cv2, numpy as np
from PIL import Image


def mask(path,size):
    im=Image.open(path).convert('L')
    if im.size!=size: raise ValueError(f'mask size {im.size} != {size}')
    return np.asarray(im)>=128

def grad(rgb):
    g=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY).astype(np.float32)
    x=cv2.Sobel(g,cv2.CV_32F,1,0,ksize=3); y=cv2.Sobel(g,cv2.CV_32F,0,1,ksize=3)
    return np.sqrt(x*x+y*y)

def hdiff(a,b):
    d=abs(float(a)-float(b))%360; return min(d,360-d)

def hcenter(v):
    a=np.deg2rad(v); return float((np.degrees(np.arctan2(np.mean(np.sin(a)),np.mean(np.cos(a))))+360)%360)

def rel(v):
    x=np.asarray(v,float); return np.full(len(x),.5) if len(x)<2 or np.ptp(x)<1e-9 else (x-x.min())/(x.max()-x.min())

def one(x):
    rgb=np.asarray(Image.open(x['image']).convert('RGB')); H,W=rgb.shape[:2]
    hm=mask(x['hair_mask'],(W,H)); n=int(hm.sum())
    if n<1000: raise ValueError(f"hair mask too small: {x['id']}")
    e=grad(rgb); hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV).astype(np.float32); h,s,v=hsv[:,:,0],hsv[:,:,1],hsv[:,:,2]
    a=np.deg2rad(h[hm]*2); hue=float((np.degrees(np.arctan2(np.mean(np.sin(a)),np.mean(np.cos(a))))+360)%360)
    dil=cv2.dilate(hm.astype(np.uint8),np.ones((31,31),np.uint8))>0
    if x.get('subject_matte'):
        sm=np.asarray(Image.open(x['subject_matte']).convert('L')).astype(np.float32)/255
        if sm.shape!=(H,W): raise ValueError('subject matte size mismatch')
        bg=(sm<.20)&~dil
    else:
        yy,xx=np.indices((H,W)); bg=((yy<.22*H)|(xx<.08*W)|(xx>.92*W))&~dil
    if bg.sum()<1000: bg=~dil
    border=np.zeros((H,W),bool); b=max(4,int(min(H,W)*.01)); border[:b]=border[-b:]=True; border[:,:b]=True; border[:,-b:]=True
    hg=float(e[hm].mean()); bgd=float(e[bg].mean())
    return {'id':str(x['id']),'image':x['image'],'hair_mask':x['hair_mask'],'hair_pixels':n,'hair_frame_fraction':float(hm.mean()),
      'hair_hue_deg':hue,'hair_saturation_median_8bit':float(np.median(s[hm])),'hair_value_median_8bit':float(np.median(v[hm])),
      'hair_gradient_mean':hg,'background_gradient_mean':bgd,'hair_to_background_detail_ratio':hg/max(bgd,1e-6),
      'hair_highlight_clip_fraction':float(np.mean(np.any(rgb>=255,2)[hm])),'hair_deep_shadow_fraction':float(np.mean(v[hm]<25)),
      'hair_border_touch_fraction':float(np.mean(border[hm]))}

def main():
    ap=argparse.ArgumentParser(description='Same-look salon set consistency + technical Hero advisory')
    ap.add_argument('--manifest',required=True); ap.add_argument('--output',required=True); ap.add_argument('--markdown-output')
    z=ap.parse_args(); man=json.loads(Path(z.manifest).read_text()); items=man.get('items') or []
    if len(items)<2 or len({str(x.get('id')) for x in items})!=len(items): raise ValueError('need >=2 unique items')
    rows=[one(x) for x in items]; hues=[r['hair_hue_deg'] for r in rows]; sats=np.array([r['hair_saturation_median_8bit'] for r in rows]); vals=np.array([r['hair_value_median_8bit'] for r in rows])
    hc=hcenter(hues); sc=float(np.median(sats)); vc=float(np.median(vals))
    for r in rows:
        r['hue_deviation_from_set_deg']=hdiff(r['hair_hue_deg'],hc); r['saturation_deviation_from_set_8bit']=abs(r['hair_saturation_median_8bit']-sc); r['value_deviation_from_set_8bit']=abs(r['hair_value_median_8bit']-vc)
        r['color_consistency_score']=float(np.exp(-(r['hue_deviation_from_set_deg']/6)**2)*np.exp(-(r['saturation_deviation_from_set_8bit']/18)**2))
    maxh=max(r['hue_deviation_from_set_deg'] for r in rows); sr=float(np.ptp(sats)); vr=float(np.ptp(vals)); gates={'max_hue_deviation_within_6deg':maxh<=6,'saturation_range_within_20':sr<=20}; status='PASS_SET_CONSISTENCY' if all(gates.values()) else 'REVIEW_SET_CONSISTENCY'
    d=rel([r['hair_gradient_mean'] for r in rows]); sep=rel([r['hair_to_background_detail_ratio'] for r in rows]); col=np.array([r['color_consistency_score'] for r in rows]); exp=np.array([max(0,1-min(1,r['hair_highlight_clip_fraction']/.01)-min(1,r['hair_deep_shadow_fraction']/.15)) for r in rows]); frm=np.array([max(0,1-min(1,r['hair_border_touch_fraction']/.05)) for r in rows]); score=.35*d+.25*sep+.20*col+.10*exp+.10*frm
    for i,r in enumerate(rows):
        r['hero_eligible']=bool(r['hue_deviation_from_set_deg']<=6 and r['hair_highlight_clip_fraction']<=.015 and r['hair_border_touch_fraction']<=.05); r['hero_advisory_score']=float(score[i]*100); r['hero_score_components']={'hair_detail':float(d[i]),'background_separation':float(sep[i]),'color_consistency':float(col[i]),'exposure':float(exp[i]),'framing':float(frm[i])}
    ranked=sorted(rows,key=lambda r:r['hero_advisory_score'],reverse=True); eligible=[r for r in ranked if r['hero_eligible']]; hero=eligible[0]['id'] if eligible else None; rs='ADVISORY_READY' if hero and status=='PASS_SET_CONSISTENCY' else ('REVIEW_SET_BEFORE_DELIVERY' if hero else 'REVIEW_NO_HERO_ELIGIBLE')
    report={'status':status,'set_id':man.get('set_id','unnamed_set'),'image_count':len(rows),'intended_scope':'same client / same hairstyle-color service / same delivery set','set_color_center':{'hair_hue_deg':hc,'hair_saturation_median_8bit':sc,'hair_value_median_8bit':vc},'set_color_spread':{'max_hue_deviation_deg':maxh,'saturation_range_8bit':sr,'value_range_8bit':vr,'value_is_diagnostic':True},'consistency_gates':gates,'hero_recommendation':{'status':rs,'recommended_id':hero,'advisory_only':True,'ranking':[{'id':r['id'],'score':r['hero_advisory_score'],'eligible':r['hero_eligible']} for r in ranked]},'items':rows,'policy':{'hero_ranking_never_auto_promotes':True,'no_person_attractiveness_or_identity_scoring':True,'hair_masks_must_be_source_derived_or_explicit_authority':True,'do_not_compare_different_hair_services_as_one_set':True,'visual_acceptance_remains_required':True}}
    out=Path(z.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2))
    if z.markdown_output:
        md=['# Multi-image Salon Hair QA','',f"Status: **{status}**",f"Hero advisory: **{hero or 'NONE'}** ({rs})",f"Max hue deviation: `{maxh:.3f}°`",f"Saturation range: `{sr:.3f}/255`",f"Value range (diagnostic): `{vr:.3f}/255`",'','## Ranking']+[f"{i}. `{r['id']}` — {r['hero_advisory_score']:.2f} — {'eligible' if r['hero_eligible'] else 'review'}" for i,r in enumerate(ranked,1)]+['','Ranking is technical/advisory only; final visual acceptance remains human-reviewed.']
        Path(z.markdown_output).write_text('\n'.join(md))
if __name__=='__main__': main()
