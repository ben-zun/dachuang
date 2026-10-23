"""Local BM25 + FAISS, rank fusion, one remote rerank, bounded context recovery."""
from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import time
import shutil
import numpy as np
from scipy import sparse
from backend.rag.v21.common import ROOT, read_jsonl, tokens, write_jsonl
from backend.rag.v21.admission import AdmissionGate,screen
from backend.rag.v21.services import EmbeddingClient, RAGServiceError, model_call
from backend.rag.v21.build import BUILD, LIBRARY, save

CONFIG = ROOT/'configs/core_v21.json'
TERMS = ROOT/'configs/core_terms.json'


def term_query(query):
    terms=json.loads(TERMS.read_text())
    additions=[english for chinese,english in terms.items() if chinese in query]
    return query+(' '+ ' '.join(dict.fromkeys(additions)) if additions else '')


def tokenize(text):
    # Keep scientific identifiers, also expose punctuation-separated components.
    words=re.findall(r'[a-z][a-z0-9_+.-]*|[\u4e00-\u9fff]',text.lower())
    result=[]
    for word in words:
        result.append(word)
        if len(word)>5 and word.endswith('s') and not word.endswith(('ss','ics')):result.append(word[:-1])
        if re.search(r'[+_.-]',word):result.extend(w for w in re.split(r'[+_.-]',word) if len(w)>1)
    return result


def lexical_build(units, path):
    vocab={};counts=[];lengths=[]
    for u in units:
        c=Counter(tokenize(u['embedding_text']));counts.append(c);lengths.append(sum(c.values()))
        for word in c:
            if word not in vocab:vocab[word]=len(vocab)
    df=Counter(w for c in counts for w in c)
    avg=max(1.,float(np.mean(lengths)));n=len(units);rows=[];cols=[];values=[]
    for i,c in enumerate(counts):
        for w,f in c.items():
            idf=np.log(1+(n-df[w]+.5)/(df[w]+.5))
            value=idf*f*2.5/(f+1.5*(.25+.75*lengths[i]/avg))
            rows.append(i);cols.append(vocab[w]);values.append(value)
    matrix=sparse.csc_matrix((np.asarray(values,dtype=np.float32),(rows,cols)),shape=(n,len(vocab)))
    sparse.save_npz(path/'bm25.npz',matrix)
    save(path/'vocabulary.json',vocab)


def build_index():
    import faiss
    config=json.loads(CONFIG.read_text());units=read_jsonl(BUILD/'units.jsonl')
    if not units:raise ValueError('No retrieval units')
    content=(BUILD/'units.jsonl').read_bytes()
    fingerprint=hashlib.sha256(content+json.dumps(config,sort_keys=True).encode()).hexdigest()
    version='v21-'+fingerprint[:16];path=BUILD/'indexes'/version
    path.mkdir(parents=True,exist_ok=True)
    write_jsonl(path/'units.jsonl',units)
    (path/'documents').mkdir(exist_ok=True)
    for doc_id in {u['doc_id'] for u in units}:
        shutil.copyfile(BUILD/'documents'/f'{doc_id}.json',path/'documents'/f'{doc_id}.json')
    lexical_build(units,path)
    # Checkpoint batches: interruption reuses model/dimension/text keyed embedding cache.
    embedder=EmbeddingClient(config)
    index=faiss.IndexFlatIP(config['dimension'])
    for start in range(0,len(units),160):
        values=embedder.embed_documents([u['embedding_text'] for u in units[start:start+160]])
        index.add(np.asarray(values,dtype=np.float32))
        print('embedding',min(start+160,len(units)),len(units),flush=True)
    faiss.write_index(index,str(path/'dense.faiss'))
    hashes={str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file() and p.name!='manifest.json'}
    save(path/'manifest.json',{'version':version,'schema':'core-v21.1','unit_count':len(units),
        'document_count':len({u['doc_id'] for u in units}),'source_count':len({u['source_id'] for u in units}),
        'config':config,'hashes':hashes,'built_at':time.time(),'published':False})
    save(BUILD/'candidate.json',{'version':version})
    print(version,flush=True)
    return version


