import os
import requests
import streamlit as st

URL = os.getenv('MATERIALS_API_URL', "http://127.0.0.1:8000").rstrip('/')


def request(method, path, **kwargs):
    try:
        response = requests.request(method, URL + path, timeout=180, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError:
        try:
            message = response.json().get("detail", "请求失败，请稍后重试。")
        except ValueError:
            message = "请求失败，请稍后重试。"
        if isinstance(message, list):
            message = "；".join(item.get("msg", "筛选条件无效") for item in message)
        st.error(message.removeprefix("Value error, "))
    except requests.RequestException:
        st.error("无法完成请求，请检查后端服务或稍后重试。")
    return None


def stream_chat(payload):
    """Consume complete SSE records; caller commits history only after done."""
    import json
    with requests.post(URL+'/chat/stream',json=payload,stream=True,timeout=(10,180)) as response:
        if not response.ok:
            try:message=response.json().get('detail','请求失败')
            except ValueError:message='请求失败'
            raise RuntimeError(str(message))
        response.encoding='utf-8';event=None;data=[]
        for line in response.iter_lines(decode_unicode=True,chunk_size=1024):
            if line.startswith('event:'):event=line[6:].strip()
            elif line.startswith('data:'):data.append(line[5:].strip())
            elif not line and data:
                yield event,json.loads('\n'.join(data));event=None;data=[]
