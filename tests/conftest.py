import pytest
from fastapi.testclient import TestClient

from mock_api.app import app
from pipeline.config import load_config


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def api_client():
    """실제 서버를 띄우지 않고 Mock API를 httpx 클라이언트처럼 호출."""
    return TestClient(app)


@pytest.fixture()
def mock_env(monkeypatch):
    monkeypatch.setenv("MOCK_API_BASE_URL", "http://testserver")


@pytest.fixture(scope="session")
def pg_url(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    srv = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    yield srv.get_uri()
