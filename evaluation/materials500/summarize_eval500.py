"""Summarize frozen paired answers, including partial progress; never rewrites answers."""
from pathlib import Path
import json,csv,collections,statistics
R=Path(__file__).resolve().parent;S=R;RUN=S/'run';G=S/'grading'
refs=json.loads((R/'grading_reference.json').read_text());byid={r['qid']:r for r in refs}
overrides=json.loads((R/'review_overrides.json').read_text()) if (R/'review_overrides.json').exists() else {}
def read(p):return json.loads(p.read_text())
def group(r):
 if len(r['turns'])>1:return '多轮连续任务'
 if r['task_type']=='database':return '数据库查询'
 if r['task_type']=='quantitative':return '数值与换算'
 if r['task_type'] in ['recommendation','practical_advice']:return '推荐与实用建议'
 if r['task_type']=='multi_task':return '混合任务'
 return '知识与方法解释'
rows=[]
for p in sorted((G/'ratings').glob('P*.json')):
 d=read(p);q=d['qid'];ref=byid[q];item={'qid':q,'domain':ref['domain'],'type':ref['task_type'],'group':group(ref),'difficulty':ref['difficulty'],'source':'旧200' if int(q[1:])<=200 else '新增300','turns':len(ref['turns'])}
 for a in ['base','bkc']:
  rate={**d['ratings'][a],**overrides.get(q,{}).get(a,{})}
  ans=[read(RUN/a/f'{a}-{q}-t{t["turn"]}.json') for t in ref['turns']]
  item.update({a+'_grade':rate['grade'],a+'_usable':rate['grade'] in ['A','B'],a+'_complete':rate['completion']=='complete',a+'_severe':rate['severe'],a+'_reason':rate['reason'],a+'_error':any(x.get('error') or (x.get('result') or {}).get('error') for x in ans),a+'_llm_calls':sum(x.get('llm_calls',0) for x in ans),a+'_input_tokens':sum(x.get('input_tokens',0) for x in ans),a+'_output_tokens':sum(x.get('output_tokens',0) for x in ans),a+'_latency_seconds':sum(x.get('latency_seconds',0) for x in ans)})
 item['pair']='both_good' if item['base_usable'] and item['bkc_usable'] else ('rescue' if item['bkc_usable'] else ('interference' if item['base_usable'] else 'both_bad'));rows.append(item)
def summary(rs):
 n=len(rs);out={'tasks':n,'pairs':dict(collections.Counter(r['pair'] for r in rs))}
 for a in ['base','bkc']:
  out[a]={'usable':sum(r[a+'_usable'] for r in rs),'A':sum(r[a+'_grade']=='A' for r in rs),'grades':dict(collections.Counter(r[a+'_grade'] for r in rs)),'complete':sum(r[a+'_complete'] for r in rs),'severe':sum(r[a+'_severe'] for r in rs),'tasks_with_api_error':sum(r[a+'_error'] for r in rs),'llm_calls':sum(r[a+'_llm_calls'] for r in rs),'input_tokens':sum(r[a+'_input_tokens'] for r in rs),'output_tokens':sum(r[a+'_output_tokens'] for r in rs)}
 return out
out={'scoring_method':'blinded reference-guided qwen-plus assisted grades with explicitly saved assistant review overrides; not independent expert review','status':'complete' if len(rows)==500 else 'in_progress','graded_tasks':len(rows),'total_tasks':500,'overall':summary(rows),'both_returned_without_api_error':summary([r for r in rows if not r['base_error'] and not r['bkc_error']]),'by_difficulty':{k:summary([r for r in rows if r['difficulty']==k]) for k in ['L1','L2','L3']},'by_group':{k:summary([r for r in rows if r['group']==k]) for k in sorted(set(r['group'] for r in rows))},'by_source':{k:summary([r for r in rows if r['source']==k]) for k in ['旧200','新增300']},'note':'Deliberately product-oriented development distribution; not an independent holdout. No random-population gain claimed.'}
(R/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
if rows:
 with (R/'逐题对比.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
brief=['# 500题Base与最终BKC对比','',f'已评分 {len(rows)}/500；状态：'+out['status'],'','模型辅助评分，未见人工专家审定。全量完成前不将中间比例当最终成绩。','','| 指标 | Base | BKC |','|---|---:|---:|']
for title,key in [('A+B可用','usable'),('A级','A'),('完整完成','complete'),('严重问题标记','severe'),('含接口错误的任务','tasks_with_api_error')]:brief.append(f"| {title} | {out['overall']['base'][key]} | {out['overall']['bkc'][key]} |")
brief+=['','配对：'+json.dumps(out['overall']['pairs'],ensure_ascii=False),'','分难度与任务结果详见summary.json；每题理由见逐题对比.csv。']
(R/'结果进度.md').write_text('\n'.join(brief)+'\n')
print(json.dumps({'status':out['status'],'graded':len(rows),'overall':out['overall']},ensure_ascii=False,indent=2))
