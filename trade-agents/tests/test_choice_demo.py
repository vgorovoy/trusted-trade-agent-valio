"""The standalone synthetic UI cannot inherit an enabled cloud review hook."""
import os
import subprocess
import sys


def test_choice_demo_disables_all_model_paths():
    script = '''
import os
import uvicorn

def inspect(app, **kwargs):
    assert os.environ["TTA_LLM_PROVIDER"] == "offline"
    assert os.environ["TTA_VALIO_REVIEW"] == "off"
    from web.server import overview
    data = overview()
    assert data["orders"] == []
    assert [o["total_eur"] for o in data["choices"][0]["options"]] == [270, 298]

uvicorn.run = inspect
from scripts.serve_choice_demo import main
main()
'''
    env = {**os.environ, "TTA_VALIO_REVIEW": "async", "TTA_VALIO_LLM_PROVIDER": "gemini"}
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
