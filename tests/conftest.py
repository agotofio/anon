import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test.db"
os.environ["BOT_TOKEN"] = "123:TEST"
os.environ["ADMIN_LOGIN"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret"
os.environ["MEDIA_CACHE_DIR"] = f"{_tmp}/media"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["FLOOD_LIMIT"] = "1000"
