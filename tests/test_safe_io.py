import json

from bot.safe_io import atomic_write_json, atomic_write_text


def test_atomic_write_json_replaces_content_and_creates_backup(tmp_path):
    target = tmp_path / "data" / "sessions" / "demo.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"version": 1}), encoding="utf-8")

    assert atomic_write_json(target, {"version": 2}) is True

    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2}
    backups = list((tmp_path / "data" / "backups").glob("demo_*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"version": 1}


def test_atomic_write_json_serialization_failure_preserves_original(tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"safe": true}', encoding="utf-8")

    assert atomic_write_json(target, {"bad": object()}) is False

    assert target.read_text(encoding="utf-8") == '{"safe": true}'
    assert not list(tmp_path.glob(".tmp_*"))


def test_atomic_write_text_accepts_dedicated_backup_directory(tmp_path):
    target = tmp_path / "worlds" / "demo.yaml"
    backup_dir = tmp_path / "backups" / "worlds"
    target.parent.mkdir(parents=True)
    target.write_text("version: 1\n", encoding="utf-8")

    assert atomic_write_text(
        target,
        "version: 2\n",
        backup_dir=backup_dir,
    ) is True

    assert target.read_text(encoding="utf-8") == "version: 2\n"
    backups = list(backup_dir.glob("demo_*.yaml"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "version: 1\n"


def test_repeated_writes_in_same_second_keep_distinct_backups(tmp_path):
    target = tmp_path / "data" / "state" / "demo.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"version": 1}', encoding="utf-8")

    assert atomic_write_json(target, {"version": 2}) is True
    assert atomic_write_json(target, {"version": 3}) is True

    backups = list((tmp_path / "data" / "backups").glob("demo_*.json"))
    assert len(backups) == 2
