"""Best-known candidate: frozen C2.1, live model stream with final validation."""
from pathlib import Path
from uuid import uuid4
from backend.api import main
from backend.agent import core_tool
from backend.agent.runtime_v2 import RuntimeConfig, make_client, run_answer
from backend.rag.v3b_runtime import CandidateRetriever, retrieve
ROOT=Path(__file__).resolve().parents[2]
core_tool.CORE_POLICY=(ROOT/'configs/core_reader_c21.txt').read_text()
retriever=CandidateRetriever(ROOT/'data/core_v3b/indexes/v3b-7e3555639d5fe6e7')
def core_search(query):
    result=retrieve(retriever,query,'A')
    return {'status':'ok','sources':result['sources'],'retrieval_audit':result}
def answer_question(message,client,**kwargs):
    config=RuntimeConfig.from_env();ident='bkc-r1-'+uuid4().hex
    scoped=make_client(config,ident,model='qwen3.7-flash',temperature=0)
    emit = kwargs.pop('emit', None)
    result = run_answer(message,scoped,config,request_id=ident,core_search=core_search,
                        emit=emit,**kwargs)
    # Stream generation live; the done payload remains the validated final answer.
    result.setdefault('metrics', {})['delivery'] = ('model_stream_then_validate' if result.get('streamed') else 'validated_buffer')
    return result
main.answer_question=answer_question
app=main.app
@app.get('/staging')
def staging_info():return {'status':'ok','candidate':'BKC-R1 integrated','model':'qwen3.7-flash','index_version':retriever.version,'core':RuntimeConfig.from_env().core,'thinking_budget':RuntimeConfig.from_env().thinking_budget,'max_tokens':RuntimeConfig.from_env().max_tokens,'detailed_answers':RuntimeConfig.from_env().detailed_answers,'delivery':'model_stream_then_validate','retrieval_variant':'A','query_expansion':False,'reader':'frozen C2.1','promotion':'integration complete; production promotion pending user trial','frontend':'porcelain-songti'}
