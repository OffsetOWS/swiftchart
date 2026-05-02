import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_NAME", "SwiftChart")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/swiftchart.db")
os.environ.setdefault("FRONTEND_ORIGINS", "*")

from app.main import app  # noqa: E402
