"""短期记忆关系数字扫描脚本测试。"""

import json

from scripts.audit_relation_numbers import scan_file


def test_scan_reports_without_modifying_file(tmp_path):
    path = tmp_path / "world_chat.json"
    messages = [
        {"id": "one", "role": "assistant", "content": "当前好感60。"},
        {"id": "two", "role": "assistant", "content": "桌上有 3 个苹果。"},
    ]
    path.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()

    findings = scan_file(path)

    assert len(findings) == 1
    assert findings[0]["id"] == "one"
    assert path.read_bytes() == before
