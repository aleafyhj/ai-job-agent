import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app


def test_health_and_request_tracing(capsys):
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as client:
        first = client.get("/api/v1/health?secret=do-not-log")
        second = client.get("/api/v1/health")
        assert first.status_code == 200
        assert first.json() == {"status": "ok"}
        UUID(first.headers["X-Request-ID"])
        assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    output = capsys.readouterr().out
    assert "do-not-log" not in output
    records = [json.loads(line) for line in output.splitlines()]
    request_logs = [item for item in records if item["message"] == "request_completed"]
    assert len(request_logs) == 2
    assert request_logs[0]["request_id"] == first.headers["X-Request-ID"]


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("JOB_AGENT_PORT=8001\nJOB_AGENT_APP_NAME=配置测试\n", encoding="utf-8")
    monkeypatch.setenv("JOB_AGENT_PORT", "8123")
    settings = Settings(_env_file=env_file)
    assert settings.port == 8123
    assert settings.app_name == "配置测试"


@pytest.mark.parametrize("values", [{"port": 0}, {"log_level": "INVALID"}, {"host": "0.0.0.0"}])
def test_invalid_configuration_fails_early(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_not_found_and_unexpected_error_are_traceable():
    app = create_app(Settings(_env_file=None))

    @app.get("/test-failure")
    async def fail():
        raise RuntimeError("private-error-detail")

    with TestClient(app) as client:
        for path, expected_status in [("/missing", 404), ("/test-failure", 500)]:
            response = client.get(path)
            assert response.status_code == expected_status
            assert response.json()["request_id"] == response.headers["X-Request-ID"]
            assert "private-error-detail" not in response.text


def test_database_config_hides_credentials_and_rejects_other_backends():
    url = "postgresql+psycopg://candidate:private-password@localhost/test"
    settings = Settings(_env_file=None, database_url=url)
    assert "private-password" not in repr(settings)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, database_url="sqlite:///private-password")
    assert "private-password" not in str(error.value)
