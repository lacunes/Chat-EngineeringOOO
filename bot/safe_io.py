"""
safe_io — 通用原子写入与备份工具。

所有运行时状态文件的写入都应通过本模块，确保：
1. 先写 .tmp 临时文件
2. flush + fsync
3. os.replace 原子替换
4. 写入前自动备份
5. JSON/YAML 写入后校验格式
"""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 备份保留数量
_MAX_BACKUPS = 20


def _backup_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_file(path: Path, backup_dir: Path | None = None) -> Path | None:
    """备份文件到指定目录（默认 data/backups/）。

    返回备份路径，失败返回 None。
    保留最近 _MAX_BACKUPS 个同名备份。
    """
    if not path.exists():
        return None
    try:
        if backup_dir is None:
            backup_dir = path.parent.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = _backup_timestamp()
        stem = path.stem
        backup_path = backup_dir / f"{stem}_{ts}{path.suffix}"
        shutil.copy2(path, backup_path)
        # 清理旧备份
        existing = sorted(backup_dir.glob(f"{stem}_*{path.suffix}"))
        for old in existing[:-_MAX_BACKUPS]:
            try:
                old.unlink()
            except Exception:
                pass
        logger.debug("Backed up %s → %s", path.name, backup_path.name)
        return backup_path
    except Exception as exc:
        logger.warning("Failed to backup %s: %s", path, exc)
        return None


def atomic_write_text(path: Path, content: str, backup: bool = True) -> bool:
    """原子写入文本文件。

    1. 备份旧文件（可选）
    2. 写入 .tmp 临时文件
    3. flush + fsync
    4. os.replace 替换
    """
    if backup:
        backup_file(path)

    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(path.parent), prefix=".tmp_", suffix=path.suffix,
            delete=False, encoding="utf-8",
        ) as f:
            tmp = f.name
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.error("atomic_write_text failed for %s: %s", path, exc)
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass
        return False


def atomic_write_json(path: Path, data: Any, backup: bool = True) -> bool:
    """原子写入 JSON 文件（写入后校验格式）。

    流程：dump → .tmp → flush+fsync → 重读校验 → replace
    """
    if backup:
        backup_file(path)

    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(path.parent), prefix=".tmp_", suffix=".json",
            delete=False, encoding="utf-8",
        ) as f:
            tmp = f.name
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # 校验：重读临时文件
        try:
            with open(tmp, "r", encoding="utf-8") as vf:
                json.load(vf)
        except json.JSONDecodeError as ve:
            logger.error("JSON validation failed for %s: %s — not replacing", path, ve)
            os.remove(tmp)
            return False

        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.error("atomic_write_json failed for %s: %s", path, exc)
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass
        return False


def atomic_write_yaml(path: Path, data: Any, backup: bool = True) -> bool:
    """原子写入 YAML 文件（写入后校验格式）。

    流程：dump → .tmp → flush+fsync → safe_load 校验 → replace
    """
    if backup:
        backup_file(path)

    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(path.parent), prefix=".tmp_", suffix=".yaml",
            delete=False, encoding="utf-8",
        ) as f:
            tmp = f.name
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())

        # 校验：重读临时文件
        try:
            with open(tmp, "r", encoding="utf-8") as vf:
                parsed = yaml.safe_load(vf)
            if parsed is None:
                logger.error("YAML validation failed for %s: parsed to None", path)
                os.remove(tmp)
                return False
        except yaml.YAMLError as ye:
            logger.error("YAML validation failed for %s: %s — not replacing", path, ye)
            os.remove(tmp)
            return False

        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.error("atomic_write_yaml failed for %s: %s", path, exc)
        if tmp:
            try:
                os.remove(tmp)
            except Exception:
                pass
        return False
