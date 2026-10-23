# 材料物理智能助手

面向材料学习与日常查询的问答网站，支持材料知识解释、数据库查询、数值换算和连续追问。

- 基座模型：Qwen3.7-Flash，通过百炼 API 调用。
- 知识检索：FAISS + BM25 + RRF + reranker。
- 材料数据：Materials Project 官方 summary 本地快照。
- 计算：Python / pymatgen，保存输入、表达式和结果。
- 网站：FastAPI + Streamlit，支持流式回答、资料引用和对话历史。

## 运行

使用 Python 3.11，先按[数据准备说明](docs/data.md)配置知识库索引和材料快照。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
cp .env.example .env
# 填写百炼 API Key
python -m uvicorn backend.api.bkc:app --host 127.0.0.1 --port 8006
```

另开终端，激活同一个环境：

```bash
MATERIALS_API_URL=http://127.0.0.1:8006 python -m streamlit run frontend/app.py --server.port 8507
```

浏览器打开 `http://127.0.0.1:8507`。云端配置见[部署说明](docs/deployment.md)。

## 评测

自建500题材料助手使用评测，同一Qwen3.7-Flash作对照：

| 指标 | Base | 完整系统 |
|---|---:|---:|
| 可用回答（A+B） | 384/500（76.8%） | 462/500（92.4%） |
| A级回答 | 331/500 | 438/500 |
| 任务完成 | 380/500 | 457/500 |

题库含85个多轮任务，共590轮输入，采用Qwen-Plus辅助评分。题目、参考和逐题结果见[evaluation/materials500](evaluation/materials500)，中间实验见[实验记录](docs/experiments.md)。

## 目录

- `backend/`：模型调用、检索、材料查询、计算与会话状态。
- `frontend/`：问答页面、材料探索和历史记录。
- `configs/`：检索、来源准入与阅读策略。
- `data/`：名称映射、资料目录及数据版本信息。
- `deploy/`：快照构建脚本、systemd与Caddy配置。
- `tests/`：查询、路由和页面测试。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试使用临时数据库和页面样例，不调用付费模型。

第三方文档正文、检索索引、MP数据库和用户聊天记录不随代码上传。
