# 数据准备

## Core知识库

当前网站使用 `v3b-7e3555639d5fe6e7`，31,292个检索单元。来源目录见 `data/source_catalog.json`，索引配置见 `data/core_index_info.json`。

完整运行入口 `backend.api.bkc` 在启动时加载以下目录，必须先准备数据：

```text
data/core_v3b/indexes/v3b-7e3555639d5fe6e7/
  manifest.json
  dense.faiss
  bm25.npz
  vocabulary.json
  units.jsonl
  span_reviews.json
  documents/*.json
```

现有部署可以将该目录复制到新环境。公开仓库只提供来源目录、解析/检索代码和配置，不附带第三方全文及全文派生索引。没有这些数据时，完整BKC入口不能直接启动。各来源的使用条件以原文许可为准。

`backend/rag/v21/build.py` 保存基础解析和单元构建代码，输入为 `data/core_library/catalog.json`、`files_manifest.csv` 及原始文件；PDF解析还需安装Docling。该基础构建器不等于完整V3B扩充与人工准入过程，不能用它单独重建历史索引。

## Materials Project

网站使用2026-09-28官方summary快照，共280,159条，保留常用性质字段，SQLite约459 MiB。下载与构建：

```bash
mkdir -p data/mp_snapshot
curl -fL 'https://materialsproject-build.s3.amazonaws.com/collections/summary/version=2026-09-28/part-00000-2cc5f2cc-2a79-4051-b013-2f9dec6a302e-c000.zstd.parquet' -o data/mp_snapshot/summary.parquet
pip install pyarrow
python deploy/build_mp_snapshot.py --source data/mp_snapshot/summary.parquet --output data/mp_snapshot/materials.sqlite --version 2026-09-28 --source-url 'https://materialsproject-build.s3.amazonaws.com/collections/summary/version=2026-09-28/part-00000-2cc5f2cc-2a79-4051-b013-2f9dec6a302e-c000.zstd.parquet'
```

保留下载文件完成校验后，可删除Parquet以节省空间。`deploy/audit_mp_snapshot.py --help`提供逐行核对参数。`.env`默认读取上述SQLite；使用在线API时设 `MP_DATA_BACKEND=api` 并填写 `MP_API_KEY`。

快照原始文件SHA256：`669e5a3bdf1644633f8c771a98b434bf449451a9aa6ce2556d0901ba1dff235b`。快照包含不同许可的数据，许可信息在构建结果中保留。
