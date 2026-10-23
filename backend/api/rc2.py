"""Isolated RC2 adapter. Same index/retriever/frontend; frozen C2.1 policy."""
from pathlib import Path
from uuid import uuid4
from backend.api import main
from backend.agent import core_tool
from backend.agent.runtime_v2 import RuntimeConfig, make_client, run_answer
from evaluation.rag_v3.retrieval_experiment import CandidateRetriever, retrieve
ROOT=Path(__file__).resolve().parents[2]
core_tool.CORE_POLICY=(ROOT/'configs/core_reader_c21.txt').read_text()
retriever=CandidateRetriever(ROOT/'data/core_v3b/indexes/v3b-7e3555639d5fe6e7')
def core_search(query):
    result=retrieve(retriever,query,'A')
    return {'status':'ok','sources':result['sources'],'retrieval_audit':result}
def answer_question(message,client,**kwargs):
    config=RuntimeConfig.from_env();ident='rc2-'+uuid4().hex
    scoped=make_client(config,ident,model='qwen3.7-flash',temperature=0)
    return run_answer(message,scoped,config,request_id=ident,core_search=core_search,**kwargs)
main.answer_question=answer_question
app=main.app
@app.get('/staging')
def staging_info():return {'status':'ok','candidate':'RC2 Calculate v3','model':'qwen3.7-flash','index_version':retriever.version,'core':RuntimeConfig.from_env().core,'reader':'frozen C2.1','promotion':'pending paired48 gate','frontend':'unchanged'}
