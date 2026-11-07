import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["FUNDRIX_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "frontend", "data")
if not os.path.exists(os.path.join(DATA, "meta.json")):          # the site is static: browser tests need the generated demo data files
    from pipeline import build
    build.run_demo(DATA)
import pytest
from fastapi.testclient import TestClient
from backend.main import app

@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture()
def H():
    import uuid
    return {"X-Client-Id": "t-" + uuid.uuid4().hex[:8]}
