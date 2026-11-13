"""Run all Python tests except the explicitly accepted, unchanged OKX failures."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
known=json.loads((ROOT/"docs/stage1_known_okx_failures.json").read_text())
env={**os.environ,"PYTHONPATH":os.pathsep.join([str(ROOT/"backend"),str(ROOT)]),"PYTHONDONTWRITEBYTECODE":"1"}
args=[sys.executable,"-m","pytest","backend/tests","execution_bot/tests","-q","-p","no:cacheprovider"]
collection=subprocess.run(args+["--collect-only"],cwd=ROOT,env=env,capture_output=True,text=True)
if collection.returncode or not all(node in collection.stdout for node in known):
    raise SystemExit("Test collection/known-failure list changed; review required")
raise SystemExit(subprocess.run(args+["--deselect="+node for node in known],cwd=ROOT,env=env).returncode)
