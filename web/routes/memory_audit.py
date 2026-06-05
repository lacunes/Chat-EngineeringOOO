"""记忆污染检查路由 — 规则检查 + AI 检查两层。

检查类型：duplicate, contradiction, outdated, hallucination_risk,
         too_trivial, unclear_subject, too_long, too_short
报告存入 memory_audit/ 目录。
"""

import asyncio
import json
import logging
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for

from config import settings
from web.app import _ctx, audit_log, _flash_redirect
from web.routes.auth import login_required

logger = logging.getLogger(__name__)

audit_bp = Blueprint("memory_audit", __name__, url_prefix="/memory-audit")

AUDIT_DIR: Path = settings.BASE_DIR / "memory_audit"

# ── 含糊词列表 ──
VAGUE_WORDS = ["某人", "某处", "有一天", "有一次", "好像", "大概", "也许", "可能",
               "似乎", "听说", "据说", "不清楚", "不太确定", "很", "非常", "特别",
               "有点", "一些", "很多", "不少"]

# ── AI 检查提示词 ──
AUDIT_PROMPT = """你是长期记忆质量检查助手。
请分析以下长期记忆条目，找出以下类型的问题：
- contradiction: 两条记忆相互矛盾
- outdated: 某条记忆描述的状态已经被后续事件覆盖
- hallucination_risk: 记忆内容看起来像是被虚构或过度推测的
- too_trivial: 内容过于琐碎，不值得长期保留
- unclear_subject: 主语不明确，不知道说的是谁
- duplicate_semantic: 两条记忆语义相同但表述不同

输出 JSON 数组，每个元素格式：
{"indices": [涉及的记忆编号列表], "type": "问题类型", "description": "问题说明（中文）", "suggested_action": "建议操作（merge/rewrite/delete/keep）", "suggested_text": "如果是合并/改写，给出建议文本，否则省略此字段"}

只输出 JSON 数组，不要解释。如果无明显问题输出 []。"""


@audit_bp.route("/")
@login_required
def index():
    """记忆检查主页面。"""
    ctx = _ctx()
    # 列出已有报告
    reports = _list_reports()
    return render_template("memory_audit.html",
                           long_count=ctx.memory.long_memory_count,
                           reports=reports,
                           ctx=ctx)


