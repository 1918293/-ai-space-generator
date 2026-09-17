import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const BASE='http://127.0.0.1:8080/api/v1';
const CLOUD_PIECES='https://cloud.activepieces.com/api/v1/pieces';
const geminiKey=process.env.GEMINI_API_KEY;
const posthogKey=process.env.POSTHOG_PROJECT_API_KEY;
const runKey=`AP-FAM-${process.env.GITHUB_RUN_ID_VALUE ?? 'local'}`;
const traceId=crypto.randomUUID();
const generationId=crypto.randomUUID();
const prompt='Reply with exactly: HAO_ACTIVEPIECES_REAL_LLM_PASS';
const model='gemini-3.5-flash-lite';
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
const listData=v=>Array.isArray(v)?v:(v?.data??v?.items??[]);

async function api(path,{method='GET',credential,body,allowed=[200,201,204]}={}){
 const headers={accept:'application/json'}; if(body!==undefined)headers['content-type']='application/json'; if(credential)headers.authorization=`Bearer ${credential}`;
 const r=await fetch(`${BASE}${path}`,{method,headers,body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(60000)});
 const t=await r.text(); let p=null; if(t){try{p=JSON.parse(t)}catch{p=t}} if(!allowed.includes(r.status))throw new Error(`${method} ${path} -> ${r.status}: ${JSON.stringify(p).slice(0,1000)}`); return p;
}
async function externalJson(url){const r=await fetch(url,{headers:{accept:'application/json'},signal:AbortSignal.timeout(60000)});const t=await r.text();if(!r.ok)throw new Error(`GET ${url} -> ${r.status}: ${t.slice(0,500)}`);return JSON.parse(t)}
function parts(v){const m=String(v).match(/^(\d+)\.(\d+)\.(\d+)/);return m?m.slice(1).map(Number):[0,0,0]}
function latest(reg,n){const a=reg.filter(x=>x?.name===n&&/^\d+\.\d+\.\d+/.test(String(x?.version??'')));a.sort((x,y)=>{const A=parts(x.version),B=parts(y.version);for(let i=0;i<3;i++)if(A[i]!==B[i])return B[i]-A[i];return 0});return a[0]?.version}
const range=v=>v.startsWith('~')?v:`~${v}`;
async function waitRun({credential,projectId,flowId}){for(let i=0;i<120;i++){const q=new URLSearchParams({projectId,flowId,limit:'10'});const runs=listData(await api(`/flow-runs?${q}`,{credential}));const r=runs.find(x=>x?.status&&!['RUNNING','QUEUED','PAUSED'].includes(x.status));if(r)return r;await sleep(1000)}throw new Error('flow run timeout')}