class Retriever:
    def __init__(self,path):
        import faiss
        self.path=Path(path);self.manifest=json.loads((self.path/'manifest.json').read_text())
        for name,digest in self.manifest['hashes'].items():
            if hashlib.sha256((self.path/name).read_bytes()).hexdigest()!=digest:raise RAGServiceError('Core index checksum mismatch')
        self.config=self.manifest['config'];self.version=self.manifest['version']
        self.chunks=read_jsonl(self.path/'units.jsonl');self.index=faiss.read_index(str(self.path/'dense.faiss'))
        self.admission=AdmissionGate(self.version);self.block_grades={}
        for doc_id in {u['doc_id'] for u in self.chunks}:
            document=json.loads((self.path/'documents'/f'{doc_id}.json').read_text())
            for b in document['blocks']:self.block_grades[b['block_id']]=self.admission.grade(b)
        for u in self.chunks:
            record=self.block_grades[u['block_id']]
            u.update(admission_grade=record['grade'],admission_reasons=record['reasons'])
        self.allowed={i for i,u in enumerate(self.chunks) if u['admission_grade']!='quarantine'}
        if self.index.ntotal!=len(self.chunks):raise RAGServiceError('Core vector count mismatch')
        self.lexical=sparse.load_npz(self.path/'bm25.npz');self.vocab=json.loads((self.path/'vocabulary.json').read_text())
        self.embedder=EmbeddingClient({**self.config,'request_timeout':20,'request_attempts':1})

    def retrieve(self,question,mode=None,queries=None,k=20):
        started=time.perf_counter();expanded=term_query(question);n=min(self.config['candidate_k'],len(self.chunks))
        terms=Counter(tokenize(expanded));cols=[self.vocab[w] for w in terms if w in self.vocab]
        scores=np.asarray(self.lexical[:,cols].sum(axis=1)).ravel() if cols else np.zeros(len(self.chunks))
        lexical=[int(i) for i in np.argsort(-scores)[:n] if scores[i]>0 and int(i) in self.allowed]
        dense=[];warnings=[]
        if mode!='bm25':
            try:
                vector=self.embedder.embed_query(expanded)
                _,indices=self.index.search(np.asarray([vector],dtype=np.float32),n)
                dense=[int(i) for i in indices[0] if i>=0 and int(i) in self.allowed]
            except RAGServiceError as exc:warnings.append({'stage':'dense','status':'error','message':str(exc)})
        if mode=='dense':lexical=[]
        fused={}
        for branch in (lexical,dense):
            for rank,i in enumerate(branch,1):fused[i]=fused.get(i,0)+1/(60+rank)
        ranking=sorted(fused.items(),key=lambda x:x[1],reverse=True)[:max(k,self.config['rerank_k'])]
        before=[self.chunks[i]['chunk_id'] for i,_ in ranking]
        reranked=False
        if ranking and mode not in ('bm25','dense','hybrid_no_rerank'):
            try:
                output=model_call('services/rerank/text-rerank/text-rerank',
                    {'model':self.config['rerank_model'],'input':{'query':expanded,'documents':[self.chunks[i]['embedding_text'] for i,_ in ranking]},
                     'parameters':{'top_n':len(ranking)}},timeout=(5,25),attempts=1)
                results=output['results']
                if len({r['index'] for r in results})!=len(ranking):raise RAGServiceError('rerank result count mismatch')
                ranking=[(ranking[r['index']][0],float(r['relevance_score'])) for r in results]
                reranked=True
            except (RAGServiceError,KeyError,IndexError,TypeError,ValueError) as exc:warnings.append({'stage':'rerank','status':'error','message':type(exc).__name__})
        return ranking[:k],{'index_version':self.version,'mode':mode or 'hybrid_rerank','queries':[question,expanded],
            'admission_version':self.admission.version,'quarantined_units':len(self.chunks)-len(self.allowed),'dense_candidates':len(dense),'bm25_candidates':len(lexical),'before_rerank':before,
            'reranked':reranked,'warnings':warnings,'latency_seconds':round(time.perf_counter()-started,3)}

    def context(self,ranking,meta,budget=None):
        budget=budget or self.config['context_tokens'];used=0;seen=set();sources=[];docs={}
        cutoff=self.config['min_rerank_score'] if meta['reranked'] else 0
        for index,score in ranking:
            if meta['reranked'] and score<cutoff:continue
            u=self.chunks[index]
            if (u['block_id'],u['text']) in seen:continue
            path=self.path/'documents'/f"{u['doc_id']}.json"
            if u['doc_id'] not in docs:docs[u['doc_id']]=json.loads(path.read_text())
            blocks=docs[u['doc_id']]['blocks'];byid={b['block_id']:b for b in blocks}
            primary=byid[u['block_id']]
            grade=lambda b:self.admission.grade(b) if hasattr(self,'admission') else {'grade':screen(b)[0],'reasons':screen(b)[1]}
            if grade(primary)['grade']=='quarantine':continue
            selected=[primary]
            for ident in primary['context_candidates']:
                b=byid[ident]
                if grade(b)['grade']=='quarantine' or b['parse_quality'] in ('uncertain','failed') or (b['block_id'],b['text']) in seen:continue
                if tokens(b['text'])>700:continue
                selected.append(b)
            selected.sort(key=lambda b:b['ordinal'])
            # A split long block must not be silently expanded beyond the budget.
            text='\n\n'.join(u['text'] if b['block_id']==u['block_id'] else b['text'] for b in selected)
            cost=tokens(text)
            if cost>budget-used:
                text=u['text'];selected=[primary];cost=tokens(text)
            if cost>budget-used:continue
            seen.update((b['block_id'],u['text'] if b['block_id']==u['block_id'] else b['text']) for b in selected);used+=cost
            pages=sorted({p['page'] for b in selected for p in b.get('provenance',[])})
            url=u['source_url']
            if pages and u['original_file'].endswith('.pdf'):url=url.split('#')[0]+f'#page={pages[0]}'
            elif primary.get('anchor'):url=url.split('#')[0]+'#'+primary['anchor']
            block_admission=[{'block_id':b['block_id'],**grade(b)} for b in selected]
            admission_grade='fact-grade' if all(b['grade']=='fact-grade' for b in block_admission) else 'background-grade'
            fact_texts=[u['text'] if b['block_id']==u['block_id'] else b['text'] for b in selected if grade(b)['grade']=='fact-grade']
            number=len(sources)+1
            sources.append({**u,'text':text,'number':number,'id':number,'url':url,'source_url':url,
                'section':' / '.join(u['section_path']),'score':float(score),'chunk_ids':[u['chunk_id']],
                'context_block_ids':[b['block_id'] for b in selected],'pages':pages,
                'support_status':'partial','admission_grade':admission_grade,'block_admission':block_admission,'fact_texts':fact_texts,'permission':'fact' if admission_grade=='fact-grade' else 'background'})
            if used>=budget:break
        return sources,{'context_tokens':used,'support_status':'partial' if sources else 'none',
            'note':'Relevance is not answer sufficiency; precise claims require direct support.'}

    def answer(self,question,client,active_material=None):
        from backend.agent.orchestrator import answer_question
        return answer_question(question,client,active_material=active_material)


