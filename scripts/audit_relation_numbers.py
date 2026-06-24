#!/usr/bin/env python3
"""只读扫描短期记忆中的关系面板式数字表达，不修改任何源文件。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


RELATION_NUMBER_PATTERN = re.compile(
    r"(?:当前)?(?:好感|信任|畏惧|依赖|怀疑|敌意)(?:度|值)?"
    r"\s*(?:已经|已)?\s*(?:达到|变为|为|是|增加|减少|上升|下降|[:：=])?"
    r"\s*[+\-]?\d+(?:\.\d+)?"
)


def scan_file(path: Path) -> list[dict]:
    """返回匹配项；读取失败抛出异常，绝不写入文件。"""
    messages = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(messages, list):
        raise ValueError("short memory JSON must be an array")

    findings: list[dict] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        content = str(message.get("content", ""))
        matches = [match.group(0) for match in RELATION_NUMBER_PATTERN.finditer(content)]
        if not matches:
            continue
        findings.append({
            "index": index,
            "id": message.get("id", "--"),
            "role": message.get("role", "--"),
            "matches": matches,
            "excerpt": content.replace("\r", " ").replace("\n", " ")[:200],
        })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="要扫描的 *_chat.json；省略时扫描 data/sessions/",
    )
    args = parser.parse_args()
    paths = args.paths or sorted(Path("data/sessions").glob("*_chat.json"))

    total = 0
    for path in paths:
        try:
            findings = scan_file(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[ERROR] {path}: {exc}")
            continue
        for finding in findings:
            total += 1
            print(
                f"{path} index={finding['index']} id={finding['id']} "
                f"role={finding['role']} matches={finding['matches']}"
            )
            print(f"  {finding['excerpt']}")

    print(f"relation-number findings: {total} (read-only; no files changed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
