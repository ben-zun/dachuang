"""Frozen paired evaluation. Questions only enter model workers; no regeneration."""
import concurrent.futures
from datetime import datetime, timezone
import hashlib,json,subprocess,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
OUT=HERE/'run'
SYSTEM='准确回答用户的问题，遵守用户指定的输出格式。'
def save(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+'\n');tmp.replace(path)
def worker(arm,qid):
 sys.path.insert(0,str(ROOT))
 from backend.model.client import LLMClient
 rows=[json.loads(l) for l in (HERE/'questions500.jsonl').read_text().splitlines()]
 task=next(q for q in rows if q['qid']==qid)
 history=[];state=None
 if arm=='bkc':
  from backend.agent.runtime_v2 import RuntimeConfig,run_answer
  from backend.agent.session_state import SessionState
  from backend.api.bkc import core_search
  state=SessionState()
  config=RuntimeConfig(core=True,detailed_answers=True,thinking_budget=2048,max_tokens=4096,trace_dir=str(OUT/arm/'traces'))
 for t in task['turns']:
  ident=f'{arm}-{qid}-t{t["turn"]}'
  path=OUT/arm/(ident+'.json')
  if path.exists():raise RuntimeError('Refuse answer replacement '+str(path))
  client=LLMClient(model='qwen3.7-flash',temperature=0,enable_thinking=True,thinking_budget=2048,timeout=90,log_path=str(OUT/arm/'calls'/(ident+'.jsonl')))
  started=time.perf_counter()
  record=dict(qid=qid,turn=t['turn'],arm=arm,user=t['user'],started_utc=datetime.now(timezone.utc).isoformat())
  try:
   if arm=='base':
    output=client._request('eval500_base',[{'role':'system','content':SYSTEM}]+history+[{'role':'user','content':t['user']}],max_tokens=4096)
    record['result']={'answer':output.get('content') or '', 'raw_answer':output.get('content') or '', 'api_output':output,'tools':[]}
    history += [{'role':'user','content':t['user']},{'role':'assistant','content':output.get('content') or ''}]
   else:
    record['result']=run_answer(t['user'],client,config,session_state=state,request_id=ident,core_search=core_search)
  except Exception as exc:
   record['error']=type(exc).__name__+': '+str(exc)
   record['traceback']=traceback.format_exc()
   if arm=='base':history += [{'role':'user','content':t['user']},{'role':'assistant','content':'[本轮调用失败，未生成答案]'}]
  record['latency_seconds']=round(time.perf_counter()-started,3)
  if state is not None:record['session']=state.data
  logs=[json.loads(l) for l in client.log_path.read_text().splitlines()] if client.log_path.exists() else []
  record['llm_calls']=len(logs)
  record['input_tokens']=sum((l.get('usage') or {}).get('prompt_tokens',0) for l in logs)
  record['output_tokens']=sum((l.get('usage') or {}).get('completion_tokens',0) for l in logs)
  save(path,record)
 save(OUT/'episodes'/f'{arm}-{qid}.json',{'arm':arm,'qid':qid,'status':'recorded','turns':len(task['turns'])})
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 # Record the current revision; the original run used bcd0e7886f6b6c915178fd88cc1c1ecc7475a872.
 if (OUT/'freeze.json').exists():raise RuntimeError('Frozen run already exists; do not overwrite')
 rows=[json.loads(l) for l in (HERE/'questions500.jsonl').read_text().splitlines()]
 assert len(rows)==500
 files=list((ROOT/'backend').rglob('*.py'))+[p for p in (ROOT/'configs').rglob('*') if p.is_file()]+[p for p in (ROOT/'data').glob('material_*.*') if p.is_file()]+[HERE/'questions500.jsonl',Path(__file__).resolve()]
 hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
 freeze=dict(timestamp=datetime.now(timezone.utc).isoformat(),commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),tracked_diff=subprocess.check_output(['git','-C',str(ROOT),'diff','--stat','HEAD'],text=True),files=hashes,model='qwen3.7-flash',temperature=0,thinking=True,thinking_budget=2048,max_tokens=4096,base_system=SYSTEM,core_index='v3b-7e3555639d5fe6e7',policy='one first answer per task/arm; no retry or regeneration; multi-turn history isolated by arm; workers see questions only',tasks=500,turns_per_arm=sum(len(r['turns']) for r in rows),arms=['base','bkc'],comparison='fresh Base vs final patched BKC',total_turns=2*sum(len(r['turns']) for r in rows))
 save(OUT/'freeze.json',freeze)
 jobs=[(a,r['qid']) for r in rows for a in ['base','bkc']]
 done=[];errors=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
  futures={pool.submit(subprocess.run,[sys.executable,str(Path(__file__).resolve()),a,q],capture_output=True,text=True):(a,q) for a,q in jobs}
  for future in concurrent.futures.as_completed(futures):
   a,q=futures[future];r=future.result();done.append([a,q])
   if r.returncode:errors.append(dict(arm=a,qid=q,error=r.stderr[-2000:]))
   counts={a:len(list((OUT/a).glob(a+'-P*-t*.json'))) for a in ['base','bkc']}
   save(OUT/'progress.json',dict(timestamp=datetime.now(timezone.utc).isoformat(),completed_episodes=len(done),total_episodes=1000,completed_turns=counts,total_turns=2*sum(len(r['turns']) for r in rows),process_errors=errors))
   print(a,q,r.returncode,counts,flush=True)
 records=[json.loads(p.read_text()) for a in ['base','bkc'] for p in (OUT/a).glob(a+'-P*-t*.json')]
 changed=[p for p,h in hashes.items() if not Path(p).exists() or hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h]
 app_errors=[{'arm':r['arm'],'qid':r['qid'],'turn':r['turn'],'error':r.get('error') or r.get('result',{}).get('error')} for r in records if r.get('error') or r.get('result',{}).get('error')]
 save(OUT/'completed.json',dict(status='complete' if len(records)==2*sum(len(r['turns']) for r in rows) and not changed and not errors and not app_errors else 'completed_with_issues',timestamp=datetime.now(timezone.utc).isoformat(),records=len(records),changed_files=changed,process_errors=errors,application_errors=app_errors))
if __name__=='__main__':
 if len(sys.argv)==3:worker(*sys.argv[1:])
 else:main()
