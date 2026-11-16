"""Settings: environment overrides, invalid values, secrets."""

from app.config.settings import Settings


def test_defaults_load():
    s = Settings()
    assert s.http_max_retries >= 0 and s.http_timeout > 0
    assert s.first_fy_slot == 2021


def test_valid_overrides(monkeypatch):
    monkeypatch.setenv("BRSR_HTTP_TIMEOUT", "30")
    monkeypatch.setenv("BRSR_COMPANY_FILTER", " titan , INFY,,")
    monkeypatch.setenv("BRSR_HTTP_CACHE_ENABLED", "no")
    s = Settings()
    assert s.http_timeout == 30.0
    assert s.company_filter == ("titan", "INFY")
    assert s.http_cache_enabled is False


def test_invalid_values_fall_back_to_defaults(monkeypatch, capsys):
    monkeypatch.setenv("BRSR_HTTP_TIMEOUT", "abc")
    monkeypatch.setenv("BRSR_HTTP_MAX_RETRIES", "-3")
    monkeypatch.setenv("BRSR_HTTP_CACHE_ENABLED", "maybe")
    s = Settings()
    assert s.http_timeout == 90.0
    assert s.http_max_retries == 4
    assert s.http_cache_enabled is True
    assert "invalid BRSR_HTTP_TIMEOUT" in capsys.readouterr().err


def test_secrets_never_in_settings_dump(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "super-secret")
    monkeypatch.setenv("DATA_GOV_IN_API_KEY", "key-123")
    s = Settings()
    dump = str(s.as_dict())
    assert "super-secret" not in dump and "key-123" not in dump
    assert "super-secret" not in s.db_description
    assert s.datagov_api_key == "key-123"