@audit_bp.route("/run", methods=["POST"])
@login_required
def run_audit():
    """执行记忆污染检查（规则检查 + AI 检查）。"""
    ctx = _ctx()
    items = list(ctx.memory.long_memory)
    if not items:
        return _flash_redirect(url_for("memory_audit.index"),
                               "没有长期记忆可供检查", "info")

    mode = request.form.get("mode", "both")  # rules / ai / both
    issues: list[dict] = []

    # ── 第一层：规则检查 ──
    if mode in ("rules", "both"):
        rule_issues = _rule_check(items)
        issues.extend(rule_issues)
        logger.info("Memory audit: rule check found %d issues", len(rule_issues))

    # ── 第二层：AI 检查 ──
    if mode in ("ai", "both"):
        ai_issues = _run_ai_check(items, ctx)
        issues.extend(ai_issues)
        logger.info("Memory audit: AI check found %d issues", len(ai_issues))

    # ── 保存报告 ──
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = AUDIT_DIR / f"memory-audit-{ts}.json"
    report = {
        "timestamp": datetime.now().isoformat(),
        "total_items": len(items),
        "issue_count": len(issues),
        "items": [{"index": i, "text": t} for i, t in enumerate(items)],
        "issues": issues,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    audit_log("运行记忆污染检查", f"报告: {report_path.name}, 问题: {len(issues)}")
    return _flash_redirect(url_for("memory_audit.view_report", filename=report_path.name),
                           f"检查完成，发现 {len(issues)} 个问题")


@audit_bp.route("/report/<filename>")
@login_required
def view_report(filename: str):
    """查看检查报告。"""
    ctx = _ctx()
    path = AUDIT_DIR / filename
    if not path.exists():
        return _flash_redirect(url_for("memory_audit.index"),
                               "报告不存在", "error")

    report = json.loads(path.read_text(encoding="utf-8"))
    return render_template("memory_audit_report.html",
                           filename=filename,
                           report=report,
                           ctx=ctx)


@audit_bp.route("/report/<filename>/action", methods=["POST"])
@login_required
def apply_action(filename: str):
    """处理检查报告中的问题：采用建议/删除/忽略。"""
    ctx = _ctx()
    action = request.form.get("action", "ignore")
    issue_idx = request.form.get("issue_index", "")
    path = AUDIT_DIR / filename

    if not path.exists():
        return _flash_redirect(url_for("memory_audit.index"),
                               "报告不存在", "error")

    report = json.loads(path.read_text(encoding="utf-8"))
    issues = report.get("issues", [])

    try:
        idx = int(issue_idx)
        issue = issues[idx]
    except (ValueError, IndexError):
        return _flash_redirect(url_for("memory_audit.view_report", filename=filename),
                               "无效的问题索引", "error")

    affected_indices = issue.get("indices", [])
    if not affected_indices:
        return _flash_redirect(url_for("memory_audit.view_report", filename=filename),
                               "此问题未关联记忆条目", "error")

    if action == "adopt":
        # 采用建议：根据 suggested_action 执行
        suggested_action = issue.get("suggested_action", "keep")
        suggested_text = issue.get("suggested_text", "")

        if suggested_action == "merge" and suggested_text:
            # 合并：用建议文本替换涉及的第一条，删除其余
            primary_idx = min(affected_indices)
            if 0 <= primary_idx < len(ctx.memory.long_memory):
                ctx.memory.long_memory[primary_idx] = suggested_text
            # 删除其余涉及条目（从高到低删，避免索引偏移）
            for i in sorted(affected_indices, reverse=True):
                if i != primary_idx and 0 <= i < len(ctx.memory.long_memory):
                    ctx.memory.long_memory.pop(i)
            ctx.memory.save_long_memory()
            audit_log("采用记忆修复建议", f"{filename}#{idx}: merge -> {suggested_text[:60]}")

        elif suggested_action == "rewrite" and suggested_text:
            # 改写：用建议文本替换
            for i in affected_indices:
                if 0 <= i < len(ctx.memory.long_memory):
                    ctx.memory.long_memory[i] = suggested_text
            ctx.memory.save_long_memory()
            audit_log("采用记忆修复建议", f"{filename}#{idx}: rewrite -> {suggested_text[:60]}")

        elif suggested_action == "delete":
            # 删除涉及条目
            for i in sorted(affected_indices, reverse=True):
                if 0 <= i < len(ctx.memory.long_memory):
                    ctx.memory.long_memory.pop(i)
            ctx.memory.save_long_memory()
            audit_log("采用记忆修复建议", f"{filename}#{idx}: delete {affected_indices}")

        # 标记问题为已处理
        issue["resolved"] = True
        issue["resolved_action"] = action
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        return _flash_redirect(url_for("memory_audit.view_report", filename=filename),
                               "已采用建议")

    elif action == "delete_item":
        # 直接删除涉及的记忆条目
        for i in sorted(affected_indices, reverse=True):
            if 0 <= i < len(ctx.memory.long_memory):
                ctx.memory.long_memory.pop(i)
        ctx.memory.save_long_memory()
        issue["resolved"] = True
        issue["resolved_action"] = "delete_item"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        audit_log("删除问题记忆", f"{filename}#{idx}: indices={affected_indices}")
        return _flash_redirect(url_for("memory_audit.view_report", filename=filename),
                               "已删除相关记忆条目")

    elif action == "ignore":
        issue["resolved"] = True
        issue["resolved_action"] = "ignore"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return _flash_redirect(url_for("memory_audit.view_report", filename=filename),
                               "已忽略此问题")

    return _flash_redirect(url_for("memory_audit.view_report", filename=filename),
                           "未知操作", "error")


@audit_bp.route("/report/<filename>/manual-edit", methods=["POST"])
@login_required
def manual_edit(filename: str):
    """手动编辑某条记忆。"""
    ctx = _ctx()
    item_index = request.form.get("item_index", "")
    new_text = (request.form.get("new_text") or "").strip()

    try:
        i = int(item_index)
    except ValueError:
        return _flash_redirect(url_for("memory_audit.view_report", filename=filename),
                               "无效的记忆索引", "error")

    if 0 <= i < len(ctx.memory.long_memory):
        ctx.memory.long_memory[i] = new_text
        ctx.memory.save_long_memory()
        audit_log("手动编辑记忆", f"#{i}: {new_text[:60]}")
        return _flash_redirect(url_for("memory_audit.view_report", filename=filename),
                               f"已编辑 #{i + 1}")

    return _flash_redirect(url_for("memory_audit.view_report", filename=filename),
                           "索引超出范围", "error")


# ═══════════════════════════════════════════════════════════
# 规则检查
# ═══════════════════════════════════════════════════════════

def _rule_check(items: list[str]) -> list[dict]:
    """执行规则检查，返回问题列表。"""
    issues: list[dict] = []

    for i, text in enumerate(items):
        if not isinstance(text, str):
            continue

        # 1. 过短（少于 5 个字符）
        if len(text) < 5:
            issues.append({
                "indices": [i],
                "type": "too_short",
                "description": f"记忆过短（仅 {len(text)} 字符），可能缺乏完整信息。",
                "suggested_action": "delete",
            })

        # 2. 过长（超过 200 字符）
        if len(text) > 200:
            issues.append({
                "indices": [i],
                "type": "too_long",
                "description": f"记忆过长（{len(text)} 字符），建议精简到 100 字以内。",
                "suggested_action": "rewrite",
            })

        # 3. 含糊词检测
        vague_hits = [w for w in VAGUE_WORDS if w in text]
        if vague_hits:
            issues.append({
                "indices": [i],
                "type": "unclear_subject",
                "description": f"含含糊表述: {', '.join(vague_hits[:3])}",
                "suggested_action": "rewrite",
            })

    # 4. 完全重复检测
    seen: dict[str, int] = {}
    for i, text in enumerate(items):
        if not isinstance(text, str):
            continue
        normalized = text.strip()
        if normalized in seen:
            issues.append({
                "indices": [seen[normalized], i],
                "type": "duplicate",
                "description": "两条记忆内容完全重复。",
                "suggested_action": "merge",
                "suggested_text": normalized,
            })
        else:
            seen[normalized] = i

    # 5. 高相似度检测（相似度 > 0.8）
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if not isinstance(items[i], str) or not isinstance(items[j], str):
                continue
            sim = SequenceMatcher(None, items[i], items[j]).ratio()
            if sim > 0.80 and sim < 1.0:
                # 检查是否已被标记为重复
                already = any(
                    iss["type"] == "duplicate" and set(iss["indices"]) == {i, j}
                    for iss in issues
                )
                if not already:
                    issues.append({
                        "indices": [i, j],
                        "type": "duplicate",
                        "description": f"两条记忆高度相似（相似度 {sim:.0%}），建议合并。",
                        "suggested_action": "merge",
                        "suggested_text": items[i] if len(items[i]) >= len(items[j]) else items[j],
                    })

    return issues


# ═══════════════════════════════════════════════════════════
# AI 检查
# ═══════════════════════════════════════════════════════════

def _run_ai_check(items: list[str], ctx) -> list[dict]:
    """使用 AI 检查语义层面的问题。分批处理，每批 20-40 条。"""
    batch_size = 30
    all_issues: list[dict] = []

    for batch_start in range(0, len(items), batch_size):
        batch_end = min(batch_start + batch_size, len(items))
        batch = items[batch_start:batch_end]
        indexed_batch = [
            {"index": batch_start + i, "text": t}
            for i, t in enumerate(batch)
        ]

        prompt_text = json.dumps(indexed_batch, ensure_ascii=False)

        try:
            loop = asyncio.new_event_loop()
            result, _ = loop.run_until_complete(
                ctx.client.chat(
                    [
                        {"role": "system", "content": AUDIT_PROMPT},
                        {"role": "user", "content": prompt_text},
                    ],
                    max_tokens=800,
                    temperature=0.3,
                    purpose="memory_audit",
                )
            )
            loop.close()

            # 解析 AI 输出
            ai_issues = _parse_audit_json(result)
            all_issues.extend(ai_issues)
            logger.info("AI memory audit batch %d-%d: %d issues",
                        batch_start, batch_end, len(ai_issues))

        except Exception as exc:
            logger.warning("AI memory audit batch %d-%d failed: %s",
                           batch_start, batch_end, exc)

    return all_issues


def _parse_audit_json(text: str) -> list[dict]:
    """容错解析 AI 返回的 JSON 数组。"""
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 尝试从文本中提取 JSON 数组
    import re
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse audit JSON: %s", text[:200])
    return []


def _list_reports() -> list[dict]:
    """列出已有报告。"""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    reports = []
    for f in sorted(AUDIT_DIR.glob("memory-audit-*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports.append({
                "filename": f.name,
                "time": data.get("timestamp", f.stem.replace("memory-audit-", "")),
                "issue_count": data.get("issue_count", "?"),
                "total_items": data.get("total_items", "?"),
            })
        except Exception:
            reports.append({
                "filename": f.name,
                "time": f.stem.replace("memory-audit-", ""),
                "issue_count": "?",
                "total_items": "?",
            })
    return reports
