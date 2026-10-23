import re
import os
from fastapi import FastAPI, HTTPException
from openai import OpenAIError, NotFoundError
from mp_api.client.core.client import MPRestError
from requests.exceptions import RequestException
from pydantic import BaseModel, Field, field_validator
from typing import Literal

from backend.rag.v21.services import RAGServiceError
from backend.rag.v21.retriever import get_retriever as get_pipeline

from backend.model.client import LLMClient
from backend.agent.orchestrator import answer_question
from backend.api.materials import router, detail
from backend.materials.explorer import result_set

app = FastAPI(title="材料物理智能助手")
client = LLMClient()
app.include_router(router)


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10000)


class ChatRequest(BaseModel):
    knowledge_mode: Literal["auto", "enhanced"] = "auto"
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    history: list[ConversationMessage] = Field(default_factory=list, max_length=10)

    @field_validator("history")
    @classmethod
    def check_history(cls, value):
        if sum(len(m.content) for m in value) > 40000:
            raise ValueError("对话历史过长，请清除对话")
        return value

    active_material_id: str | None = None
    active_results_id: str | None = None
    message: str = Field(min_length=1, max_length=10000)

    @field_validator("message")
    @classmethod
    def check_message(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请输入问题")
        # 同一带隙量的显式互斥条件在调用模型前拒绝；其余条件仍由工具校验。
        bounds = re.search(r"(?:带隙|band_gap)\s*(?:大于|>)\s*(\d+(?:\.\d+)?)\s*(?:eV)?\s*(?:且|并且|同时)\s*(?:小于|<)\s*(\d+(?:\.\d+)?)\s*(?:eV)?", value, re.I)
        if bounds and float(bounds[1]) >= float(bounds[2]):
            raise ValueError("带隙上下限互相矛盾，请调整筛选条件")
        return value


def _run_chat(request: ChatRequest, emit=None, session_state=None):
    mode_options = {"knowledge_mode": request.knowledge_mode} if os.getenv('AGENT_RUNTIME', 'v21') == 'v2' else {}
    try:
        if request.active_results_id:
            if request.active_material_id:
                raise ValueError("请只选择单个材料或一个筛选结果集")
            return answer_question(request.message, client, history=[m.model_dump() for m in request.history], active_results=result_set(request.active_results_id), emit=emit, **mode_options, **({"session_state":session_state} if session_state is not None else {}))
        active = detail(request.active_material_id)["data"] if request.active_material_id else None
        result = answer_question(request.message, client, active_material=active, history=[m.model_dump() for m in request.history], emit=emit, **mode_options, **({"session_state":session_state} if session_state is not None else {}))
    except RAGServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (MPRestError, RequestException) as exc:
        raise HTTPException(status_code=502, detail="材料数据服务暂时不可用，请稍后重试。") from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=503, detail="配置的模型当前不存在或未启用，请检查百炼模型和工作空间配置。") from exc
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail="模型调用失败，请稍后重试。") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail="回答生成失败，请稍后重试。") from exc
    return result


def run_chat(request: ChatRequest, emit=None):
    if os.getenv('AGENT_RUNTIME', 'v21')!='v2':return _run_chat(request,emit)
    from backend.agent.session_state import open_session
    try:
        with open_session(request.session_id) as (sid,state):
            result=_run_chat(request,emit,session_state=state)
            result['session_id']=sid
            return result
    except ValueError as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc


@app.post("/knowledge")
def knowledge(request: ChatRequest):
    try:
        return get_pipeline().answer(request.message, client)
    except RAGServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=503, detail="配置的模型当前不存在或未启用，请检查百炼模型和工作空间配置。") from exc
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail="模型调用失败，请稍后重试。") from exc


@app.post("/chat")
def chat(request: ChatRequest):
    return run_chat(request)


@app.post("/chat/stream")
def chat_stream(request: ChatRequest):
    import json
    import queue
    import threading
    import time
    from fastapi.responses import StreamingResponse
    from backend.paths import ROOT
    events=queue.Queue(maxsize=128); cancelled=threading.Event()
    started=time.perf_counter(); first=None
    def emit(event,data):
        while not cancelled.is_set():
            try:events.put((event,data),timeout=.2);return
            except queue.Full:continue
        raise GeneratorExit()
    def work():
        try:
            result=run_chat(request,emit)
            # Legacy runtimes without model streaming still deliver a buffered answer.
            if result.get('response_mode')!='general' and not result.get('streamed'):
                emit('status',{'message':'答案已整理，正在显示…'})
                text=result['answer']
                for i in range(0,len(text),256):emit('delta',{'text':text[i:i+256]})
                result['response_mode']='verified'
                result.setdefault('metrics',{}).update(delivery='validated_buffer')
            if result.get('streamed'):
                result.setdefault('metrics',{}).update(delivery='model_stream_then_validate')
            emit('sources',{'sources':result.get('sources',[]),'database_sources':result.get('database_sources',[])})
            emit('done',result)
        except GeneratorExit:pass
        except Exception as exc:
            message=exc.detail if isinstance(exc,HTTPException) else '本次回答未完成，请重试。'
            emit('error',{'message':message,'incomplete':True})
    def generate():
        nonlocal first
        thread=threading.Thread(target=work,daemon=True);thread.start()
        try:
            while True:
                try:event,data=events.get(timeout=10)
                except queue.Empty:
                    yield ': keepalive\n\n';continue
                if event=='delta' and first is None:first=time.perf_counter()-started
                if event=='done':
                    data['metrics'].update(ttft_seconds=round(first,3) if first is not None else None,
                        time_to_final_seconds=round(time.perf_counter()-started,3))
                    with (ROOT/'logs/stream_calls.jsonl').open('a') as f:
                        f.write(json.dumps({'question':request.message,'response_mode':data['response_mode'],**data['metrics']},ensure_ascii=False)+'\n')
                yield 'event: '+event+'\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n'
                if event in ('done','error'):break
        finally:cancelled.set()
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@app.get("/health")
def health():
    if os.getenv('AGENT_RUNTIME', 'v21') == 'v2':
        from backend.agent.runtime_v2 import RuntimeConfig
        from dataclasses import asdict
        config = asdict(RuntimeConfig.from_env())
        config.pop('trace_dir')
        return {'status':'ok','architecture':'Materials v2 Beta','config':config,
                'capabilities':['direct'] + (['search_materials','calculate'] + (['search_core_knowledge'] if config['core'] else []) if config['tools'] else [])}
    from backend.rag.v21.build import BUILD
    import json
    pointer=BUILD/"current.json"
    return {"status":"ok","architecture":"Core RAG V2.1","core_index":json.loads(pointer.read_text())["version"] if pointer.exists() else None}
