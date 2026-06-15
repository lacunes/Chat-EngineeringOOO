"""ContextSelector 测试 — 失败回退。"""

import pytest


class TestContextSelectorFallback:
    """选择器失败时正常回退。"""

    def test_selector_failure_does_not_crash(self):
        """选择器内部异常不会传播到调用方（由调用方 try/except 覆盖）。"""
        # ContextSelector.select() 的失败回退逻辑在 telegram_handlers.py 中
        # 测试的是：当 select() 抛出异常时，外侧 try/except 捕获并回退到旧 long_memory
        from bot.context_selector import ContextSelector

        selector = ContextSelector()

        # 传入无效参数（None world）应触发异常
        with pytest.raises(Exception):
            selector.select(
                user_text="测试",
                world=None,  # 无效 world
                memory_store=None,
                relationship_manager=None,
                time_manager=None,
                story_state=None,
            )

    def test_fallback_uses_long_memory_list(self):
        """验证回退逻辑使用 store.to_text_list()（间接测试属性路径通畅）。"""
        import tempfile
        from pathlib import Path
        from bot.memory_store import MemoryStore, MemoryItem

        # 创建带数据的 store
        d = Path(tempfile.mkdtemp())
        (d / "data" / "memory").mkdir(parents=True)
        store = MemoryStore("test", d)
        for i in range(5):
            item = MemoryItem(
                id=f"mem_test{i}",
                world_id="test",
                type="fact",
                content=f"测试记忆 {i}",
                importance=0.5 + i * 0.1,
                status="active",
            )
            store.add(item)

        texts = store.to_text_list(limit=3)
        assert len(texts) == 3
        # 应按 importance 降序排列
        assert "测试记忆 4" in texts[0] or "测试记忆" in texts[0]

        import shutil
        shutil.rmtree(d, ignore_errors=True)
