"""Frozen V3B A retrieval path, packaged for the integrated runtime.

No experiment runners, query expansion, translation or alternative retrieval lanes.
"""
from collections import Counter
import hashlib,json,time
from pathlib import Path
import numpy as np
from scipy import sparse
import faiss
from backend.rag.v21.common import read_jsonl,tokens
from backend.rag.v21.admission import AdmissionGate
from backend.rag.v21.retriever import Retriever,term_query,tokenize
from backend.rag.v21.services import EmbeddingClient,model_call
from backend.rag.claim_policy_v3 import ClaimPolicy

class CandidateRetriever(Retriever):
    def __init__(self,path):
        self.path=Path(path);self.manifest=json.loads((self.path/'manifest.json').read_text())
        for name,digest in self.manifest['hashes'].items():
            if hashlib.sha256((self.path/name).read_bytes()).hexdigest()!=digest:raise ValueError('Candidate checksum mismatch: '+name)
        self.config=self.manifest['config'];self.version=self.manifest['version']
        self.chunks=read_jsonl(self.path/'units.jsonl');self.index=faiss.read_index(str(self.path/'dense.faiss'))
        reviews=json.loads((self.path/'span_reviews.json').read_text()) if (self.path/'span_reviews.json').exists() else {}
        self.admission=ClaimPolicy(AdmissionGate('v21-83f2b8a228df7e57'),reviews)
        self.block_grades={}
        for p in (self.path/'documents').glob('*.json'):
            doc=json.loads(p.read_text())
            for b in doc['blocks']:self.block_grades[b['block_id']]=self.admission.grade(b)
        for u in self.chunks:
            g=self.block_grades[u['block_id']];u.update(admission_grade=g['grade'],claim_grade=g['claim_grade'],admission_reasons=g['reasons'])
        self.allowed={i for i,u in enumerate(self.chunks) if self.block_grades[u['block_id']]['retrieval_allowed']}
        self.lexical=sparse.load_npz(self.path/'bm25.npz');self.vocab=json.loads((self.path/'vocabulary.json').read_text())
        self.embedder=EmbeddingClient({**self.config,'request_timeout':20,'request_attempts':1})

def retrieve(r,query,variant='A',n=None,rerank_n=None):
    if variant != 'A':raise ValueError('Integrated runtime supports only frozen Raw A retrieval')
    start=time.perf_counter();n=n or r.config['candidate_k'];rerank_n=rerank_n or r.config['rerank_k']
    original=term_query(query);queries=[original];translation=None
    branches=[];warnings=[]
    for text in dict.fromkeys(queries):
        cols=[r.vocab[w] for w in Counter(tokenize(text)) if w in r.vocab]
        scores=np.asarray(r.lexical[:,cols].sum(axis=1)).ravel() if cols else np.zeros(len(r.chunks))
        lex=[int(i) for i in np.argsort(-scores)[:n] if scores[i]>0 and int(i) in r.allowed]
        try:
            vector=r.embedder.embed_query(text);ss,ii=r.index.search(np.asarray([vector],dtype=np.float32),n)
            dense=[int(i) for i in ii[0] if i>=0 and int(i) in r.allowed]
        except Exception as exc:dense=[];warnings.append(dict(stage='dense',error=type(exc).__name__))
        branches.extend([dict(type='bm25',query=text,indices=lex),dict(type='dense',query=text,indices=dense)])
    fused={}
    for b in branches:
        for rank,i in enumerate(b['indices'],1):fused[i]=fused.get(i,0)+1/(60+rank)
    before=sorted(fused,key=lambda i:fused[i],reverse=True)[:rerank_n]
    rank_query='\n'.join(queries);ranked=[];reranked=False
    if before:
        try:
            out=model_call('services/rerank/text-rerank/text-rerank',dict(model=r.config['rerank_model'],input=dict(query=rank_query,documents=[r.chunks[i]['embedding_text'] for i in before]),parameters=dict(top_n=len(before))),timeout=(5,25),attempts=1)
            results=out['results']
            if {x['index'] for x in results}!=set(range(len(before))):raise ValueError('Invalid rerank index set')
            ranked=[(before[x['index']],float(x['relevance_score'])) for x in results];reranked=True
        except Exception as exc:warnings.append(dict(stage='rerank',error=type(exc).__name__));ranked=[(i,fused[i]) for i in before]
    meta=dict(reranked=reranked,index_version=r.version,queries=queries,warnings=warnings,
        before_rerank=[r.chunks[i]['chunk_id'] for i in before],dense_candidates=sum(len(b['indices']) for b in branches if b['type']=='dense'),bm25_candidates=sum(len(b['indices']) for b in branches if b['type']=='bm25'))
    sources,assembly=r.context(ranked[:24],meta)
    return dict(status='success',query=query,variant=variant,translation=translation,branches=branches,
        before=before,ranking=[dict(index=i,score=s) for i,s in ranked[:24]],sources=sources,metadata={**meta,**assembly},seconds=time.perf_counter()-start,
        rerank_document_tokens=sum(tokens(r.chunks[i]['embedding_text']) for i in before))