assert.ok(geminiKey&&posthogKey,'required secrets missing');
const password=crypto.randomBytes(18).toString('hex')+'Aa1!';
let auth=await api('/authentication/sign-up',{method:'POST',body:{email:`hao-ap-gemini-${Date.now()}@example.test`,password,firstName:'Hao',lastName:'FAM',trackEvents:false,newsLetter:false}});
let credential=auth?.token,projectId=auth?.projectId;
if(!projectId){const p=await api('/platforms',{method:'POST',credential,body:{name:'Hao AP Gemini FAM'}});credential=p?.token;projectId=p?.projectId}
assert.ok(credential&&projectId);
const flags=await api('/flags',{credential});
const registryUrl=new URL(`${CLOUD_PIECES}/registry`);registryUrl.searchParams.set('edition',flags.EDITION);registryUrl.searchParams.set('release',flags.CURRENT_VERSION);
const registry=listData(await externalJson(registryUrl));
const webhookExact=latest(registry,'@activepieces/piece-webhook'),httpExact=latest(registry,'@activepieces/piece-http');assert.ok(webhookExact&&httpExact);
for(const [pieceName,pieceVersion] of [['@activepieces/piece-webhook',webhookExact],['@activepieces/piece-http',httpExact]])await api('/pieces',{method:'POST',credential,body:{packageType:'REGISTRY',scope:'PLATFORM',pieceName,pieceVersion}});
const flow=await api(`/flows?projectId=${encodeURIComponent(projectId)}`,{method:'POST',credential,body:{displayName:`Hao Gemini PostHog FAM ${runKey}`,projectId}});const flowId=flow.id;assert.ok(flowId);
await api(`/flows/${flowId}`,{method:'POST',credential,body:{type:'UPDATE_TRIGGER',request:{name:'trigger',valid:true,displayName:'Catch Webhook',type:'PIECE_TRIGGER',lastUpdatedDate:new Date().toISOString(),settings:{pieceName:'@activepieces/piece-webhook',pieceVersion:range(webhookExact),triggerName:'catch_webhook',input:{authType:'none'},propertySettings:{}}}}});
await api(`/flows/${flowId}`,{method:'POST',credential,body:{type:'ADD_ACTION',request:{parentStep:'trigger',action:{name:'step_1',skip:false,type:'PIECE',valid:true,displayName:'Gemini Free Tier real generation',settings:{input:{method:'POST',url:`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${geminiKey}`,headers:{'Content-Type':'application/json'},queryParams:{},authType:'NONE',body_type:'json',body:{data:{contents:[{parts:[{text:prompt}]}],generationConfig:{maxOutputTokens:32}}},response_is_binary:false,use_proxy:false,timeout:30,followRedirects:false,failureMode:'retry_none'},pieceName:'@activepieces/piece-http',actionName:'send_request',pieceVersion:range(httpExact),propertySettings:{},errorHandlingOptions:{}}}}}});
await api(`/flows/${flowId}`,{method:'POST',credential,body:{type:'ADD_ACTION',request:{parentStep:'step_1',action:{name:'step_2',skip:false,type:'PIECE',valid:true,displayName:'Capture verified generation in PostHog',settings:{input:{method:'POST',url:'https://eu.i.posthog.com/i/v0/e/',headers:{'Content-Type':'application/json'},queryParams:{},authType:'NONE',body_type:'json',body:{data:{api_key:posthogKey,event:'$ai_generation',distinct_id:`hao-activepieces-${runKey}`,properties:{'$ai_trace_id':traceId,'$ai_generation_id':generationId,'$ai_trace_name':'Hao Activepieces real LLM E2E','$ai_provider':'google','$ai_model':model,'$ai_input':prompt,'$ai_output_choices':[{'role':'assistant','content':"{{step_1['body']['candidates'][0]['content']['parts'][0]['text']}}"}],'$ai_input_tokens':"{{step_1['body']['usageMetadata']['promptTokenCount']}}",'$ai_output_tokens':"{{step_1['body']['usageMetadata']['candidatesTokenCount']}}",'$ai_is_error':false,run_key:runKey,is_synthetic:false,measurement_source:'activepieces_real_gemini_free_tier',production_canonical_target_touched:false}}},response_is_binary:false,use_proxy:false,timeout:30,followRedirects:false,failureMode:'retry_none'},pieceName:'@activepieces/piece-http',actionName:'send_request',pieceVersion:range(httpExact),propertySettings:{},errorHandlingOptions:{}}}}}});
await api(`/flows/${flowId}`,{method:'POST',credential,body:{type:'LOCK_AND_PUBLISH',request:{}}});
for(let i=0;i<60;i++){const f=await api(`/flows/${flowId}`,{credential});if(f?.status==='ENABLED')break;if(i===59)throw new Error('flow not enabled');await sleep(1000)}
const r=await fetch(`${BASE}/webhooks/${encodeURIComponent(flowId)}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({runKey}),signal:AbortSignal.timeout(60000)});assert.ok(r.ok,`webhook ${r.status}`);
const terminal=await waitRun({credential,projectId,flowId});assert.equal(terminal.status,'SUCCEEDED',`flow status ${terminal.status}`);
console.log(JSON.stringify({event:'hao_activepieces_real_llm_e2e',result:'FLOW_PASS',providerRelease:flags.CURRENT_VERSION,flowId,flowRunId:terminal.id,runKey,traceId,generationId,model,productionCanonicalTargetTouched:false},null,2));
