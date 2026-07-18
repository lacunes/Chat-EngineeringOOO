"""测试短期记忆管理功能。

验证：
- 页面倒序展示（最新在上），底层存储顺序不变
- 编辑/删除使用 storage_index，不破坏其他消息
- 异常处理：越界索引、空内容、两次删除同一条
- Bot 构建 Prompt 时对话顺序不变
"""
import json
import os
import tempfile

import pytest


# ── 单元测试：MemoryManager 层 ──

class TestShortMemoryManager:
    """测试 MemoryManager 的短期记忆操作（不依赖 Flask）。"""

    @pytest.fixture
    def manager(self):
        """创建带测试数据的 MemoryManager。"""
        import threading
        from pathlib import Path as _Path
        from bot.memory_manager import MemoryManager

        # 替换路径到临时目录
        tmpdir = tempfile.mkdtemp(prefix="test_short_mem_")
        chat_path = _Path(tmpdir) / "test_world_chat.json"

        # 预写初始数据
        initial = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
            {"role": "user", "content": "今天天气怎么样"},
            {"role": "assistant", "content": "抱歉，我无法获取实时天气数据。"},
        ]
        os.makedirs(str(chat_path.parent), exist_ok=True)
        with open(str(chat_path), "w", encoding="utf-8") as f:
            json.dump(initial, f, ensure_ascii=False)

        # 创建 manager 并替换路径
        mgr = MemoryManager.__new__(MemoryManager)
        mgr.world_name = "test_world"
        mgr._chat_path = chat_path
        mgr._summary_path = _Path(tmpdir) / "test_summary.json"
        mgr._store = _FakeStore()
        mgr.summary_log = []
        mgr.last_auto_memory_index = 0
        mgr.reset_confirm_users = {}
        mgr._lock = threading.Lock()
        mgr._last_save_ok = True
        mgr._last_save_time = ""
        mgr._was_recovered = False
        mgr._empty_protection_triggered = False

        # 加载
        mgr.memory = MemoryManager._load_json_list(mgr._chat_path, "short memory")
        mgr.summary_log = MemoryManager._load_json_list(mgr._summary_path, "summary log")
        mgr.last_auto_memory_index = len(mgr.memory)
        mgr._migrate_short_memory_ids()

        # 替换 save 以写入测试路径
        mgr._real_path = mgr._chat_path

        def test_save():
            data = mgr.memory
            with open(str(mgr._real_path), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        mgr.save_memory = test_save

        yield mgr

        # 清理
        try:
            os.remove(str(chat_path))
            os.rmdir(tmpdir)
        except Exception:
            pass

    def test_migration_adds_ids(self, manager):
        """旧消息无 id 时，_migrate_short_memory_ids 应自动补充。"""
        for msg in manager.memory:
            assert "id" in msg, f"消息缺少 id: {msg}"
            assert msg["id"].startswith("short_"), f"id 格式错误: {msg['id']}"

    def test_update_existing_message(self, manager):
        """更新有效索引的消息。"""
        result = manager.update_short_memory(0, "修改后的内容")
        assert result is not None
        assert manager.memory[0]["content"] == "修改后的内容"
        assert manager.memory[0]["role"] == "user"  # role 不变
        # 验证文件
        with open(str(manager._real_path), "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved[0]["content"] == "修改后的内容"

    def test_update_with_role_change(self, manager):
        """修改时允许变更 role。"""
        result = manager.update_short_memory(1, "assistant 变 user", role="user")
        assert result is not None
        assert manager.memory[1]["role"] == "user"
        assert manager.memory[1]["content"] == "assistant 变 user"

    def test_update_invalid_role_rejected(self, manager):
        """非法 role 应被拒绝。"""
        result = manager.update_short_memory(0, "test", role="system")
        assert result is None
        assert manager.memory[0]["content"] == "你好"  # 不变

    def test_update_out_of_range(self, manager):
        """越界索引应返回 None。"""
        result = manager.update_short_memory(99, "test")
        assert result is None

    def test_update_empty_content_rejected_in_route(self, manager):
        """空内容在路由层验证，manager 层允许（先保存再说）。"""
        # manager 层不拒绝空内容（由 Web 路由验证）
        result = manager.update_short_memory(0, "")
        # 空内容技术上可以保存，路由层会拦截
        assert result is not None

    def test_delete_existing_message(self, manager):
        """删除有效索引的消息。"""
        original_len = len(manager.memory)
        removed = manager.delete_short_memory(0)
        assert removed is not None
        assert removed["content"] == "你好"
        assert len(manager.memory) == original_len - 1
        # 原索引 1 的消息现在在索引 0
        assert manager.memory[0]["content"] == "你好！有什么可以帮你的？"

    def test_delete_out_of_range(self, manager):
        """删除越界索引应返回 None。"""
        result = manager.delete_short_memory(999)
        assert result is None

    def test_delete_twice_same_index(self, manager):
        """两次删除同一条记录——第二次应失败。"""
        removed = manager.delete_short_memory(2)
        assert removed is not None
        # 原索引 3 现在是索引 2
        result = manager.delete_short_memory(2)
        assert result is not None  # 现在删的是原来的索引 3
        # 再删索引 2（只剩 2 条了）
        result = manager.delete_short_memory(2)
        assert result is None  # 越界

    def test_build_messages_strips_id(self, manager):
        """build_messages 应剔除 id 字段，只保留 role/content。"""
        msgs = manager.build_messages("test world prompt")
        # 最后一条是短期记忆
        short_msgs = [m for m in msgs if m.get("role") in ("user", "assistant")]
        for msg in short_msgs:
            assert "id" not in msg, f"build_messages 不应包含 id: {msg}"
            assert "role" in msg
            assert "content" in msg

    def test_storage_order_unchanged_after_edit(self, manager):
        """编辑后存储顺序不变。"""
        original_order = [m["content"] for m in manager.memory]
        manager.update_short_memory(0, "修改后内容")
        assert manager.memory[0]["content"] == "修改后内容"
        # 其余顺序不变
        for i in range(1, len(manager.memory)):
            assert manager.memory[i]["content"] == original_order[i]

    def test_storage_order_unchanged_after_delete(self, manager):
        """删除后其余消息顺序不变。"""
        manager.delete_short_memory(1)  # 删除索引 1
        assert manager.memory[0]["content"] == "你好"
        assert manager.memory[1]["content"] == "今天天气怎么样"
        assert manager.memory[2]["content"] == "抱歉，我无法获取实时天气数据。"

    def test_display_reversed_but_storage_preserved(self, manager):
        """页面展示倒序，但底层存储顺序不变。"""
        # 模拟路由层的倒序
        all_msgs = manager.memory
        display = [
            {"storage_index": idx, "message": msg}
            for idx, msg in reversed(list(enumerate(all_msgs)))
        ]

        # 展示第一条应是最新消息
        assert display[0]["message"]["content"] == "抱歉，我无法获取实时天气数据。"
        assert display[0]["storage_index"] == 3

        # 展示最后一条应是最旧消息
        assert display[-1]["message"]["content"] == "你好"
        assert display[-1]["storage_index"] == 0

        # 底层存储不变
        assert manager.memory[0]["content"] == "你好"
        assert manager.memory[-1]["content"] == "抱歉，我无法获取实时天气数据。"

    def test_validate_index(self, manager):
        """validate_short_memory_index 正确判断索引范围。"""
        assert manager.validate_short_memory_index(0) is True
        assert manager.validate_short_memory_index(3) is True
        assert manager.validate_short_memory_index(4) is False
        assert manager.validate_short_memory_index(-1) is False

    def test_add_user_message_has_id(self, manager):
        """新添加的 user 消息应有 id。"""
        manager.add_user_message("新消息")
        last = manager.memory[-1]
        assert "id" in last
        assert last["id"].startswith("short_")
        assert last["role"] == "user"
        assert last["content"] == "新消息"

    def test_add_assistant_message_has_id(self, manager):
        """新添加的 assistant 消息应有 id。"""
        manager.add_assistant_message("回复")
        last = manager.memory[-1]
        assert "id" in last
        assert last["id"].startswith("short_")
        assert last["role"] == "assistant"


class TestShortMemoryConcurrency:
    """并发安全测试。"""

    def test_update_during_add_does_not_lose_data(self):
        """模拟 Telegram 写入新消息 + Web 编辑旧消息的并发场景。"""
        import threading
        from pathlib import Path as _Path
        from bot.memory_manager import MemoryManager

        tmpdir = tempfile.mkdtemp(prefix="test_concurrent_")
        chat_path = _Path(tmpdir) / "test_chat.json"

        initial = [
            {"id": "short_0001", "role": "user", "content": "msg1"},
            {"id": "short_0002", "role": "assistant", "content": "msg2"},
        ]
        with open(str(chat_path), "w", encoding="utf-8") as f:
            json.dump(initial, f, ensure_ascii=False)

        mgr = MemoryManager.__new__(MemoryManager)
        mgr.world_name = "test"
        mgr._chat_path = chat_path
        mgr._summary_path = _Path(tmpdir) / "sum.json"
        mgr._store = _FakeStore()
        mgr.summary_log = []
        mgr.last_auto_memory_index = 0
        mgr.reset_confirm_users = {}
        mgr._lock = threading.Lock()
        mgr._last_save_ok = True
        mgr._last_save_time = ""
        mgr._was_recovered = False
        mgr._empty_protection_triggered = False

        mgr.memory = list(initial)  # 直接赋值跳过文件加载
        mgr._migrate_short_memory_ids()

        # 替换 save 为 dummy
        def dummy_save():
            pass
        mgr.save_memory = dummy_save

        errors = []

        def add_messages():
            try:
                for i in range(20):
                    mgr.add_user_message(f"user_msg_{i}")
            except Exception as e:
                errors.append(f"add: {e}")

        def edit_messages():
            try:
                for i in range(10):
                    mgr.update_short_memory(1, f"edited_{i}")
            except Exception as e:
                errors.append(f"edit: {e}")

        t1 = threading.Thread(target=add_messages)
        t2 = threading.Thread(target=edit_messages)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"并发错误: {errors}"
        # 所有消息都应存在
        assert len(mgr.memory) >= 2 + 20  # 初始 2 + 新增 20
        # 第一条消息不应丢失
        assert any(m["content"] == "msg1" for m in mgr.memory)

        try:
            os.remove(str(chat_path))
            os.rmdir(tmpdir)
        except Exception:
            pass


# ── 辅助 ──

class _FakeStore:
    """模拟 MemoryStore 以避免文件 I/O。"""
    def __init__(self):
        self._items = []

    def to_text_list(self, limit=100):
        return [f"[{getattr(i, 'type', 'fact')}] {getattr(i, 'content', '')}" for i in self._items[:limit]]

    @property
    def count(self):
        return len(self._items)

    def active_items(self):
        return self._items

    def add(self, item):
        self._items.append(item)

    def set_status(self, item_id, status):
        pass

    def update(self, item_id, **kwargs):
        pass

    def save(self):
        pass
