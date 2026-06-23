"""测试关系网络 Web 表单保存。

验证 relations.html 中 row_index 修复后：
- 6 个维度（affection/trust/fear/dependence/suspicion/hostility）不会归零
- 保存后 JSON 文件中的值与表单提交一致
"""
import json
import os
import secrets
import tempfile

import pytest


def _create_test_app():
    """创建带测试数据的 Flask 应用，返回 (app, stub_rm, relations_path)。"""
    import threading

    # 在临时目录中创建数据
    data_dir = tempfile.mkdtemp(prefix="test_relations_")
    relations_path = os.path.join(data_dir, "relations.json")

    # 初始关系数据
    initial = {
        "characters": ["Alice", "Bob", "Charlie"],
        "relations": {
            "Alice->Bob": {
                "affection": 50, "trust": 60, "fear": 10,
                "dependence": 30, "suspicion": 20, "hostility": 5,
                "notes": ["old note"], "last_updated": "test",
            },
            "Bob->Charlie": {
                "affection": 70, "trust": 80, "fear": 15,
                "dependence": 40, "suspicion": 10, "hostility": 0,
                "notes": [], "last_updated": "test",
            },
        },
    }

    # 模拟 relationship_manager
    class _StubRM:
        def __init__(self):
            self.characters = list(initial["characters"])
            self.relations = dict(initial["relations"])
            self._lock = threading.Lock()
            self._reply_count_since_extract = 0

        @staticmethod
        def _empty_relation():
            return {
                "affection": 0, "trust": 0, "fear": 0,
                "dependence": 0, "suspicion": 0, "hostility": 0,
                "notes": [], "last_updated": "",
            }

        def save(self):
            data = {
                "characters": self.characters,
                "relations": self.relations,
                "_reply_count_since_extract": self._reply_count_since_extract,
            }
            with open(relations_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    stub_rm = _StubRM()

    # 创建 Fake AppContext，注入 app.config["ctx"]
    class _FakeWorld:
        WORLD_NAME = "test_world"

    class _FakeCtx:
        relationship_manager = stub_rm
        world = _FakeWorld()
        start_time = 0  # for inject_globals

    # 创建最小 Flask 应用
    from flask import Flask
    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(__file__), "..", "web", "templates",
    ))
    app.config["SECRET_KEY"] = "test-key"
    app.config["ctx"] = _FakeCtx()

    # 注入 CSRF token 模板全局变量（base.html 需要）
    @app.context_processor
    def inject_csrf():
        from flask import session as _session
        def _generate():
            if "_csrf_token" not in _session:
                _session["_csrf_token"] = secrets.token_hex(32)
            return _session["_csrf_token"]
        return {"csrf_token": _generate}

    # 注入全局模板变量（base.html 的 inject_globals 也需要 ctx 和 uptime）
    @app.context_processor
    def inject_globals():
        ctx = app.config.get("ctx")
        return {"ctx": ctx, "uptime": "--"}

    from web.routes.relations import relations_bp
    app.register_blueprint(relations_bp)

    return app, stub_rm, relations_path


