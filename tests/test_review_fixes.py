"""审查修复的回归测试。"""

import json

from bot.context_selector import ContextItem, SelectionResult
from bot.state_paths import state_path
from bot.telegram_handlers import should_send_memo_reminder
from config import settings


def test_memo_reminder_can_be_disabled():
    assert should_send_memo_reminder(40, 0) is False
    assert should_send_memo_reminder(40, -1) is False
    assert should_send_memo_reminder(40, 40) is True
    assert should_send_memo_reminder(41, 40) is False


def test_selector_prompt_context_keeps_all_layers_in_order():
    selection = SelectionResult(
        time_context=[ContextItem(source="time", content="时间")],
        story_context=[ContextItem(source="story", content="剧情")],
        memory_context=[
            ContextItem(source="state", content="场景"),
            ContextItem(source="memory", content="记忆"),
        ],
        relationship_context=[ContextItem(source="relationship", content="关系")],
        world_context=[ContextItem(source="world", content="世界")],
        character_context=[ContextItem(source="character", content="角色")],
    )

    prompt = selection.build_prompt_context()
    assert prompt is not None
    assert all(text in prompt for text in ("时间", "剧情", "场景", "关系", "世界", "角色", "记忆"))
    assert prompt.index("[状态]") < prompt.index("[关系]") < prompt.index("[事实]")


def test_legacy_state_is_copied_without_deleting_source(tmp_path, monkeypatch):
    state_dir = tmp_path / "data" / "state"
    legacy_dir = tmp_path / "memory"
    legacy_dir.mkdir(parents=True)
    legacy = legacy_dir / "demo_time_state.json"
    legacy.write_text(json.dumps({"day": 3}), encoding="utf-8")
    monkeypatch.setattr(settings, "STATE_DIR", state_dir)
    monkeypatch.setattr(settings, "LEGACY_STATE_DIR", legacy_dir)

    resolved = state_path("demo", "time_state")
    assert resolved == state_dir / "demo_time_state.json"
    assert json.loads(resolved.read_text(encoding="utf-8")) == {"day": 3}
    assert legacy.exists()


def test_project_root_backup_stays_inside_project(tmp_path, monkeypatch):
    from bot.safe_io import backup_file

    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    source = tmp_path / "providers.yaml"
    source.write_text("providers: []\n", encoding="utf-8")

    backup = backup_file(source)
    assert backup is not None
    assert backup.parent == tmp_path / "backups"


def test_web_session_secret_is_independent_from_password(monkeypatch):
    from types import SimpleNamespace
    from web.app import AppContext, create_app

    monkeypatch.setattr(settings, "WEB_PASSWORD", "password-only")
    monkeypatch.setattr(settings, "WEB_SESSION_SECRET", "separate-session-secret")
    ctx = AppContext(
        world_manager=SimpleNamespace(get_world=lambda: SimpleNamespace(WORLD_NAME="test")),
        roleplay_bot=SimpleNamespace(),
        client=SimpleNamespace(router=None),
        start_time=0,
    )
    app = create_app(ctx)
    assert app.secret_key == "separate-session-secret"
