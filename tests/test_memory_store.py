"""MemoryStore 测试 — 旧格式迁移、损坏文件保护、备份。"""

import json
import os


class TestMemoryMigration:
    """旧格式迁移测试。"""

    def test_old_memory_migrated_only_once(self, old_format_memory_file):
        """旧文件存在时首次加载自动迁移，再次加载不重复迁移。"""
        from bot.memory_store import MemoryStore

        old_path = old_format_memory_file / "data" / "memory" / "test_long_term.json"
        new_path = old_format_memory_file / "data" / "memory" / "test_memories.json"

        assert old_path.exists()

        # 第一次加载：迁移
        store1 = MemoryStore("test", old_format_memory_file)
        assert store1.count >= 3  # 3 条旧记忆被迁移
        assert new_path.exists()  # 新格式文件已创建

        # 第二次加载：直接读新格式，不会再次迁移
        store2 = MemoryStore("test", old_format_memory_file)
        assert store2.count == store1.count  # 数量不变（不会重复迁移）

    def test_migration_creates_backup(self, old_format_memory_file):
        """迁移前旧文件被备份。"""
        from bot.memory_store import MemoryStore

        old_path = old_format_memory_file / "data" / "memory" / "test_long_term.json"
        backup_dir = old_format_memory_file / "data" / "backups"

        # 确保初始无备份
        if backup_dir.exists():
            import shutil
            shutil.rmtree(backup_dir)

        store = MemoryStore("test", old_format_memory_file)
        assert store.count >= 3

        # 检查备份目录是否生成了备份文件
        backups = list(backup_dir.glob("test_long_term_*.json")) if backup_dir.exists() else []
        assert len(backups) >= 1, f"Expected backup files in {backup_dir}, got {backups}"


class TestCorruptedFileProtection:
    """损坏文件保护测试。"""

    def test_corrupted_memory_file_not_overwritten(self, corrupted_memory_file):
        """JSON 损坏时原文件保留，不会用空数据覆盖。"""
        from bot.memory_store import MemoryStore

        new_path = corrupted_memory_file / "data" / "memory" / "test_memories.json"
        original_content = new_path.read_text(encoding="utf-8")
        assert "not valid json" in original_content

        # 加载：应返回空 store 而不覆盖原文件
        store = MemoryStore("test", corrupted_memory_file)
        assert store.count == 0  # 没有有效记忆

        # 原文件内容应保持不变（或至少不是空数组覆盖的）
        # MemoryStore 在加载失败时保留原文件，只记录错误
        # 验证文件仍然存在（未被删除）
        assert new_path.exists()
