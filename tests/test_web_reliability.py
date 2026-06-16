import json
from types import SimpleNamespace

import pytest

from web.app import AppContext, create_app


class FakeRouter:
    def __init__(self):
        self.fetch_args = None
        self._state = {
            "mode": "auto",
            "manual_provider": None,
            "last_fallback_time": None,
            "last_fallback_reason": None,
        }

    def get_provider_list(self):
        return [
            {
                "name": "foo/[bar] @ test",
                "enabled": True,
                "priority": 1,
                "model": "demo-model",
                "base_url": "https://example.test/v1",
                "task_types": ["chat"],
                "has_api_key": True,
                "api_key_env": "TEST_PROVIDER_KEY",
                "timeout_chat_seconds": 30,
                "timeout_background_seconds": 20,
                "max_retries": 1,
                "cooldown_seconds": 60,
                "max_consecutive_failures": 3,
                "disable_on_quota_exhausted": True,
                "thinking_enabled": False,
                "consecutive_failures": 0,
                "cooldown_remaining": 0,
                "exhausted": False,
                "last_failure_reason": None,
                "last_failure_time": None,
                "last_error_type": None,
            }
        ]

    def get_dashboard_status(self):
        return {"current_model": "demo-model", "current_provider": "foo/[bar] @ test"}

    def get_call_history(self):
        return []

    def test_connection(self, name):
        return {"ok": True, "reply": "OK", "latency_ms": 1, "error": "", "model": "demo-model"}

    def fetch_models_from_api(self, base_url, api_key):
        self.fetch_args = (base_url, api_key)
        return {"ok": True, "models": ["demo-a", "demo-b"], "error": ""}


@pytest.fixture
def web_client():
    router = FakeRouter()
    ctx = AppContext(
        world_manager=SimpleNamespace(get_world=lambda: SimpleNamespace(WORLD_NAME="test")),
        roleplay_bot=SimpleNamespace(),
        client=SimpleNamespace(router=router),
        start_time=0,
    )
    app = create_app(ctx)
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["_csrf_token"] = "csrf-token"
    return client, router


def test_csp_stays_strict_and_static_scripts_are_loaded(web_client):
    client, _router = web_client
    response = client.get("/providers/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert "js/app.js" in html
    assert "js/providers.js" in html
    assert "<script>\n" not in html
    assert "onclick=" not in html
    assert "onchange=" not in html
    assert "onsubmit=" not in html


def test_provider_data_is_json_and_does_not_include_api_key(web_client, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "sk-real-secret-value")
    client, _router = web_client
    html = client.get("/providers/").get_data(as_text=True)

    assert "sk-real-secret-value" not in html
    marker = '<script id="provider-data" type="application/json">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    providers = json.loads(html[start:end])
    assert providers[0]["name"] == "foo/[bar] @ test"
    assert providers[0]["api_key_env"] == "TEST_PROVIDER_KEY"


def test_provider_test_requires_csrf(web_client):
    client, _router = web_client
    response = client.post("/providers/test", data={"name": "foo/[bar] @ test"})
    assert response.status_code == 403


def test_fetch_models_reads_key_from_environment(web_client, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "sk-real-secret-value")
    client, router = web_client
    response = client.post(
        "/providers/fetch-models",
        data={
            "_csrf_token": "csrf-token",
            "name": "foo/[bar] @ test",
            "base_url": "https://example.test/v1",
            "api_key_env": "TEST_PROVIDER_KEY",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["models"] == ["demo-a", "demo-b"]
    assert router.fetch_args == ("https://example.test/v1", "sk-real-secret-value")


def test_health_minimal_without_login(web_client):
    client, _router = web_client
    with client.session_transaction() as sess:
        sess.clear()

    data = client.get("/health").get_json()
    assert data["ok"] is True
    assert data["process_alive"] is True
    assert "active_world" not in data