@lru_cache(maxsize=1)
def _load(version):return Retriever(BUILD/'indexes'/version)


def get_retriever():
    pointer=BUILD/'current.json'
    if not pointer.exists():raise RAGServiceError('Core V2.1 索引尚未发布')
    return _load(json.loads(pointer.read_text())['version'])


def search(question,query=None,task_id=None):
    try:
        pipeline=get_retriever();ranking,meta=pipeline.retrieve(query or question,k=24)
        sources,assembly=pipeline.context(ranking,meta)
        for source in sources:source['task_id']=task_id
        return {'status':'ok','sources':sources,'retrieved_chunks':[{**pipeline.chunks[i],'score':float(score)} for i,score in ranking],
                'papers':[],'retrieval':{**meta,**assembly,'task_id':task_id,'model_calls':0,'route':'core',
                'lookup_status':'success' if sources else 'no_strong_evidence'}}
    except (RAGServiceError,OSError,ValueError) as exc:
        return {'status':'error','sources':[],'retrieved_chunks':[],'papers':[],
                'message':str(exc),'retrieval':{'lookup_status':'error','support_status':'none','task_id':task_id,'model_calls':0}}


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['index','publish']);p.add_argument('--version')
    args=p.parse_args()
    if args.stage=='index':build_index();return
    version=args.version or json.loads((BUILD/'candidate.json').read_text())['version']
    audit=json.loads((BUILD/'reports/retrieval_audit.json').read_text())
    qa=json.loads((BUILD/'reports/parsing_qa.json').read_text())
    if audit.get('index_version')!=version or not audit.get('passed') or not qa.get('passed'):
        raise ValueError('Matching retrieval and parsing QA required before publication')
    checked=Retriever(BUILD/'indexes'/version)
    if qa.get('units_sha256')!=checked.manifest['hashes']['units.jsonl']:
        raise ValueError('Parsing QA belongs to different retrieval units')
    save(BUILD/'current.json',{'version':version,'published_at':time.time()})
    print('published',version)


if __name__=='__main__':main()
