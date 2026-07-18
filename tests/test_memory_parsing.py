from bot.memory_manager import _parse_category


def test_v3_memory_categories_are_not_reported_as_legacy():
    for category in ("fact", "event", "promise", "preference", "secret", "goal", "scene_state"):
        parsed, content = _parse_category(f"[{category}] remembered detail")
        assert parsed == category
        assert content == "remembered detail"
