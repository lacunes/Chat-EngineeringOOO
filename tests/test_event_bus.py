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


class TestListenerLifecycle:
    """监听器生命周期：重复注册不累积，世界切换不累积。"""

    def test_repeated_registration_does_not_accumulate(self, event_bus):
        """连续调用注册逻辑 3 次后，监听器总数始终为 2。"""
        # 模拟 _register_event_listeners 的注册/卸载模式
        def register():
            # 先卸旧
            for key in ("_fn_a", "_fn_b"):
                old = getattr(register, key, None)
                if old is not None:
                    event_bus.off("test_lifecycle", old)

            @event_bus.on("test_lifecycle", priority=10)
            def _fn_a(**kwargs):
                pass

            @event_bus.on("test_lifecycle", priority=20)
            def _fn_b(**kwargs):
                pass

            # 保存引用（模拟 setattr on self）
            register._fn_a = _fn_a
            register._fn_b = _fn_b

        # 首次注册
        register()
        assert len(event_bus._listeners.get("test_lifecycle", [])) == 2

        # 第二次注册（模拟世界切换）
        register()
        assert len(event_bus._listeners.get("test_lifecycle", [])) == 2, (
            "第二次注册后监听器应仍为2个，实际: "
            f"{len(event_bus._listeners.get('test_lifecycle', []))}"
        )

        # 第三次注册
        register()
        assert len(event_bus._listeners.get("test_lifecycle", [])) == 2, (
            "第三次注册后监听器应仍为2个，实际: "
            f"{len(event_bus._listeners.get('test_lifecycle', []))}"
        )

    def test_world_switch_does_not_accumulate_listeners(self, event_bus):
        """模拟 _init_managers → _register_event_listeners 重复执行。"""
        call_counts = {"maintenance": 0, "time": 0}

        class FakeBot:
            """模拟 RoleplayBot 的最小接口。"""
            def __init__(self):
                self.event_bus = event_bus

            def _init_managers(self):
                self._register_event_listeners()

            def _register_event_listeners(self):
                for name in ("_on_mem", "_on_time"):
                    old = getattr(self, name, None)
                    if old is not None:
                        self.event_bus.off("test_switch", old)

                @self.event_bus.on("test_switch", priority=10)
                def _on_mem(**kwargs):
                    call_counts["maintenance"] += 1

                @self.event_bus.on("test_switch", priority=20)
                def _on_time(**kwargs):
                    call_counts["time"] += 1

                self._on_mem = _on_mem
                self._on_time = _on_time

        bot = FakeBot()

        # 三轮 init（模拟三次世界切换）
        for round_num in range(3):
            bot._init_managers()
            listeners = event_bus._listeners.get("test_switch", [])
            assert len(listeners) == 2, (
                f"第{round_num + 1}轮init后监听器数: {len(listeners)}，预期2"
            )

        # emit 一次，每个监听器应只被调用一次
        event_bus.emit("test_switch")
        assert call_counts["maintenance"] == 1, (
            f"maintenance 应调用1次，实际: {call_counts['maintenance']}"
        )
        assert call_counts["time"] == 1, (
            f"time 应调用1次，实际: {call_counts['time']}"
        )

    def test_emit_calls_each_listener_exactly_once(self, event_bus):
        """emit 一次 after_assistant_reply，每个监听器只被调用一次。"""
        call_counter = {}

        @event_bus.on("test_once", priority=10)
        def listener_a(**kwargs):
            call_counter["a"] = call_counter.get("a", 0) + 1

        @event_bus.on("test_once", priority=20)
        def listener_b(**kwargs):
            call_counter["b"] = call_counter.get("b", 0) + 1

        event_bus.emit("test_once")
        assert call_counter.get("a") == 1
        assert call_counter.get("b") == 1

        # 再次 emit 不应影响第一次的结果——每次 emit 独立计数
        event_bus.emit("test_once")
        assert call_counter.get("a") == 2
        assert call_counter.get("b") == 2
