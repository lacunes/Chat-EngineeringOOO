import asyncio
import collections
import time

import pytest
import yaml

import bot.llm_router as llm_router_module
import bot.safe_io as safe_io_module
from bot.llm_router import LLMRouter


def _isolated_router(provider: dict, state: dict | None = None) -> LLMRouter:
    """Build a router without loading or writing project runtime files."""
    router = LLMRouter.__new__(LLMRouter)
    router._notify = None
    router._providers_mtime = 0.0
    router._providers_config = [provider]
    router._providers_by_name = {provider["name"]: provider}
    router._state = {
        "mode": "auto",
        "manual_provider": None,
        "providers": {provider["name"]: state or {}},
    }
    router._call_history = collections.deque(maxlen=20)
    router._check_reload = lambda: None
    router._select_candidates = lambda task_type: [provider["name"]]
    router._notify_admin = lambda text: None
    router._reload_providers = lambda: None
    router._load_and_merge_state = lambda: None
    return router


def test_chat_reports_missing_key_when_every_candidate_is_skipped(monkeypatch):
    provider = {
        "name": "missing-key",
        "enabled": True,
        "priority": 1,
        "task_types": ["chat"],
        "api_key_env": "TEST_LLM_ROUTER_MISSING_KEY",
    }
    router = _isolated_router(provider)
    usage_log: list[dict] = []
    monkeypatch.delenv("TEST_LLM_ROUTER_MISSING_KEY", raising=False)
    monkeypatch.setattr(llm_router_module, "_log_llm_usage", usage_log.append)

    with pytest.raises(RuntimeError, match="所有模型供应商暂时不可用"):
        asyncio.run(router.chat([{"role": "user", "content": "test"}], max_tokens=1))

    assert usage_log[-1]["error_type"] == "missing_api_key"
    assert usage_log[-1]["provider"] == "missing-key"
    assert list(router._call_history) == usage_log


def test_chat_reports_cooldown_when_every_candidate_is_skipped(monkeypatch):
    provider = {
        "name": "cooling-down",
        "enabled": True,
        "priority": 1,
        "task_types": ["chat"],
        "api_key_env": "TEST_LLM_ROUTER_PRESENT_KEY",
    }
    router = _isolated_router(
        provider,
        state={"cooldown_until": time.time() + 60, "exhausted": False},
    )
    usage_log: list[dict] = []
    monkeypatch.setenv("TEST_LLM_ROUTER_PRESENT_KEY", "test-only-key")
    monkeypatch.setattr(llm_router_module, "_log_llm_usage", usage_log.append)

    with pytest.raises(RuntimeError, match="所有模型供应商暂时不可用"):
        asyncio.run(router.chat([{"role": "user", "content": "test"}], max_tokens=1))

    assert usage_log[-1]["error_type"] == "provider_cooldown"
    assert "cooldown" in usage_log[-1]["error_message"]


def test_parse_provider_response_rejects_empty_choices():
    with pytest.raises(ValueError, match="空 choices"):
        LLMRouter._parse_provider_response({}, "demo")


def test_disable_provider_reports_atomic_persistence_failure(tmp_path, monkeypatch):
    provider = {
        "name": "demo",
        "enabled": True,
        "priority": 1,
        "task_types": ["chat"],
        "api_key_env": "TEST_PROVIDER_KEY",
    }
    target = tmp_path / "providers.yaml"
    target.write_text(yaml.safe_dump({"providers": [provider]}), encoding="utf-8")
    router = _isolated_router(provider)
    monkeypatch.setattr(llm_router_module, "_providers_yaml_path", lambda: target)
    monkeypatch.setattr(safe_io_module, "atomic_write_yaml", lambda path, data: False)

    assert router.disable_provider("demo") is False
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["providers"][0]["enabled"] is True


def test_add_provider_does_not_replace_malformed_document(tmp_path, monkeypatch):
    existing = {
        "name": "existing",
        "enabled": True,
        "priority": 1,
        "task_types": ["chat"],
        "api_key_env": "TEST_PROVIDER_KEY",
    }
    target = tmp_path / "providers.yaml"
    malformed = "providers: [\n"
    target.write_text(malformed, encoding="utf-8")
    router = _isolated_router(existing)
    monkeypatch.setattr(llm_router_module, "_providers_yaml_path", lambda: target)

    assert router.add_provider({"name": "new-provider"}) is False
    assert target.read_text(encoding="utf-8") == malformed
