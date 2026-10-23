from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
import numpy as np
import requests
from dotenv import load_dotenv
from backend.rag.v21.common import ROOT
DATA=ROOT/'data/core_v21'

class RAGServiceError(RuntimeError):pass


def model_call(path, body, timeout=(10,90), attempts=8):
    load_dotenv(ROOT/'.env')
    key=os.getenv('DASHSCOPE_API_KEY');workspace=os.getenv('DASHSCOPE_WORKSPACE_ID')
    if not key or not workspace:raise RAGServiceError('请配置 DASHSCOPE_API_KEY 和 DASHSCOPE_WORKSPACE_ID')
    start=time.perf_counter()
    for attempt in range(attempts):
        try:
            response=requests.post(f'https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1/{path}',
                headers={'Authorization':'Bearer '+key},json=body,timeout=timeout)
            if response.status_code in (429,500,502,503,504) and attempt<attempts-1:
                time.sleep(min(30,max(float(response.headers.get('Retry-After',0) or 0),2**attempt)));continue
            data=response.json()
        except (requests.RequestException,ValueError) as exc:
            if attempt<attempts-1:time.sleep(1+attempt);continue
            raise RAGServiceError('文档检索模型请求失败，请稍后重试') from exc
        log={'timestamp':datetime.now(timezone.utc).isoformat(),'model':body['model'],'usage':data.get('usage'),
             'latency_seconds':round(time.perf_counter()-start,3),'status':response.status_code,'code':data.get('code')}
        with (ROOT/'logs/rag_model_calls.jsonl').open('a') as f:f.write(json.dumps(log)+'\n')
        if response.status_code!=200 or data.get('code'):
            raise RAGServiceError(f"文档检索模型不可用（HTTP {response.status_code}, {data.get('code','unknown')}）")
        return data['output']

class EmbeddingClient:
    def __init__(self,config=None):
        config=config or {'embedding_model':'qwen3.7-text-embedding','dimension':1024}
        self.model=config['embedding_model'];self.dimension=config['dimension']
        self.timeout=config.get('request_timeout',90);self.attempts=config.get('request_attempts',8)
        (DATA/'indexes').mkdir(parents=True,exist_ok=True)
        self.cache=DATA/'indexes/embedding_cache.sqlite'
        with sqlite3.connect(self.cache) as db:db.execute('CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB NOT NULL)')

    def embed_documents(self,texts):
        return self.encode(texts,text_type='document')

    def embed_query(self,query):
        if not isinstance(query,str) or not query.strip():raise ValueError('请输入检索问题')
        return self.encode([query],text_type='query')[0]

    def encode(self,texts,text_type='document'):
        if any(not isinstance(t,str) or not t.strip() for t in texts):raise ValueError('Embedding 输入不能为空')
        keys=[hashlib.sha256(f'{self.model}:{self.dimension}:{text_type}:{t}'.encode()).hexdigest() for t in texts]
        found={}
        with sqlite3.connect(self.cache) as db:
            for key in keys:
                row=db.execute('SELECT vector FROM vectors WHERE key=?',(key,)).fetchone()
                if row:
                    vector=np.frombuffer(row[0],dtype=np.float32).copy()
                    if vector.shape!=(self.dimension,) or not np.isfinite(vector).all() or np.linalg.norm(vector)==0:raise RAGServiceError('缓存向量损坏，请重新建库')
                    found[key]=vector
        missing=list(dict.fromkeys(k for k in keys if k not in found));lookup=dict(zip(keys,texts))
        def fetch(batch):
            output=model_call('services/embeddings/text-embedding/text-embedding',
                {'model':self.model,'input':{'texts':[lookup[k] for k in batch]},
                 'parameters':{'dimension':self.dimension,'text_type':text_type}}, timeout=(5,self.timeout),attempts=self.attempts)
            vectors=sorted(output['embeddings'],key=lambda x:x['text_index'])
            if [v['text_index'] for v in vectors]!=list(range(len(batch))):raise RAGServiceError('向量数量不匹配')
            return batch,vectors
        batches=[missing[start:start+20] for start in range(0,len(missing),20)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            for batch,vectors in pool.map(fetch,batches):
                with sqlite3.connect(self.cache) as db:
                    for key,item in zip(batch,vectors):
                        vector=np.asarray(item['embedding'],dtype=np.float32)
                        if vector.shape!=(self.dimension,) or not np.isfinite(vector).all() or np.linalg.norm(vector)==0:
                            raise RAGServiceError('无效向量')
                        vector/=np.linalg.norm(vector);found[key]=vector
                        db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?)',(key,vector.tobytes()))
        return np.stack([found[k] for k in keys]) if keys else np.empty((0,self.dimension),dtype=np.float32)
