# 500题材料助手评测

- `questions500.jsonl`：500任务、590轮输入。
- `grading_reference.json`：评分参考，与模型输入分开。
- `逐题对比.csv`、`summary.json`、`最终评测报告.md`：历史结果。
- `bank_freeze.json`：历史题库冻结记录，按其中文件名及路径核对。
- 三个Python脚本：历史作答、匿名评分和汇总程序，项目根目录改为相对路径。原实验代码提交见 `docs/experiments.md`。

A/B为可用，C/D为不可用，另记任务完成和严重错误。题目分L1基础174题、L2常用231题、L3综合95题，含85个多轮会话。用户输入使用材料名称、化学式和自然追问。

如需在当前代码上重新运行，先准备运行环境、数据和API Key，依次执行 `run_eval500.py`、`grade_eval500.py`、`summarize_eval500.py`；实际入口参数见脚本。重新作答会产生API费用。新结果写入run/和grading/，不能视为历史版本的精确复现。
