"""Local source-anchored regression; does not use public benchmark answers."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from backend.rag.v21.common import ROOT, read_jsonl, write_jsonl, tokens
from backend.rag.v21.build import BUILD, LIBRARY, save, sha


def parsing_qa():
    docs=[json.loads(p.read_text()) for p in (BUILD/'documents').glob('*.json') if not p.name.endswith('.docling.json')]
    ids=set();errors=[];quality=Counter();pdf_missing_pages=0
    for d in docs:
        if sha((LIBRARY/d['path']).read_bytes())!=d['source_sha256']:errors.append('checksum:'+d['doc_id'])
        for b in d['blocks']:
            if b['block_id'] in ids:errors.append('duplicate id')
            ids.add(b['block_id']);quality[b['parse_quality']]+=1
            if not b['locator'] or not b['section_path']:errors.append('missing provenance')
        if d['format']=='pdf':
            covered={p['page'] for b in d['blocks'] for p in b.get('provenance',[])}
            pdf_missing_pages+=max(0,int(d.get('pages') or 0)-len(covered))
    review_path=BUILD/'reports/manual_parsing_review.json'
    review=json.loads(review_path.read_text()) if review_path.exists() else {}
    result={'passed':not errors and review.get('passed',False),'documents':len(docs),
        'source_count':len({d['source_id'] for d in docs}),'blocks':len(ids),'quality_counts':dict(quality),
        'units_sha256':sha((BUILD/'units.jsonl').read_bytes()) if (BUILD/'units.jsonl').exists() else None,
        'errors':errors,'pdf_pages_without_text_blocks':pdf_missing_pages,
        'manual_review':review,'limitations':'Automated screens and representative review only. Unreviewed formula/table content is not verified.'}
    save(BUILD/'reports/parsing_qa.json',result);print(json.dumps(result,ensure_ascii=False))


def create_cases():
    units=read_jsonl(BUILD/'units.jsonl');overrides=json.loads((ROOT/'evaluation/core_v21_gold.json').read_text());topics=json.loads((ROOT/'evaluation/core_v21_topics.json').read_text());cases=[];reviews=[]
    for index,(ident,zh,en,sids,pattern) in enumerate(topics):
        matches=[u for u in units if u['source_id'] in sids and re.search(pattern,u['text'],re.I|re.S)]
        if not matches:raise ValueError('No gold passage for '+ident)
        # Prefer intact explanatory paragraphs over very short navigation/API signatures.
        matches.sort(key=lambda u:(u['kind'] not in ('paragraph','list'),not 120<=len(u['text'])<=2500,len(u['text'])))
        gold=next((u for u in units if u['block_id']==overrides.get(ident)),matches[0])
        equivalent=[u for u in units if u['doc_id']==gold['doc_id'] and (u['block_id']==gold['block_id'] or re.search(pattern,u['text'],re.I|re.S))]
        reviews.append({'topic':ident,'block_id':gold['block_id'],'source_id':gold['source_id'],'title':gold['title'],'text':gold['text'],
            'locator':gold['locator'],'pages':gold['provenance'],'question':zh})
        for lang,question in [('zh',zh),('en',en)]:
            cases.append({'case_id':ident+'-'+lang,'topic_group':ident,'question':question,'split':'test' if index%4==3 else 'development',
                'gold_doc_ids':sorted({u['doc_id'] for u in equivalent}),'gold_block_ids':sorted({u['block_id'] for u in equivalent}),
                'gold_quotes':[gold['text']],'expected_capabilities':['search_knowledge'],'answerable_in_core':'partial',
                'note':'Gold spans are one known supporting passage group; alternative correct passages may exist.'})
    write_jsonl(ROOT/'evaluation/core_v21_retrieval.jsonl',cases)
    save(BUILD/'reports/gold_passage_review.json',reviews)
    print('created',len(cases),'cases,',len(topics),'topic groups')


def retrieval_audit(mode='hybrid_rerank'):
    from backend.rag.v21.retriever import Retriever
    version=json.loads((BUILD/'candidate.json').read_text())['version'];r=Retriever(BUILD/'indexes'/version)
    rows=[]
    for case in read_jsonl(ROOT/'evaluation/core_v21_retrieval.jsonl'):
        ranking,meta=r.retrieve(case['question'],mode=mode,k=24)
        found=[r.chunks[i] for i,s in ranking]
        ranks=[n for n,u in enumerate(found,1) if u['block_id'] in case['gold_block_ids']]
        rows.append({'case_id':case['case_id'],'split':case['split'],'gold_rank':min(ranks) if ranks else None,
            'doc_recall_at_20':any(u['doc_id'] in case['gold_doc_ids'] for u in found[:20]),
            'span_recall_at_5':any(n<=5 for n in ranks),'span_recall_at_20':any(n<=20 for n in ranks),
            'warnings':meta['warnings'],'top5':[{'unit_id':u['unit_id'],'title':u['title'],'text':u['text'][:200]} for u in found[:5]]})
        print(case['case_id'],min(ranks) if ranks else None,flush=True)
    def summary(subset):
        return {'count':len(subset),'span_recall_at_5':sum(x['span_recall_at_5'] for x in subset)/len(subset),
            'span_recall_at_20':sum(x['span_recall_at_20'] for x in subset)/len(subset),
            'doc_recall_at_20':sum(x['doc_recall_at_20'] for x in subset)/len(subset)}
    report={'index_version':version,'mode':mode,'all':summary(rows),'development':summary([x for x in rows if x['split']=='development']),
        'test':summary([x for x in rows if x['split']=='test']),'cases':rows,'passed':False,
        'limitations':'Known passage recall is a lower-bound diagnostic, not complete semantic relevance or scientific correctness.'}
    # First release gate is explicit and modest; scientific correctness is evaluated separately.
    report['passed']=report['all']['doc_recall_at_20']>=.75 and report['all']['span_recall_at_20']>=.60 and not any(x['warnings'] for x in rows)
    save(BUILD/'reports'/f'retrieval_audit_{mode}.json',report)
    if mode=='hybrid_rerank':save(BUILD/'reports/retrieval_audit.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False))


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['parsing','cases','retrieval']);p.add_argument('--mode',default='hybrid_rerank');a=p.parse_args()
    if a.stage=='parsing':parsing_qa()
    elif a.stage=='cases':create_cases()
    else:retrieval_audit(a.mode)


if __name__=='__main__':main()
