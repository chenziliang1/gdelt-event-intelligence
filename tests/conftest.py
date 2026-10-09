"""Make the repo root importable so `import backend...` works under pytest."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "db_scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import pytest


@pytest.fixture(autouse=True)
def _no_remote_llm(monkeypatch):
    """No test may reach a paid LLM API.

    backend/database/pool.py runs load_dotenv() on import, so a developer's .env (provider and
    key) leaked into tests and one planner fallback really called the provider. Empty keys are
    set, not deleted, because load_dotenv never overrides a variable that already exists; with
    no key, build_llm raises before any request is made.
    """
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    for var in ("LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    for var in ("ANTHROPIC_API_KEY", "KIMI_CODE_API_KEY", "MOONSHOT_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(var, "")
