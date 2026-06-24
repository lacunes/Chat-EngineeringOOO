"""关系数值链路、异步版本和正文数字检测测试。"""

import asyncio
import json

from bot.relationship_manager import RelationshipManager
from config import settings


def _manager(tmp_path, monkeypatch) -> RelationshipManager:
    monkeypatch.setattr(settings, "MEMORY_DIR", tmp_path)
    return RelationshipManager("test_world")


def _set_relation(rm, key, affection):
    before = rm.snapshot_state()
    rm.characters = sorted(set(rm.characters) | set(key.split("->")))
    rm.relations[key] = rm._empty_relation()
    rm.relations[key]["affection"] = affection
    rm.commit_web_manual_change(before, action="test")


def test_web_memory_json_and_prompt_use_same_total(tmp_path, monkeypatch):
    rm = _manager(tmp_path, monkeypatch)
    _set_relation(rm, "Alice->Bob", 60)

    persisted = json.loads(rm.file_path.read_text(encoding="utf-8"))
    debug = rm.get_debug_info()
    assert rm.relations["Alice->Bob"]["affection"] == 60
    assert persisted["relations"]["Alice->Bob"]["affection"] == 60
    assert "好感60" in rm.get_summary()
    assert "好感60" in debug["prompt_summary"]
    assert debug["consistency_ok"] is True
    assert debug["revision"] == 1
    reloaded = RelationshipManager("test_world")
    assert reloaded.revision == 1
    assert reloaded.relations["Alice->Bob"]["affection"] == 60
    assert "好感60" in reloaded.get_summary()


def test_auto_delta_records_before_delta_after(tmp_path, monkeypatch):
    rm = _manager(tmp_path, monkeypatch)
    _set_relation(rm, "Alice->Bob", 60)
    base_revision = rm.revision

    records = rm.apply_changes(
        [{"from": "Alice", "to": "Bob", "changes": {"affection": 2}, "note": "helped"}],
        120,
        expected_revision=base_revision,
        extraction_metadata={"message_range": "short_a..short_b", "base_relation_version": base_revision},
    )

    assert records == [{
        "pair": "Alice→Bob",
        "dimension": "affection",
        "before_value": 60,
        "delta": 2,
        "after_value": 62,
        "reason": "helped",
        "trigger_message_index": 120,
    }]
    assert rm.relations["Alice->Bob"]["affection"] == 62
    assert rm.revision == base_revision + 1


def test_stale_async_extraction_is_discarded_after_web_change(tmp_path, monkeypatch):
    rm = _manager(tmp_path, monkeypatch)
    _set_relation(rm, "Alice->Bob", 60)
    rm._reply_count_since_extract = 1
    monkeypatch.setattr(settings, "RELATION_EXTRACT_INTERVAL", 1)
    monkeypatch.setattr(settings, "RELATION_EXTRACT_REQUIRE_SIGNAL", False)

    class FakeClient:
        async def chat(self, *args, **kwargs):
            before = rm.snapshot_state()
            rm.relations["Alice->Bob"]["affection"] = 80
            rm.commit_web_manual_change(before, action="concurrent_web_save")
            return ('[{"from":"Alice","to":"Bob","changes":{"affection":2},"note":"helped"}]', None)

    memory = [
        {"id": "short_a", "role": "user", "content": "Alice helped Bob"},
        {"id": "short_b", "role": "assistant", "content": "Bob thanked Alice"},
    ]
    asyncio.run(rm.auto_extract(memory, FakeClient()))
    assert rm.relations["Alice->Bob"]["affection"] == 80
    assert rm.last_modified_source == "web_manual"


def test_relation_directions_are_independent(tmp_path, monkeypatch):
    rm = _manager(tmp_path, monkeypatch)
    _set_relation(rm, "Alice->Bob", 60)
    _set_relation(rm, "Bob->Alice", 15)
    assert rm.relations["Alice->Bob"]["affection"] == 60
    assert rm.relations["Bob->Alice"]["affection"] == 15
    summary = rm.get_summary()
    assert "Alice->Bob：好感60" in summary
    assert "Bob->Alice：好感15" in summary


def test_relation_number_detection_warns_without_rewriting(tmp_path, monkeypatch):
    rm = _manager(tmp_path, monkeypatch)
    bad_reply = "她对你的好感已经达到75。信任+2。今天买了 3 个苹果。"
    matches = rm.warn_if_reply_exposes_relation_numbers(bad_reply)
    assert "好感已经达到75" in matches
    assert "信任+2" in matches
    assert "3 个苹果" not in matches
    assert bad_reply.endswith("今天买了 3 个苹果。")