class TestRelationsSave:
    """测试关系保存 API 的维度值正确性。"""

    def test_save_preserves_all_dimensions(self):
        """保存后 6 个维度值应与提交值一致，无归零。"""
        app, stub_rm, relations_path = _create_test_app()

        with app.test_client() as client:
            # 绕过登录检查
            with client.session_transaction() as sess:
                sess["logged_in"] = True

            # 模拟 POST：更新 Alice->Bob 的所有维度
            response = client.post("/relations/save", data={
                "mode": "structured",
                # Row 0: Alice->Bob — 修改值
                "rel_0_from": "Alice",
                "rel_0_to": "Bob",
                "rel_0_affection": "85",
                "rel_0_trust": "72",
                "rel_0_fear": "33",
                "rel_0_dependence": "41",
                "rel_0_suspicion": "19",
                "rel_0_hostility": "8",
                "rel_0_notes": "updated note",
                # Row 1: Bob->Charlie — 保持不变
                "rel_1_from": "Bob",
                "rel_1_to": "Charlie",
                "rel_1_affection": "70",
                "rel_1_trust": "80",
                "rel_1_fear": "15",
                "rel_1_dependence": "40",
                "rel_1_suspicion": "10",
                "rel_1_hostility": "0",
                "rel_1_notes": "",
            }, follow_redirects=False)

            assert response.status_code == 302  # redirect after save

        # 验证内存中的值
        ab = stub_rm.relations["Alice->Bob"]
        assert ab["affection"] == 85, f"affection expected 85, got {ab['affection']}"
        assert ab["trust"] == 72, f"trust expected 72, got {ab['trust']}"
        assert ab["fear"] == 33, f"fear expected 33, got {ab['fear']}"
        assert ab["dependence"] == 41, f"dependence expected 41, got {ab['dependence']}"
        assert ab["suspicion"] == 19, f"suspicion expected 19, got {ab['suspicion']}"
        assert ab["hostility"] == 8, f"hostility expected 8, got {ab['hostility']}"

        bc = stub_rm.relations["Bob->Charlie"]
        assert bc["affection"] == 70, f"affection expected 70, got {bc['affection']}"
        assert bc["trust"] == 80, f"trust expected 80, got {bc['trust']}"
        assert bc["fear"] == 15, f"fear expected 15, got {bc['fear']}"
        assert bc["dependence"] == 40, f"dependence expected 40, got {bc['dependence']}"
        assert bc["suspicion"] == 10, f"suspicion expected 10, got {bc['suspicion']}"
        assert bc["hostility"] == 0, f"hostility expected 0, got {bc['hostility']}"

        # 验证 JSON 文件中的值
        with open(relations_path, "r", encoding="utf-8") as f:
            saved = json.load(f)

        saved_ab = saved["relations"]["Alice->Bob"]
        for dim in ["affection", "trust", "fear", "dependence", "suspicion", "hostility"]:
            assert saved_ab[dim] == ab[dim], f"JSON mismatch for {dim}: {saved_ab[dim]} != {ab[dim]}"

    def test_save_no_zeroing_on_partial_update(self):
        """更新部分维度时，其他维度不应归零。"""
        app, stub_rm, relations_path = _create_test_app()

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True

            # 只提交 Alice->Bob，只改了 affection，其余维度不传
            response = client.post("/relations/save", data={
                "mode": "structured",
                "rel_0_from": "Alice",
                "rel_0_to": "Bob",
                "rel_0_affection": "99",
                "rel_0_trust": "60",
                "rel_0_fear": "10",
                "rel_0_dependence": "30",
                "rel_0_suspicion": "20",
                "rel_0_hostility": "5",
                "rel_0_notes": "",
            }, follow_redirects=False)

            assert response.status_code == 302  # redirect after save

        ab = stub_rm.relations["Alice->Bob"]
        assert ab["affection"] == 99
        # 其他维度保持提交值
        assert ab["trust"] == 60
        assert ab["fear"] == 10
        assert ab["hostility"] == 5

    def test_new_relation_creation(self):
        """新增关系行应正确保存。"""
        app, stub_rm, relations_path = _create_test_app()

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True

            response = client.post("/relations/save", data={
                "mode": "structured",
                "new_from": "Charlie",
                "new_to": "Alice",
                "new_affection": "45",
                "new_trust": "55",
                "new_fear": "20",
                "new_dependence": "35",
                "new_suspicion": "15",
                "new_hostility": "3",
                "new_notes": "new pair",
            }, follow_redirects=False)

            assert response.status_code == 302  # redirect after save

        assert "Charlie->Alice" in stub_rm.relations
        ca = stub_rm.relations["Charlie->Alice"]
        assert ca["affection"] == 45
        assert ca["trust"] == 55
        assert ca["fear"] == 20
        assert ca["hostility"] == 3

    def test_delete_relation(self):
        """勾选删除后关系应被移除。"""
        app, stub_rm, relations_path = _create_test_app()

        assert "Alice->Bob" in stub_rm.relations

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True

            response = client.post("/relations/save", data={
                "mode": "structured",
                "rel_0_from": "Alice",
                "rel_0_to": "Bob",
                "rel_0_affection": "50",
                "rel_0_trust": "60",
                "rel_0_fear": "10",
                "rel_0_dependence": "30",
                "rel_0_suspicion": "20",
                "rel_0_hostility": "5",
                "rel_0_notes": "",
                "rel_0_delete": "1",
            }, follow_redirects=False)

            assert response.status_code == 302  # redirect after save

        assert "Alice->Bob" not in stub_rm.relations
