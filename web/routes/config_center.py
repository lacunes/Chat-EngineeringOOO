"""配置中心路由 — 剧情和机器人行为参数管理。

管理 21 项参数，中文名称 + 推荐范围 + 校验。
保存 .env 前自动备份，保留未知字段和注释，只替换被管理字段。
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for

from config import settings
from web.app import _ctx, audit_log, _flash_redirect
from web.routes.auth import login_required

logger = logging.getLogger(__name__)

config_bp = Blueprint("config_center", __name__, url_prefix="/config")

# ═══════════════════════════════════════════════════════════
# 配置项定义：中文名 / .env key / 类型 / 推荐范围 / 说明
# ═══════════════════════════════════════════════════════════

CONFIG_DEFS = [
    {
        "key": "ACTIVE_WORLD",
        "name": "当前世界",
        "type": "select",
        "desc": "选择当前启用的世界设定。切换后需重启 Bot 生效。",
        "options_dynamic": True,  # 动态从 worlds/ 目录读取
    },
    {
        "key": "MODEL_NAME",
        "name": "使用模型",
        "type": "select",
        "desc": "选择 DeepSeek 模型。deepseek-chat 适合角色扮演，deepseek-reasoner 适合复杂推理。",
        "options": [
            {"value": "deepseek-chat", "label": "deepseek-chat（推荐，通用模型）"},
            {"value": "deepseek-reasoner", "label": "deepseek-reasoner（推理模型，较慢）"},
        ],
    },
    {
        "key": "MIN_REPLY_TOKENS",
        "name": "回复最短长度",
        "type": "int",
        "desc": "控制 AI 单次回复的最低 token 数。越高越不容易出现短回复，但也可能让简单场景显得啰嗦。",
        "range": (300, 700),
        "default": 700,
    },
    {
        "key": "MID_REPLY_TOKENS",
        "name": "回复常规长度",
        "type": "int",
        "desc": "大多数情况下 AI 会接近这个长度（85% 概率在此范围内随机）。",
        "range": (1000, 2400),
        "default": 2400,
    },
    {
        "key": "MAX_REPLY_TOKENS",
        "name": "回复最长长度",
        "type": "int",
        "desc": "限制单次生成的最大 token 数。15% 概率触发更长回复，但不会超过此值。",
        "range": (1800, 3600),
        "default": 3400,
    },
    {
        "key": "SPLIT_THRESHOLD",
        "name": "自动分段阈值",
        "type": "int",
        "desc": "回复超过此字符数时自动拆成多条 Telegram 消息发送。",
        "range": (1500, 3000),
        "default": 3000,
    },
    {
        "key": "CONTINUE_LIMIT",
        "name": "续写最大次数",
        "type": "int",
        "desc": "使用 /continue 时最多连续续写几次。过高可能让模型跑偏。",
        "range": (1, 5),
        "default": 7,
    },
    {
        "key": "CONTEXT_LENGTH",
        "name": "最近对话保留数量",
        "type": "int",
        "desc": "每次请求模型时携带最近多少条聊天记录。太低会失忆，太高会浪费 token。",
        "range": (40, 80),
        "default": 60,
    },
    {
        "key": "MEMORY_MAX_LENGTH",
        "name": "短期记忆最大容量",
        "type": "int",
        "desc": "本地短期记忆最多保存多少条。超过后自动压缩旧对话。",
        "range": (100, 300),
        "default": 200,
    },
    {
        "key": "LONG_MEMORY_CONTEXT_LIMIT",
        "name": "长期记忆参考上限",
        "type": "int",
        "desc": "每次回复时最多引用多少条长期记忆注入到上下文。",
        "range": (10, 40),
        "default": 12,
    },
    {
        "key": "LONG_MEMORY_MAX_ITEMS",
        "name": "长期记忆总量上限",
        "type": "int",
        "desc": "长期记忆最多保留多少条。超过后触发精炼去重合并。",
        "range": (100, 500),
        "default": 12,
    },
    {
        "key": "AUTO_MEMORY_INTERVAL",
        "name": "自动整理记忆间隔",
        "type": "int",
        "desc": "每隔多少轮对话尝试提取一次长期记忆。太小可能造成记忆污染，太大可能漏掉重要剧情。",
        "range": (8, 15),
        "default": 26,
    },
    {
        "key": "AUTO_MEMORY_LOOKBACK",
        "name": "记忆回看范围",
        "type": "int",
        "desc": "自动整理记忆时回看最近多少条对话。略大于整理间隔以避免遗漏。",
        "range": (20, 60),
        "default": 32,
    },
    {
        "key": "MEMO_SIZE_LIMIT",
        "name": "手动记忆长度限制",
        "type": "int",
        "desc": "/memo 手动添加记忆时允许的最大字数。",
        "range": (200, 1000),
        "default": 500,
    },
    {
        "key": "NPC_BASE_ACTIVATION",
        "name": "NPC 主动出现概率",
        "type": "float",
        "desc": "NPC 在合适场景中主动介入的基础概率。越高世界越热闹，越低剧情越围绕对话双方。",
        "range": (0.15, 0.35),
        "default": 0.5,
        "step": 0.05,
    },
    {
        "key": "NPC_MAX_ACTIONS_PER_CHECK",
        "name": "单次最多主动 NPC 数",
        "type": "int",
        "desc": "一轮中最多允许几个 NPC 主动行动。设 1 叙事最清晰。",
        "range": (1, 3),
        "default": 1,
    },
    {
        "key": "NPC_TIMER_INTERVAL",
        "name": "NPC 检查间隔",
        "type": "int",
        "desc": "每隔多少秒检查一次 NPC 是否主动行动。太短浪费资源，太长 NPC 显得迟钝。",
        "range": (30, 180),
        "default": 300,
    },
    {
        "key": "RELATION_EXTRACT_INTERVAL",
        "name": "关系自动分析间隔",
        "type": "int",
        "desc": "每隔多少轮对话尝试分析一次人物关系变化。太小容易被一两句话误导。",
        "range": (8, 15),
        "default": 2,
    },
    {
        "key": "TIME_AUTO_ADVANCE_ENABLED",
        "name": "是否允许自动推进时间",
        "type": "bool",
        "desc": "AI 是否能根据剧情自然推进时段。开启后每个回复都可能触发时间推进。",
    },
    {
        "key": "TIME_USER_DRIVEN_ADVANCE_ENABLED",
        "name": "是否允许用户话语推动时间",
        "type": "bool",
        "desc": "当用户说'第二天''晚上'等词时，是否自动更新时间。",
    },
    {
        "key": "TIME_LONG_SCENE_HINT_THRESHOLD",
        "name": "长场景提醒阈值",
        "type": "int",
        "desc": "同一时段持续多少轮后温和提醒可以推进时间。",
        "range": (8, 20),
        "default": 80,
    },
]


@config_bp.route("/")
@login_required
def index():
    """显示配置中心页面。"""
    ctx = _ctx()

    # 读取当前 .env 中的所有值
    current_values = _read_all_env_values()

    # 动态获取世界列表
    world_options = _get_world_options()

    return render_template(
        "config.html",
        configs=CONFIG_DEFS,
        current_values=current_values,
        world_options=world_options,
        ctx=ctx,
    )


@config_bp.route("/save", methods=["POST"])
@login_required
def save():
    """保存配置并自动备份 .env。"""
    updates: dict[str, str] = {}

    for cfg in CONFIG_DEFS:
        key = cfg["key"]
        ctype = cfg["type"]

        if ctype == "bool":
            # checkbox：勾选时传 "true"，未勾选时字段不存在
            updates[key] = "true" if request.form.get(key) else "false"
        else:
            val = (request.form.get(key) or "").strip()
            if val == "":
                continue  # 空值跳过，保持原值

            # 类型校验
            if ctype == "int":
                try:
                    int_val = int(val)
                except ValueError:
                    return _flash_redirect(url_for("config_center.index"),
                                           f"「{cfg['name']}」必须是整数，当前输入: {val}", "error")
                rng = cfg.get("range")
                if rng:
                    min_v, max_v = rng
                    if not (min_v <= int_val <= max_v):
                        logger.info("Config '%s' = %s out of range %s-%s, allowing with warning",
                                    key, int_val, min_v, max_v)
                updates[key] = str(int_val)

            elif ctype == "float":
                try:
                    float_val = float(val)
                except ValueError:
                    return _flash_redirect(url_for("config_center.index"),
                                           f"「{cfg['name']}」必须是数字，当前输入: {val}", "error")
                updates[key] = str(float_val)

            elif ctype == "select":
                updates[key] = val

    if not updates:
        return _flash_redirect(url_for("config_center.index"), "没有需要保存的配置变更", "info")

    # 备份 .env
    _backup_env()

    # 写入 .env
    try:
        _update_env_multi(updates)
    except Exception as exc:
        logger.error("Failed to save config: %s", exc)
        return _flash_redirect(url_for("config_center.index"), f"保存失败: {exc}", "error")

    audit_log("保存配置", f"{len(updates)} 项: {', '.join(updates.keys())}")
    logger.info("Web panel: saved %d config items", len(updates))
    return _flash_redirect(url_for("config_center.index"),
                           "配置已保存，部分配置可能需要重新启动进程后生效。")


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _read_all_env_values() -> dict[str, str]:
    """读取 .env 中所有被管理 key 的当前值。"""
    env_path = settings.BASE_DIR / ".env"
    result: dict[str, str] = {}
    if not env_path.exists():
        return result

    managed_keys = {cfg["key"] for cfg in CONFIG_DEFS}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in managed_keys:
            result[key] = value
    return result


def _get_world_options() -> list[dict]:
    """列出 worlds/ 目录下的世界文件作为下拉选项。"""
    worlds_dir = settings.BASE_DIR / "worlds"
    options = []
    for py_file in sorted(worlds_dir.glob("*.py")):
        name = py_file.stem
        if name.startswith("_"):
            continue
        options.append({"value": name, "label": name})
    return options


def _backup_env() -> None:
    """备份 .env 为 .env.backup.YYYYMMDD-HHMMSS。"""
    env_path = settings.BASE_DIR / ".env"
    if not env_path.exists():
        logger.warning("No .env to backup")
        return

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = settings.BASE_DIR / f".env.backup.{ts}"
    try:
        backup_path.write_bytes(env_path.read_bytes())
        logger.info("Backed up .env to %s", backup_path.name)
    except Exception as exc:
        logger.warning("Failed to backup .env: %s", exc)


def _update_env_multi(updates: dict[str, str]) -> None:
    """更新 .env 文件中多个键值对，保留未知字段和注释。

    策略：按行处理，遇到被管理的 key 则替换；未找到则追加到末尾。
    """
    env_path = settings.BASE_DIR / ".env"
    if not env_path.exists():
        # .env 不存在则创建
        lines = []
        logger.warning(".env not found, creating new one")
    else:
        lines = env_path.read_text(encoding="utf-8").splitlines()

    found_keys: set[str] = set()

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            # 保留行内注释（# 之后的部分）
            comment = ""
            if "#" in line:
                _, comment = line.split("#", 1)
                comment = "#" + comment
            lines[i] = f"{key}={updates[key]}  {comment}".rstrip()
            found_keys.add(key)

    # 未找到的 key 追加到末尾
    for key, value in updates.items():
        if key not in found_keys:
            lines.append(f"{key}={value}")

    # 确保末尾换行
    content = "\n".join(lines)
    if not content.endswith("\n"):
        content += "\n"

    env_path.write_text(content, encoding="utf-8")
