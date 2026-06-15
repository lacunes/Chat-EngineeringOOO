"""EventBus 测试 — 优先级、错误隔离、递归检测、单次执行。"""

import pytest


class TestEventBusPriority:
    """监听器按优先级执行。"""

    def test_listeners_execute_in_priority_order(self, event_bus):
        order = []

        @event_bus.on("test_event", priority=30)
        def third(**kwargs):
            order.append("third")

        @event_bus.on("test_event", priority=10)
        def first(**kwargs):
            order.append("first")

        @event_bus.on("test_event", priority=20)
        def second(**kwargs):
            order.append("second")

        event_bus.emit("test_event")
        assert order == ["first", "second", "third"]

    def test_no_listeners_returns_empty(self, event_bus):
        result = event_bus.emit("nonexistent")
        assert result == {}


class TestEventBusErrorIsolation:
    """单个监听器报错不中断后续监听器。"""

    def test_single_listener_error_does_not_block_others(self, event_bus):
        order = []

        @event_bus.on("test_event", priority=10)
        def ok_first(**kwargs):
            order.append("first")

        @event_bus.on("test_event", priority=20)
        def broken(**kwargs):
            order.append("broken")
            raise ValueError("模拟监听器异常")

        @event_bus.on("test_event", priority=30)
        def ok_third(**kwargs):
            order.append("third")

        result = event_bus.emit("test_event")
        # 三个都尝试执行
        assert order == ["first", "broken", "third"]
        # broken 返回 None
        assert result.get("broken") is None
        # ok_third 正常返回
        assert result.get("ok_third") is None  # 无返回值
        # ok_first 正常返回
        assert "ok_first" in result

    def test_all_listeners_fail_still_returns_results(self, event_bus):
        @event_bus.on("test_event", priority=10)
        def fail1(**kwargs):
            raise RuntimeError("fail1")

        @event_bus.on("test_event", priority=20)
        def fail2(**kwargs):
            raise RuntimeError("fail2")

        result = event_bus.emit("test_event")
        assert result == {"fail1": None, "fail2": None}


class TestEventBusRecursiveDetection:
    """递归触发检测。"""

    def test_recursive_emit_raises_error(self, event_bus):
        @event_bus.on("test_event", priority=10)
        def recurser(**kwargs):
            event_bus.emit("test_event")  # 递归触发

        with pytest.raises(RuntimeError, match="Recursive emit detected"):
            event_bus.emit("test_event")

    def test_nested_different_events_allowed(self, event_bus):
        """不同事件的嵌套触发应被允许。"""
        order = []

        @event_bus.on("event_a", priority=10)
        def a_handler(**kwargs):
            order.append("a")
            event_bus.emit("event_b")

        @event_bus.on("event_b", priority=10)
        def b_handler(**kwargs):
            order.append("b")

        event_bus.emit("event_a")
        assert order == ["a", "b"]


class TestEventBusRequestId:
    """请求 ID 注入。"""

    def test_request_id_auto_generated(self, event_bus):
        captured = {}

        @event_bus.on("test_event", priority=10)
        def capture(**kwargs):
            captured["id"] = kwargs.get("request_id", "")

        event_bus.emit("test_event")
        assert len(captured["id"]) == 8  # uuid hex 8 chars

    def test_request_id_passed_through(self, event_bus):
        captured = {}

        @event_bus.on("test_event", priority=10)
        def capture(**kwargs):
            captured["id"] = kwargs.get("request_id", "")

        event_bus.emit("test_event", request_id="myid1234")
        assert captured["id"] == "myid1234"
