"""把 JSON-only 旧世界迁移为当前 YAML 格式。"""

import argparse
import json
from pathlib import Path

from web.routes.worlds import save_world_yaml


def migrate_worlds(data_dir: Path, *, overwrite: bool = False) -> tuple[int, int]:
    """迁移目录中的旧 JSON；默认跳过已有 YAML，返回 (迁移数, 跳过数)。"""
    migrated = 0
    skipped = 0
    for json_file in sorted(data_dir.glob("*.json")):
        yaml_path = json_file.with_suffix(".yaml")
        if yaml_path.exists() and not overwrite:
            skipped += 1
            continue

        data = json.loads(json_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{json_file} 的根节点必须是字典")
        save_world_yaml(yaml_path, data)
        migrated += 1
    return migrated, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已有 YAML；旧文件会先备份到 backups/worlds/",
    )
    args = parser.parse_args()
    migrated, skipped = migrate_worlds(Path("data/worlds"), overwrite=args.overwrite)
    print(f"Migration complete: migrated={migrated}, skipped={skipped}")


if __name__ == "__main__":
    main()
