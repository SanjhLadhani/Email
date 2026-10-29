import pytest

import config


class TestRequireEmailCredentials:
    def test_raises_when_address_missing(self, monkeypatch):
        monkeypatch.setattr(config, "EMAIL_ADDRESS", "")
        monkeypatch.setattr(config, "EMAIL_APP_PASSWORD", "secret")
        with pytest.raises(RuntimeError):
            config.require_email_credentials()

    def test_raises_when_password_missing(self, monkeypatch):
        monkeypatch.setattr(config, "EMAIL_ADDRESS", "a@b.com")
        monkeypatch.setattr(config, "EMAIL_APP_PASSWORD", "")
        with pytest.raises(RuntimeError):
            config.require_email_credentials()

    def test_returns_both_when_set(self, monkeypatch):
        monkeypatch.setattr(config, "EMAIL_ADDRESS", "a@b.com")
        monkeypatch.setattr(config, "EMAIL_APP_PASSWORD", "secret")
        assert config.require_email_credentials() == ("a@b.com", "secret")


class TestBackend:
    def test_fields_and_defaults(self):
        b = config.Backend("api", "https://x", "model-a", "key")
        assert b.kind == "api"
        assert b.models == ()

    def test_models_field(self):
        b = config.Backend("api", "https://x", "model-a", "key", ("model-a", "model-b"))
        assert b.models == ("model-a", "model-b")


class TestOllamaUrl:
    def test_refuses_localhost_announcement(self, monkeypatch):
        monkeypatch.setattr(config, "_remote_settings", lambda max_age=60.0: {"url": "http://localhost:11434"})
        assert config.ollama_url() == config._NO_SERVER

    def test_refuses_127_0_0_1(self, monkeypatch):
        monkeypatch.setattr(config, "_remote_settings", lambda max_age=60.0: {"url": "http://127.0.0.1:11434"})
        assert config.ollama_url() == config._NO_SERVER

    def test_accepts_a_real_remote_url(self, monkeypatch):
        monkeypatch.setattr(config, "_remote_settings", lambda max_age=60.0: {"url": "https://abc.trycloudflare.com"})
        assert config.ollama_url() == "https://abc.trycloudflare.com"

    def test_falls_back_to_no_server_when_nothing_set(self, monkeypatch):
        monkeypatch.setattr(config, "_remote_settings", lambda max_age=60.0: {})
        monkeypatch.setattr(config, "_env", lambda key, default="": default)
        assert config.ollama_url() == config._NO_SERVER


class TestRemoteSettingsHostAllowlist:
    def test_rejects_untrusted_host_from_ntfy(self, monkeypatch):
        """Only *.trycloudflare.com announcements are trusted; anything else is ignored."""
        import json as jsonlib

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                event = {"event": "message", "message": jsonlib.dumps({"url": "https://evil.example.com", "ts": 1}), "time": 1}
                return jsonlib.dumps(event).encode()

        monkeypatch.setattr(config, "_env", lambda key, default="": "sometopic" if key == "NTFY_TOPIC" else default)
        monkeypatch.setattr(config.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
        config.refresh()
        assert config._remote_settings() == {}

    def test_accepts_trusted_host_from_ntfy(self, monkeypatch):
        import json as jsonlib

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self):
                event = {"event": "message", "message": jsonlib.dumps({"url": "https://abc.trycloudflare.com", "ts": 1}), "time": 1}
                return jsonlib.dumps(event).encode()

        monkeypatch.setattr(config, "_env", lambda key, default="": "sometopic" if key == "NTFY_TOPIC" else default)
        monkeypatch.setattr(config.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
        config.refresh()
        assert config._remote_settings()["url"] == "https://abc.trycloudflare.com"


class TestBackendSelection:
    def test_backend_tries_order_and_skips_unavailable(self, monkeypatch):
        monkeypatch.setattr(config, "_colab_backend", lambda: None)
        monkeypatch.setattr(config, "_api_backend", lambda: config.Backend("api", "https://x", "m", "k"))
        monkeypatch.setattr(config, "_env", lambda key, default="": "colab,api" if key == "MODEL_ORDER" else default)
        config.refresh()
        found = config.backend(max_age=0)
        assert found.kind == "api"

    def test_backend_none_when_nothing_available(self, monkeypatch):
        monkeypatch.setattr(config, "_colab_backend", lambda: None)
        monkeypatch.setattr(config, "_api_backend", lambda: None)
        config.refresh()
        assert config.backend(max_age=0) is None
