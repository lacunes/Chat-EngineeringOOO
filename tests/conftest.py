"""pytest fixtures — 共享测试环境。"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def tmp_dir():
    """临时目录，测试结束自动清理。"""
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def event_bus():
    """全新的 EventBus 实例。"""
    from bot.event_bus import EventBus
    return EventBus()


@pytest.fixture
def memory_store(tmp_dir):
    """创建临时目录下的 MemoryStore 实例。"""
    from bot.memory_store import MemoryStore
    (tmp_dir / "data" / "memory").mkdir(parents=True)
    return MemoryStore("test", tmp_dir)


@pytest.fixture
def old_format_memory_file(tmp_dir):
    """创建旧格式长期记忆文件（字符串列表）。"""
    mem_dir = tmp_dir / "data" / "memory"
    mem_dir.mkdir(parents=True)
    old_path = mem_dir / "test_long_term.json"
    data = [
        "[hard_fact] 陈平是退役特工",
        "[relationship] 陈平对林栖的信任度为高",
        "[plot_fact] 林栖发现了陈平的真实身份",
    ]
    old_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return tmp_dir


@pytest.fixture
def corrupted_memory_file(tmp_dir):
    """创建损坏的 JSON 文件。"""
    mem_dir = tmp_dir / "data" / "memory"
    mem_dir.mkdir(parents=True)
    new_path = mem_dir / "test_memories.json"
    new_path.write_text("this is not valid json {{{", encoding="utf-8")
    return tmp_dir
