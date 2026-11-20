"""Run the complete backend and execution-bot suites without exclusions."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "backend"), str(ROOT)]),
       "PYTHONDONTWRITEBYTECODE": "1"}
args = [sys.executable, "-m", "pytest", "backend/tests", "execution_bot/tests", "-q", "-p", "no:cacheprovider"]
raise SystemExit(subprocess.run(args, cwd=ROOT, env=env).returncode)
