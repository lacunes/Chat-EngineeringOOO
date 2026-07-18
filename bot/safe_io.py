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
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)

# ── YAML 自定义 Dumper：强制引号包裹含特殊字符的字符串 ──
# 模型名如 DeepSeek-V3[Free]、Qwen/Qwen3.6-35B-A3B[Free]、claude-sonnet-4@anthropic
# 在 YAML 中 [] 会被解析为 flow sequence，@/: 等也可能导致解析问题。
# 此 dumper 对包含这些字符的字符串自动加双引号。

_YAML_SPECIAL_CHARS_RE = re.compile(r'[\[\]{}:,#&*!|>\'\"%@=`]')


class _SafeStringDumper(yaml.SafeDumper):
    pass


def _quoted_str_representer(dumper, data: str):
    """对含 YAML 特殊字符的字符串强制使用双引号风格。"""
    if _YAML_SPECIAL_CHARS_RE.search(data):
        return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='"')
    return dumper.represent_scalar('tag:yaml.org,2002:str', data)


_SafeStringDumper.add_representer(str, _quoted_str_representer)

# 备份保留数量
_MAX_BACKUPS = 20


def _backup_timestamp() -> str:
    # 微秒避免同一秒内连续写入覆盖上一份备份。
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def backup_file(path: Path, backup_dir: Path | None = None) -> Path | None:
    """备份文件到指定目录（默认 data/backups/）。

    返回备份路径，失败返回 None。
    保留最近 _MAX_BACKUPS 个同名备份。
    """
    if not path.exists():
        return None
    try:
        if backup_dir is None:
            # 根目录配置备份到项目内；数据文件沿用同级数据目录的备份约定。
            from config import settings
            if path.parent.resolve() == settings.BASE_DIR.resolve():
                backup_dir = settings.BASE_DIR / "backups"
            else:
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


def _atomic_write_content(
    path: Path,
    content: str,
    *,
    backup: bool,
    backup_dir: Path | None,
    suffix: str,
    label: str,
    validator: Callable[[str], None] | None = None,
) -> bool:
    """共享的 tmp → fsync → validate → replace 写入骨架。"""
    tmp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if backup:
            backup_file(path, backup_dir)

        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(path.parent),
            prefix=".tmp_",
            suffix=suffix,
            delete=False,
            encoding="utf-8",
        ) as file:
            tmp_path = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

        if validator:
            validator(tmp_path.read_text(encoding="utf-8"))

        os.replace(tmp_path, path)
        tmp_path = None
        return True
    except Exception as exc:
        logger.error("atomic_write_%s failed for %s: %s", label, path, exc)
        return False
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_text(
    path: Path,
    content: str,
    backup: bool = True,
    backup_dir: Path | None = None,
) -> bool:
    """原子写入文本，可为旧文件指定专用备份目录。"""
    return _atomic_write_content(
        path,
        content,
        backup=backup,
        backup_dir=backup_dir,
        suffix=path.suffix,
        label="text",
    )


def atomic_write_json(path: Path, data: Any, backup: bool = True) -> bool:
    """原子写入 JSON 文件（写入后重读校验格式）。"""
    try:
        content = json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as exc:
        logger.error("atomic_write_json failed for %s: %s", path, exc)
        return False

    def validate_json(text: str) -> None:
        json.loads(text)

    return _atomic_write_content(
        path,
        content,
        backup=backup,
        backup_dir=None,
        suffix=".json",
        label="json",
        validator=validate_json,
    )


def atomic_write_yaml(path: Path, data: Any, backup: bool = True) -> bool:
    """原子写入 YAML 文件（写入后重读校验格式）。"""
    try:
        content = yaml.dump(
            data,
            Dumper=_SafeStringDumper,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
    except yaml.YAMLError as exc:
        logger.error("atomic_write_yaml failed for %s: %s", path, exc)
        return False

    def validate_yaml(text: str) -> None:
        if yaml.safe_load(text) is None:
            raise ValueError("YAML parsed to None")

    return _atomic_write_content(
        path,
        content,
        backup=backup,
        backup_dir=None,
        suffix=".yaml",
        label="yaml",
        validator=validate_yaml,
    )
