"""Single website answering entry point: Core RAG V2.1."""
import os

def answer_question(*args, **kwargs):
    if os.getenv('AGENT_RUNTIME', 'v21') == 'v2':
        from backend.agent.runtime_v2 import answer_question as run
    else:
        from backend.agent.v21 import answer_question as run
    return run(*args, **kwargs)
