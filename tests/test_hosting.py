import pytest
from fastapi.testclient import TestClient

from bracket22.app import create_app
from bracket22.config import configured_database


def test_hosted_postgres_url_uses_installed_driver(monkeypatch):
    monkeypatch.setenv('DATABASE_URL','postgres://user:password@db/desk')
    assert configured_database()=='postgresql+psycopg://user:password@db/desk'


def test_hosted_app_refuses_missing_access_token(tmp_path,monkeypatch):
    monkeypatch.setenv('BRACKET22_REQUIRE_AUTH','true')
    monkeypatch.delenv('BRACKET22_API_TOKEN',raising=False)
    with pytest.raises(ValueError,match='BRACKET22_API_TOKEN'), TestClient(create_app(f'sqlite:///{tmp_path}/desk.db')):
        pass
