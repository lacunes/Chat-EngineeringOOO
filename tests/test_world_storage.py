import json

import yaml

from bot import utils
from config import settings
from scripts.migrate_to_yaml import migrate_worlds
from web.routes.worlds import save_world_yaml


def test_json_only_legacy_world_remains_loadable(tmp_path, monkeypatch):
    worlds_dir = tmp_path / "data" / "worlds"
    worlds_dir.mkdir(parents=True)
    (worlds_dir / "legacy.json").write_text(
        json.dumps({"WORLD_NAME": "Legacy", "START_SCENE": "Old scene"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)

    world = utils.load_world("legacy")

    assert world.WORLD_NAME == "Legacy"
    assert world.START_SCENE == "Old scene"


def test_invalid_yaml_does_not_silently_fall_back_to_stale_json(tmp_path, monkeypatch):
    worlds_dir = tmp_path / "data" / "worlds"
    worlds_dir.mkdir(parents=True)
    (worlds_dir / "demo.yaml").write_text("WORLD_NAME: [\n", encoding="utf-8")
    (worlds_dir / "demo.json").write_text(
        json.dumps({"WORLD_NAME": "Stale copy"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)

    try:
        utils.load_world("demo")
    except ValueError as exc:
        assert "YAML" in str(exc)
    else:
        raise AssertionError("invalid canonical YAML must not load stale JSON")


def test_world_yaml_save_uses_atomic_writer_and_dedicated_backup(tmp_path, monkeypatch):
    target = tmp_path / "data" / "worlds" / "demo.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("WORLD_NAME: Before\n", encoding="utf-8")
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)

    save_world_yaml(target, {"WORLD_NAME": "After", "START_SCENE": "New scene"})

    saved = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert saved["WORLD_NAME"] == "After"
    backups = list((tmp_path / "backups" / "worlds").glob("demo_*.yaml"))
    assert len(backups) == 1
    assert yaml.safe_load(backups[0].read_text(encoding="utf-8"))["WORLD_NAME"] == "Before"


def test_legacy_migration_skips_existing_yaml_by_default(tmp_path, monkeypatch):
    worlds_dir = tmp_path / "data" / "worlds"
    worlds_dir.mkdir(parents=True)
    (worlds_dir / "legacy.json").write_text(
        json.dumps({"WORLD_NAME": "Legacy", "START_SCENE": "Old scene"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)

    assert migrate_worlds(worlds_dir) == (1, 0)
    yaml_path = worlds_dir / "legacy.yaml"
    first_content = yaml_path.read_text(encoding="utf-8")
    assert migrate_worlds(worlds_dir) == (0, 1)
    assert yaml_path.read_text(encoding="utf-8") == first_content
