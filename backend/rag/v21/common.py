"""Paths and deterministic serialization for the new Core build only."""
import json
from pathlib import Path
import tiktoken
ROOT=Path(__file__).resolve().parents[3]
ENC=tiktoken.get_encoding('cl100k_base')  # Approximate budget, not Qwen billing tokens.


def tokens(text):return len(ENC.encode(text,disallowed_special=()))


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_jsonl(path,rows):
    import os
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows))
    tmp.replace(path)
