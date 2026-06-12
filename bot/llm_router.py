"""
LLM Router — 多模型供应商自动路由与故障切换。

职责：
1. 读取 providers.yaml，按 task_type + priority 选择 provider
2. 自动检测 providers.yaml 修改并热重载
3. 失败自动 fallback、冷却、额度耗尽永久跳过
4. 记录 logs/llm_usage.jsonl
5. 持久化状态到 data/provider_state.json

⚠️ 隔离原则：本模块只写 data/provider_state.json 和 providers.yaml，
绝不触碰 memory/、data/sessions/、data/memory/ 下的任何文件。
provider 切换、fallback、冷却、模式变更均不影响聊天记忆。
"""

import asyncio
import collections
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse, urlunparse

import requests
import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ── 抑制 httpx/httpcore 日志，防止泄露 Telegram Bot Token ──
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── 文件路径（延迟计算，避免导入时 BASE_DIR 未初始化）──

def _providers_yaml_path() -> Path:
    from config import settings
    return settings.BASE_DIR / "providers.yaml"

def _provider_state_path() -> Path:
    from config import settings
    return settings.BASE_DIR / "data" / "provider_state.json"

def _llm_usage_log_path() -> Path:
    from config import settings
    return settings.BASE_DIR / "logs" / "llm_usage.jsonl"

# ── 线程锁 ──
_state_lock = threading.RLock()
_usage_lock = threading.Lock()


def _backup_providers_yaml(path: Path) -> None:
    """备份 providers.yaml 到 backups/ 目录。"""
    try:
        backup_dir = path.parent.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"providers_{ts}.yaml"
        import shutil
        shutil.copy2(path, backup_path)
        logger.debug("Backed up providers.yaml to %s", backup_path)
    except Exception as e:
        logger.warning("Failed to backup providers.yaml: %s", e)


# ═══════════════════════════════════════════════════════════════
# 状态持久化
# ═══════════════════════════════════════════════════════════════

def _load_state() -> dict:
    """从 data/provider_state.json 读取运行时状态。"""
    path = _provider_state_path()
    if not path.exists():
        return _default_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 确保结构完整
        default = _default_state()
        for key in default:
            if key not in data:
                data[key] = default[key]
        # 确保每个 provider 有状态条目
        for p_name in default.get("providers", {}):
            if p_name not in data.get("providers", {}):
                data.setdefault("providers", {})[p_name] = default["providers"][p_name]
        return data
    except Exception:
        logger.warning("Failed to load provider_state.json, using defaults")
        return _default_state()


def _default_state() -> dict:
    """返回最小默认状态（不再写死 provider 名称）。

    具体 provider 的状态由 _load_and_merge_state() 根据 providers.yaml 自动补齐。
    """
    return {
        "mode": "auto",
        "manual_provider": None,
        "providers": {},
        "last_fallback_time": None,
        "last_fallback_reason": None,
    }


def _save_state(state: dict) -> None:
    """原子保存运行时状态到 data/provider_state.json。"""
    path = _provider_state_path()
    from bot.safe_io import atomic_write_json
    with _state_lock:
        atomic_write_json(path, state)


# ═══════════════════════════════════════════════════════════════
# 用量日志
# ═══════════════════════════════════════════════════════════════

def _log_llm_usage(entry: dict) -> None:
    """记录 LLM 调用到 logs/llm_usage.jsonl。只记录统计信息，不记录 prompt/回复/API Key。"""
    path = _llm_usage_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _usage_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# 错误分类（v2 — 6 种明确类型）
# ═══════════════════════════════════════════════════════════════

# 余额/额度耗尽
_QUOTA_EXHAUSTED_KEYWORDS = [
    "balance is insufficient",
    "your account balance is insufficient",
    "insufficient balance",
    "account balance is insufficient",
    "insufficient_quota", "insufficient quota",
    "quota_exceeded", "quota exceeded",
    "余额不足", "额度耗尽", "额度不足",
    "out of credits", "credits exhausted",
    "resource exhausted", "exceeded your current quota",
    "free quota", "free trial", "trial ended",
    "billing",
]

# 限流
_RATE_LIMIT_KEYWORDS = [
    "rate limit", "too many requests",
    "rpm", "tpm",
]

# 服务临时不可用
_UNAVAILABLE_KEYWORDS = [
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "upstream error",
]

# 免费通道无可用线路
_NO_CHANNEL_KEYWORDS = [
    "no available channel",
    "under group free",
    "no channel",
]

# 模型名错误
_MODEL_NOT_FOUND_KEYWORDS = [
    "model_not_found",
    "model does not exist",
    "unknown model",
    "model not found",
]

# Key/权限错误
_AUTH_ERROR_KEYWORDS = [
    "invalid api key",
    "permission denied",
    "unauthorized",
    "forbidden",
]

# 可重试的 HTTP 状态码
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_quota_exhausted(text: str) -> bool:
    """检查错误文本是否匹配余额/额度耗尽关键词。"""
    return any(kw in text for kw in _QUOTA_EXHAUSTED_KEYWORDS)


def _classify_error(exc: Exception) -> tuple[str, bool]:
    """
    分类错误类型，返回 (error_type, is_retryable)。

    error_type:
        quota_exhausted  — 余额/额度耗尽，应永久跳过
        rate_limited     — 限流，冷却 30-120 秒
        unavailable      — 服务临时不可用，冷却 60-300 秒
        no_channel       — 免费通道不可用，冷却 10-30 分钟
        model_not_found  — 模型不存在/配置错误
        auth_error       — Key/权限错误，长期跳过
        http_{code}      — 未分类的 HTTP 状态码
        timeout          — 请求超时
        connection       — 连接失败
        format_error     — 回复格式错误
        empty_reply      — 返回空回复
        unknown          — 未知错误
    """
    if isinstance(exc, asyncio.TimeoutError):
        return ("timeout", False)
    if isinstance(exc, requests.exceptions.Timeout):
        return ("timeout", True)
    if isinstance(exc, requests.exceptions.ConnectionError):
        return ("connection", True)

    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        error_body = ""
        try:
            error_body = exc.response.text if exc.response is not None else ""
        except Exception:
            pass
        lower = error_body.lower()

        # 1. 余额/额度耗尽 (403 + balance insufficient 等)
        if _is_quota_exhausted(lower):
            return ("quota_exhausted", False)

        # 2. 限流 (429)
        if status == 429 or any(kw in lower for kw in _RATE_LIMIT_KEYWORDS):
            return ("rate_limited", True)

        # 3. Key/权限错误 (401/403 + auth keywords)
        if status in (401, 403) and any(kw in lower for kw in _AUTH_ERROR_KEYWORDS):
            return ("auth_error", False)

        # 4. 模型不存在
        if any(kw in lower for kw in _MODEL_NOT_FOUND_KEYWORDS):
            return ("model_not_found", False)

        # 5. 免费通道不可用
        if any(kw in lower for kw in _NO_CHANNEL_KEYWORDS):
            return ("no_channel", False)

        # 6. 服务临时不可用 (5xx)
        if status in _RETRYABLE_STATUS_CODES:
            if any(kw in lower for kw in _UNAVAILABLE_KEYWORDS):
                return ("unavailable", True)
            return (f"http_{status}", True)

        return (f"http_{status}", False)

    # 非 HTTP 异常
    if isinstance(exc, ValueError):
        error_msg = str(exc).lower()
        if "空回复" in error_msg or "empty" in error_msg or "空 choices" in error_msg:
            return ("empty_reply", False)
        return ("format_error", False)
    if isinstance(exc, (KeyError, TypeError)):
        return ("format_error", False)

    return ("unknown", False)


# ═══════════════════════════════════════════════════════════════
# purpose → task_type 映射
# ═══════════════════════════════════════════════════════════════

_PURPOSE_TO_TASK_TYPE = {
    "main_chat": "chat",
    "continue": "chat",
    "memory_compress": "memory",
    "memory_extract": "memory",
    "memory_refine": "memory",
    "relation_extract": "relation",
    "day_summary": "summary",
    "story_summary": "summary",
}


def _purpose_to_task_type(purpose: str) -> str:
    """将调用方传入的 purpose 映射为标准 task_type。"""
    task_type = _PURPOSE_TO_TASK_TYPE.get(purpose)
    if task_type:
        return task_type
    # 如果 purpose 本身就是 chat/memory/relation/summary/background，直接使用
    if purpose in ("chat", "memory", "relation", "summary", "background"):
        return purpose
    # 默认归为后台任务
    return "background"


# ═══════════════════════════════════════════════════════════════
# LLMRouter
# ═══════════════════════════════════════════════════════════════

class LLMRouter:
    """多供应商 LLM 路由器。

    使用方式：
        router = LLMRouter(notify_callback=send_telegram_to_admin)
        reply, finish = await router.chat(messages, purpose="main_chat")
    """

    def __init__(self, notify_callback: Optional[Callable] = None):
        """初始化路由器。

        Args:
            notify_callback: 可选异步回调 async def callback(text: str)，用于给管理员发 Telegram 提醒。
        """
        self._notify = notify_callback
        self._providers_mtime: float = 0.0  # providers.yaml 最后修改时间
        self._providers_config: list[dict] = []  # 当前生效的 provider 列表
        self._providers_by_name: dict[str, dict] = {}  # name → provider 快速查找
        self._state: dict = _default_state()  # 运行时状态
        self._call_history: collections.deque = collections.deque(maxlen=20)  # 最近 20 次 LLM 调用记录

        # 确保 .env 已加载（settings 模块在 import 时已调用 load_dotenv）
        self._reload_providers()
        self._load_and_merge_state()

    # ── 配置热重载 ──

    def _reload_providers(self) -> None:
        """读取 providers.yaml 并解析为内部结构。"""
        path = _providers_yaml_path()
        if not path.exists():
            logger.warning("providers.yaml not found at %s, using empty config", path)
            self._providers_config = []
            self._providers_by_name = {}
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            raw = data.get("providers", []) if isinstance(data, dict) else []
            self._providers_config = raw
            self._providers_by_name = {}
            for p in raw:
                name = p.get("name", "")
                if name:
                    self._providers_by_name[name] = p
            self._providers_mtime = path.stat().st_mtime
            logger.debug("Loaded %d providers from providers.yaml", len(raw))
        except Exception as e:
            logger.warning("Failed to reload providers.yaml: %s", e)

    def _check_reload(self) -> None:
        """检查 providers.yaml 是否已修改，是则自动重载并合并状态。"""
        path = _providers_yaml_path()
        if not path.exists():
            return
        try:
            mtime = path.stat().st_mtime
            if mtime != self._providers_mtime:
                logger.info("providers.yaml changed, hot-reloading...")
                self._reload_providers()
                self._load_and_merge_state()
        except Exception:
            pass

    # ── 状态管理 ──

    def _load_and_merge_state(self) -> None:
        """加载持久化状态，并与当前 providers 配置合并。

        - 自动补齐新增 provider 的状态条目
        - 清理 providers.yaml 中已删除的 provider 状态
        """
        saved = _load_state()
        self._state = saved

        # 当前 YAML 中的 provider 名称集合
        current_names = {p.get("name", "") for p in self._providers_config if p.get("name")}

        # 确保状态中每个 provider 都有条目
        for name in current_names:
            if name and name not in self._state.get("providers", {}):
                self._state.setdefault("providers", {})[name] = {
                    "consecutive_failures": 0,
                    "cooldown_until": None,
                    "exhausted": False,
                    "last_failure_reason": None,
                    "last_failure_time": None,
                    "last_error_type": None,
                }

        # 清理 YAML 中已不存在的 provider 状态
        state_providers = self._state.get("providers", {})
        stale = [name for name in state_providers if name not in current_names]
        for name in stale:
            logger.info("Cleaning up stale provider state: %s", name)
            del state_providers[name]

        _save_state(self._state)

    def _get_provider_state(self, name: str) -> dict:
        """获取某个 provider 的状态（线程安全）。"""
        with _state_lock:
            return dict(self._state.get("providers", {}).get(name, {}))

    def _update_provider_state(self, name: str, updates: dict) -> None:
        """更新某个 provider 的状态。"""
        with _state_lock:
            providers = self._state.setdefault("providers", {})
            if name not in providers:
                providers[name] = {
                    "consecutive_failures": 0,
                    "cooldown_until": None,
                    "exhausted": False,
                    "last_failure_reason": None,
                    "last_failure_time": None,
                }
            providers[name].update(updates)
            _save_state(self._state)

    def _record_success(self, name: str) -> None:
        """调用成功后清零连续失败计数。"""
        self._update_provider_state(name, {
            "consecutive_failures": 0,
            "cooldown_until": None,
            "last_failure_reason": None,
            "last_failure_time": None,
        })

    def _record_failure(self, name: str, error_type: str, error_message: str) -> str:
        """
        记录一次失败。返回处理方式标记：
            "exhausted" — 永久跳过
            "cooldown"  — 进入冷却
            "recorded"  — 仅记录计数

        根据错误类型设置不同的冷却时间：
            rate_limited  → 60-120s
            unavailable   → 120-300s
            no_channel    → 600-1800s (10-30分钟)
            其他连续失败   → 配置的 cooldown_seconds (默认300s)
        """
        now_ts = time.time()
        state = self._get_provider_state(name)

        # ── 永久跳过类型 ──
        if error_type in ("auth_error", "model_not_found"):
            self._update_provider_state(name, {
                "exhausted": True,
                "consecutive_failures": 0,
                "cooldown_until": None,
                "last_failure_reason": f"[{error_type}] {error_message[:200]}",
                "last_failure_time": datetime.now(timezone.utc).isoformat(),
                "last_error_type": error_type,
            })
            label = {"auth_error": "Key/权限错误", "model_not_found": "模型不存在"}.get(error_type, error_type)
            self._notify_admin(f"⚠️ {name} {label}，已永久跳过。请在 Web 面板手动清除状态。")
            return "exhausted"

        # quota_exhausted：检查 disable_on_quota_exhausted 配置
        if error_type == "quota_exhausted":
            disable_flag = self._get_provider_config(name, "disable_on_quota_exhausted", True)
            if disable_flag:
                self._update_provider_state(name, {
                    "exhausted": True,
                    "consecutive_failures": 0,
                    "cooldown_until": None,
                    "last_failure_reason": f"[{error_type}] {error_message[:200]}",
                    "last_failure_time": datetime.now(timezone.utc).isoformat(),
                    "last_error_type": error_type,
                })
                self._notify_admin(f"⚠️ {name} 额度耗尽，已永久跳过。请在 Web 面板手动清除状态。")
                return "exhausted"
            # disable_on_quota_exhausted=false：按普通失败冷却处理

        # ── 冷却类型（连续失败或特定错误）──
        failures = state.get("consecutive_failures", 0) + 1

        # 根据错误类型确定冷却时间
        if error_type == "rate_limited":
            cooldown_sec = 90  # 30-120s 中点
        elif error_type == "unavailable":
            cooldown_sec = 180  # 120-300s 中点
        elif error_type == "no_channel":
            cooldown_sec = 900  # 10-30分钟中点
        else:
            cooldown_sec = self._get_provider_config(name, "cooldown_seconds", 300)

        max_fail = self._get_provider_config(name, "max_consecutive_failures", 3)

        # 特定类型立即冷却，否则累积到阈值
        if error_type in ("rate_limited", "unavailable", "no_channel"):
            cooldown_until = now_ts + cooldown_sec
            self._update_provider_state(name, {
                "consecutive_failures": failures,
                "cooldown_until": cooldown_until,
                "last_failure_reason": f"[{error_type}] {error_message[:200]}",
                "last_failure_time": datetime.now(timezone.utc).isoformat(),
                "last_error_type": error_type,
            })
            reason_map = {"rate_limited": "限流", "unavailable": "服务临时不可用", "no_channel": "免费通道不可用"}
            self._notify_admin(f"⚠️ {name} {reason_map.get(error_type, error_type)}，冷却 {cooldown_sec} 秒。")
            return "cooldown"

        if failures >= max_fail:
            cooldown_until = now_ts + cooldown_sec
            self._update_provider_state(name, {
                "consecutive_failures": failures,
                "cooldown_until": cooldown_until,
                "last_failure_reason": f"[{error_type}] {error_message[:200]}",
                "last_failure_time": datetime.now(timezone.utc).isoformat(),
                "last_error_type": error_type,
            })
            self._notify_admin(f"⚠️ {name} 连续失败 {failures} 次，冷却 {cooldown_sec} 秒。")
            return "cooldown"

        self._update_provider_state(name, {
            "consecutive_failures": failures,
            "last_failure_reason": f"[{error_type}] {error_message[:200]}",
            "last_failure_time": datetime.now(timezone.utc).isoformat(),
            "last_error_type": error_type,
        })
        return "recorded"

    def _mark_exhausted(self, name: str) -> None:
        """标记 provider 为额度耗尽（永久跳过）。"""
        self._update_provider_state(name, {
            "exhausted": True,
            "consecutive_failures": 0,
            "cooldown_until": None,
        })

    def _clear_exhausted(self, name: str) -> None:
        """清除 exhausted 标记。"""
        self._update_provider_state(name, {
            "exhausted": False,
            "consecutive_failures": 0,
            "cooldown_until": None,
        })

    def _get_provider_config(self, name: str, key: str, default=None):
        """从 providers 配置中获取某个字段。"""
        p = self._providers_by_name.get(name, {})
        return p.get(key, default)

    # ── 通知 ──

    def _notify_admin(self, text: str) -> None:
        """异步通知管理员（通过回调）。如果回调未设置则只写日志。"""
        logger.info("[AdminNotify] %s", text)
        if self._notify:
            try:
                # 可能在同步上下文中调用，尝试用 asyncio 处理
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._notify(text))
                except RuntimeError:
                    # 不在异步上下文中，同步执行
                    pass
            except Exception:
                pass

    # ── 公开方法：获取所有 provider 状态（供 Telegram 命令使用）──

    def get_status_text(self) -> str:
        """生成 /provider status 的展示文本。"""
        self._check_reload()
        mode = self._state.get("mode", "auto")
        manual = self._state.get("manual_provider")

        lines = [
            f"📡 当前模式：{'🔧 手动' if mode == 'manual' else '🔄 自动'}",
        ]
        if mode == "manual" and manual:
            lines.append(f"   手动优先：{manual}")

        lines.append("")
        lines.append("── Provider 状态 ──")

        # 按 priority 排序
        sorted_providers = sorted(
            self._providers_config,
            key=lambda p: p.get("priority", 99),
        )
        for p in sorted_providers:
            name = p.get("name", "?")
            enabled = p.get("enabled", False)
            cfg_enabled = "✅" if enabled else "❌"
            st = self._get_provider_state(name)
            exhausted = st.get("exhausted", False)
            cooldown_until = st.get("cooldown_until")
            failures = st.get("consecutive_failures", 0)

            status_parts = [cfg_enabled]
            if exhausted:
                status_parts.append("🚫 额度耗尽")
            elif cooldown_until:
                remaining = max(0, int(cooldown_until - time.time()))
                if remaining > 0:
                    status_parts.append(f"⏳ 冷却中 ({remaining}s)")
                else:
                    status_parts.append("✅ 可用")
            else:
                status_parts.append("✅ 可用")

            if failures > 0:
                status_parts.append(f"失败×{failures}")

            model = p.get("model", "?")
            priority = p.get("priority", "?")
            task_types = ", ".join(p.get("task_types", []))
            api_key_env = p.get("api_key_env", "")
            has_key = bool(os.getenv(api_key_env)) if api_key_env else False
            key_status = "🔑" if has_key else "❌缺Key"

            lines.append(
                f"  {name} (P{priority}) {key_status} {' '.join(status_parts)}\n"
                f"    模型: {model}  任务: [{task_types}]"
            )

        # 最近 fallback
        last_ft = self._state.get("last_fallback_time")
        last_fr = self._state.get("last_fallback_reason")
        if last_ft:
            lines.append(f"\n📋 最近 fallback: {last_ft} — {last_fr}")

        return "\n".join(lines)

    def set_mode_auto(self) -> None:
        """切换到自动模式。"""
        self._state["mode"] = "auto"
        self._state["manual_provider"] = None
        _save_state(self._state)
        logger.info("Provider mode set to auto")

    def set_mode_manual(self, provider_name: str) -> None:
        """切换到手动模式，优先使用指定 provider。"""
        self._state["mode"] = "manual"
        self._state["manual_provider"] = provider_name
        _save_state(self._state)
        logger.info("Provider mode set to manual, prefer: %s", provider_name)

    def enable_provider(self, name: str) -> bool:
        """启用 provider 并清除所有负面状态。返回是否成功。"""
        if name not in self._providers_by_name:
            return False
        # 修改 providers.yaml 中的 enabled 字段
        self._set_config_enabled(name, True)
        self._clear_exhausted(name)
        logger.info("Provider %s enabled and state cleared", name)
        return True

    def disable_provider(self, name: str) -> bool:
        """禁用 provider。返回是否成功。"""
        if name not in self._providers_by_name:
            return False
        self._set_config_enabled(name, False)
        logger.info("Provider %s disabled", name)
        return True

    def _set_config_enabled(self, name: str, enabled: bool) -> None:
        """修改 providers.yaml 中某个 provider 的 enabled 字段（原子写入）。"""
        path = _providers_yaml_path()
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or "providers" not in data:
                return
            for p in data["providers"]:
                if p.get("name") == name:
                    p["enabled"] = enabled
                    break
            # 原子写入（自动备份 + tmp + fsync + 校验 + replace）
            from bot.safe_io import atomic_write_yaml
            atomic_write_yaml(path, data)
            # 立即重载
            self._reload_providers()
            self._load_and_merge_state()
        except Exception as e:
            logger.warning("Failed to update providers.yaml for %s: %s", name, e)

    def save_providers_config(self, providers_list: list[dict]) -> bool:
        """用新配置覆盖 providers.yaml（原子写入，带备份+校验）。返回是否成功。"""
        path = _providers_yaml_path()
        if not path.exists():
            return False
        try:
            data = {"providers": providers_list}
            from bot.safe_io import atomic_write_yaml
            atomic_write_yaml(path, data)
            # 立即重载
            self._reload_providers()
            self._load_and_merge_state()
            logger.info("providers.yaml saved and reloaded")
            return True
        except Exception as e:
            logger.warning("Failed to save providers.yaml: %s", e)
            return False

    def clear_provider_state(self, name: str) -> bool:
        """清除某个 provider 的 failures/cooldown/exhausted。返回是否成功。"""
        if name not in self._providers_by_name:
            return False
        self._clear_exhausted(name)
        logger.info("Provider %s state cleared", name)
        return True

    # ── 核心调用逻辑 ──

    async def chat(
        self,
        messages: list,
        max_tokens: int = None,
        temperature: float = 0.85,
        purpose: str = "main_chat",
    ) -> tuple[str, str]:
        """
        调用 LLM API 进行对话补全。
        签名与 DeepSeekClient.chat() 完全兼容。

        Args:
            messages: OpenAI 格式消息列表
            max_tokens: 最大生成 token 数
            temperature: 生成温度
            purpose: 调用用途标签

        Returns:
            (reply_text, finish_reason)
        """
        from bot.utils import get_reply_length

        if max_tokens is None:
            max_tokens = get_reply_length()

        logger.info("[LLMRouter] 收到请求 purpose=%s max_tokens=%s", purpose, max_tokens)

        # 检查配置文件是否更新
        self._check_reload()

        task_type = _purpose_to_task_type(purpose)
        start_time = time.time()
        error_type = ""
        error_message = ""
        fallback_from = ""
        fallback_to = ""
        usage = None
        total_retries = 0

        # 选择候选 provider 列表
        candidates = self._select_candidates(task_type)
        if not candidates:
            raise RuntimeError("没有可用的模型供应商，请检查 providers.yaml 和 API Key 配置。")

        logger.info("[LLMRouter] 候选 providers: %s (task_type=%s)", candidates, task_type)

        # 逐个尝试
        for idx, p_name in enumerate(candidates):
            provider_config = self._providers_by_name.get(p_name, {})
            provider_state = self._get_provider_state(p_name)

            # 跳过额度耗尽的
            if provider_state.get("exhausted"):
                logger.debug("Provider %s is exhausted, skipping", p_name)
                continue

            # 跳过冷却中的
            cooldown_until = provider_state.get("cooldown_until")
            if cooldown_until:
                now_ts = time.time()
                if now_ts < cooldown_until:
                    remaining = int(cooldown_until - now_ts)
                    logger.debug("Provider %s in cooldown (%ds remaining), skipping", p_name, remaining)
                    continue
                else:
                    # 冷却已结束，自动恢复
                    self._update_provider_state(p_name, {
                        "cooldown_until": None,
                        "consecutive_failures": 0,
                    })

            # 检查 API Key
            api_key_env = provider_config.get("api_key_env", "")
            api_key = os.getenv(api_key_env, "").strip() if api_key_env else ""
            if not api_key:
                logger.debug("Provider %s has no API key (env: %s), skipping", p_name, api_key_env)
                continue

            # 如果是第一次不是首选，记录 fallback
            if idx > 0 and not fallback_from:
                fallback_from = candidates[0]
                fallback_to = p_name
                self._state["last_fallback_time"] = datetime.now(timezone.utc).isoformat()
                self._state["last_fallback_reason"] = f"{fallback_from} 失败，切换到 {fallback_to}"
                _save_state(self._state)
                self._notify_admin(
                    f"⚠️ {fallback_from} 调用失败，已切换到 {fallback_to}。"
                )

            # 确定超时时间
            timeout = provider_config.get(
                "timeout_chat_seconds" if task_type == "chat" else "timeout_background_seconds",
                60,
            )
            max_retries = provider_config.get("max_retries", 1)

            # 尝试调用（含重试）
            success = False
            last_error: Exception | None = None
            attempt_reply = ""
            attempt_finish = ""

            for retry in range(max_retries + 1):
                total_retries = retry
                try:
                    logger.info(
                        "[LLMRouter] API 请求开始 provider=%s model=%s retry=%s/%s",
                        p_name, provider_config.get("model", ""), retry, max_retries,
                    )
                    attempt_reply, attempt_finish, usage = await self._call_provider(
                        provider_config=provider_config,
                        api_key=api_key,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=timeout,
                    )
                    logger.info("[LLMRouter] API 请求结束 provider=%s success=True", p_name)
                    success = True
                    break
                except Exception as exc:
                    last_error = exc
                    error_type, is_retryable = _classify_error(exc)
                    error_message = str(exc)[:200]

                    # 永久跳过类型：不重试
                    if error_type in ("quota_exhausted", "auth_error", "model_not_found"):
                        logger.warning(
                            "Provider %s %s: %s", p_name, error_type, error_message,
                        )
                        break

                    # 免费通道不可用：不重试该 provider，直接 fallback
                    if error_type == "no_channel":
                        logger.warning(
                            "Provider %s no_channel: %s", p_name, error_message,
                        )
                        break

                    # 不可重试的错误
                    if not is_retryable:
                        logger.warning(
                            "Provider %s non-retryable error: %s — %s",
                            p_name, error_type, error_message,
                        )
                        break

                    # 可重试
                    logger.warning(
                        "Provider %s failed (%s/%s): %s — %s",
                        p_name, retry + 1, max_retries + 1, error_type, error_message,
                    )
                    if retry < max_retries:
                        await asyncio.sleep(2 ** retry)

            if success:
                # 成功！记录用量、清零失败计数
                logger.info("[LLMRouter] 开始记录 provider state: %s (success)", p_name)
                self._record_success(p_name)
                logger.info("[LLMRouter] state 保存完成: %s", p_name)

                # 构建日志条目
                log_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "task_type": task_type,
                    "purpose": purpose,
                    "provider": p_name,
                    "model": provider_config.get("model", ""),
                    "success": True,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "finish_reason": attempt_finish,
                    "latency_ms": round((time.time() - start_time) * 1000),
                    "prompt_tokens": usage.get("prompt_tokens", 0) if usage else 0,
                    "completion_tokens": usage.get("completion_tokens", 0) if usage else 0,
                    "total_tokens": usage.get("total_tokens", 0) if usage else 0,
                    "error_type": "",
                    "error_message": "",
                    "fallback_from": fallback_from,
                    "fallback_to": fallback_to,
                    "retry_count": total_retries,
                }
                _log_llm_usage(log_entry)
                self._call_history.append(log_entry)
                logger.info("LLM call succeeded via %s (%s)", p_name, purpose)
                logger.info("[LLMRouter] 准备返回 reply provider=%s latency=%sms",
                           p_name, round((time.time() - start_time) * 1000))
                return attempt_reply, attempt_finish

            # 失败：记录状态
            logger.info("[LLMRouter] 开始记录 provider state: %s (failure type=%s)", p_name, error_type)
            result = self._record_failure(p_name, error_type, error_message)
            logger.info("[LLMRouter] state 保存完成: %s result=%s", p_name, result)

        # ── 所有 provider 都失败了 ──
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_type": task_type,
            "purpose": purpose,
            "provider": candidates[-1] if candidates else "none",
            "model": "",
            "success": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "finish_reason": "",
            "latency_ms": round((time.time() - start_time) * 1000),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "error_type": error_type,
            "error_message": error_message[:200],
            "fallback_from": fallback_from,
            "fallback_to": "",
            "retry_count": total_retries,
        }
        _log_llm_usage(log_entry)
        self._call_history.append(log_entry)

        self._notify_admin(
            f"🚨 所有模型供应商均调用失败！最后错误: {error_type} — {error_message[:100]}"
        )

        raise RuntimeError("所有模型供应商暂时不可用，请稍后再试。") from (last_error if last_error else None)

    def _select_candidates(self, task_type: str) -> list[str]:
        """
        根据 task_type 和 provider 状态选择候选列表（按优先级排序）。
        手动模式下，手动指定的 provider 排到最前面。
        """
        mode = self._state.get("mode", "auto")
        manual_name = self._state.get("manual_provider")

        # 筛选支持该 task_type 且 enabled 的 provider
        eligible = []
        for p in self._providers_config:
            name = p.get("name", "")
            if not name:
                continue
            if not p.get("enabled", False):
                continue
            task_types = p.get("task_types", [])
            # 支持显式 task_type 或 "background" 通配
            if task_type not in task_types and "background" not in task_types:
                continue
            eligible.append(p)

        # 按 priority 排序
        eligible.sort(key=lambda p: p.get("priority", 99))

        # 手动模式：把手动指定的 provider 提到最前面
        if mode == "manual" and manual_name:
            manual_p = None
            others = []
            for p in eligible:
                if p.get("name") == manual_name:
                    manual_p = p
                else:
                    others.append(p)
            if manual_p:
                eligible = [manual_p] + others

        return [p.get("name", "") for p in eligible]

    async def _call_provider(
        self,
        provider_config: dict,
        api_key: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> tuple[str, str, dict | None]:
        """执行一次 provider API 调用。返回 (reply, finish_reason, usage)。"""

        base_url = provider_config.get("base_url", "")
        model = provider_config.get("model", "")
        thinking_enabled = provider_config.get("thinking_enabled", False)
        provider_name = provider_config.get("name", "unknown")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # OpenRouter 需要额外的 HTTP-Referer 头
        if "openrouter" in base_url.lower():
            headers["HTTP-Referer"] = "https://t.me/roleplay_bot"
            headers["X-Title"] = "RoleplayBot"

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # thinking / reasoning 配置
        if not thinking_enabled:
            # 智谱 GLM 和 DeepSeek 都支持 thinking: {type: "disabled"}
            # OpenRouter 对 Qwen 也支持，但如果报错会 fallback
            payload["thinking"] = {"type": "disabled"}

        try:
            response = await asyncio.to_thread(
                requests.post,
                base_url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()

            if "choices" not in data or not data["choices"]:
                raise ValueError(f"Provider {provider_name} 返回了空 choices")

            choice = data["choices"][0]
            message = choice.get("message", {})
            reply = (message.get("content") or "").strip()

            if not reply:
                raise ValueError(f"Provider {provider_name} 返回了空回复")

            finish_reason = choice.get("finish_reason", "stop")
            usage = data.get("usage")
            return reply, finish_reason, usage

        except requests.exceptions.HTTPError as exc:
            # 特殊处理：如果是 thinking 字段导致的错误，移除 thinking 后重试一次
            if exc.response is not None and exc.response.status_code == 400:
                error_body = ""
                try:
                    error_body = exc.response.text
                except Exception:
                    pass
                # 如果错误与 thinking 相关，且 payload 中有 thinking，移除后重试
                if "thinking" in error_body.lower() and "thinking" in payload:
                    logger.debug("Provider %s rejected thinking field, retrying without it", provider_name)
                    payload.pop("thinking", None)
                    response = await asyncio.to_thread(
                        requests.post,
                        base_url,
                        headers=headers,
                        json=payload,
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    data = response.json()
                    choice = data["choices"][0]
                    message = choice.get("message", {})
                    reply = (message.get("content") or "").strip()
                    if not reply:
                        raise ValueError(f"Provider {provider_name} 返回了空回复（去掉thinking后）")
                    finish_reason = choice.get("finish_reason", "stop")
                    usage = data.get("usage")
                    return reply, finish_reason, usage
            raise

    # ── 测试连接 ──

    def test_connection(self, provider_name: str) -> dict:
        """同步测试某个 provider 的连接。

        发送固定消息 "请只回复 OK"，返回结果字典：
        {
            "ok": bool,
            "reply": str,
            "latency_ms": int,
            "error": str,
            "model": str,
        }
        """
        p_config = self._providers_by_name.get(provider_name)
        if not p_config:
            return {"ok": False, "reply": "", "latency_ms": 0,
                    "error": f"Provider '{provider_name}' 不存在", "model": ""}

        api_key_env = p_config.get("api_key_env", "")
        api_key = os.getenv(api_key_env, "").strip() if api_key_env else ""
        if not api_key:
            return {"ok": False, "reply": "", "latency_ms": 0,
                    "error": f"缺少 API Key（环境变量 {api_key_env}）", "model": p_config.get("model", "")}

        model = p_config.get("model", "")
        base_url = p_config.get("base_url", "")
        timeout = p_config.get("timeout_chat_seconds", 30)

        messages = [{"role": "user", "content": "请只回复 OK"}]
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if "openrouter" in base_url.lower():
            headers["HTTP-Referer"] = "https://t.me/roleplay_bot"
            headers["X-Title"] = "RoleplayBot"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 10,
        }
        # 关闭思考模式
        if not p_config.get("thinking_enabled", False):
            payload["thinking"] = {"type": "disabled"}

        start = time.time()
        try:
            resp = requests.post(base_url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if "choices" in data and data["choices"]:
                reply = (data["choices"][0].get("message", {}).get("content") or "").strip()
            else:
                reply = "(空回复)"
            latency = round((time.time() - start) * 1000)
            return {"ok": True, "reply": reply, "latency_ms": latency, "error": "", "model": model}
        except Exception as exc:
            latency = round((time.time() - start) * 1000)
            return {"ok": False, "reply": "", "latency_ms": latency,
                    "error": str(exc)[:300], "model": model}

    # ── 仪表盘状态（供 Web 面板和 Telegram /status 统一使用）──

    def get_dashboard_status(self) -> dict:
        """返回仪表盘所需的模型/Provider 实时状态。

        所有显示层（仪表盘、顶部栏、Telegram /status）必须从此方法读取，
        不再使用 settings.MODEL_NAME。
        """
        self._check_reload()
        call_history = list(self._call_history)

        last_success = None
        for call in reversed(call_history):
            if call.get("success"):
                last_success = call
                break

        # 当前活跃 provider：最后一次成功调用的 provider
        current_provider = last_success["provider"] if last_success else None
        current_model = last_success["model"] if last_success else None

        return {
            "provider_mode": self._state.get("mode", "auto"),
            "manual_provider": self._state.get("manual_provider"),
            "current_provider": current_provider,
            "current_model": current_model,
            "last_success_provider": current_provider,
            "last_success_model": current_model,
            "last_fallback_reason": self._state.get("last_fallback_reason"),
            "last_fallback_time": self._state.get("last_fallback_time"),
        }

    # ── 调用历史 ──

    def get_call_history(self) -> list[dict]:
        """获取最近 20 次 LLM 调用记录。"""
        return list(self._call_history)

    # ── 获取 provider 完整信息（供 Web 面板使用）──

    def get_provider_list(self) -> list[dict]:
        """获取所有 provider 配置和状态的合并列表（供 Web 面板使用）。"""
        self._check_reload()
        result = []
        for p in self._providers_config:
            name = p.get("name", "")
            st = self._get_provider_state(name)
            api_key_env = p.get("api_key_env", "")
            has_key = bool(os.getenv(api_key_env)) if api_key_env else False

            # 冷却剩余秒数
            cooldown_until = st.get("cooldown_until")
            cooldown_remaining = 0
            if cooldown_until:
                cooldown_remaining = max(0, int(cooldown_until - time.time()))

            result.append({
                "name": name,
                "enabled": p.get("enabled", False),
                "priority": p.get("priority", 99),
                "model": p.get("model", ""),
                "base_url": p.get("base_url", ""),
                "task_types": p.get("task_types", []),
                "has_api_key": has_key,
                "api_key_env": api_key_env,
                "timeout_chat_seconds": p.get("timeout_chat_seconds", 60),
                "timeout_background_seconds": p.get("timeout_background_seconds", 30),
                "max_retries": p.get("max_retries", 1),
                "cooldown_seconds": p.get("cooldown_seconds", 300),
                "max_consecutive_failures": p.get("max_consecutive_failures", 3),
                "disable_on_quota_exhausted": p.get("disable_on_quota_exhausted", True),
                "thinking_enabled": p.get("thinking_enabled", False),
                "consecutive_failures": st.get("consecutive_failures", 0),
                "cooldown_until": cooldown_until,
                "cooldown_remaining": cooldown_remaining,
                "exhausted": st.get("exhausted", False),
                "last_failure_reason": st.get("last_failure_reason"),
                "last_failure_time": st.get("last_failure_time"),
                "last_error_type": st.get("last_error_type"),
            })
        return result

    # ── 从 /v1/models 获取模型列表 ──

    @staticmethod
    def fetch_models_from_api(base_url: str, api_key: str) -> dict:
        """从 API 的 /v1/models 端点获取可用模型列表。

        根据 base_url 自动推导 models 端点：
        - .../v1/chat/completions → .../v1/models
        - .../api/paas/v4/chat/completions → .../api/paas/v4/models
        - 其他格式 → 同 origin 的 /v1/models

        Returns:
            {"ok": bool, "models": [str, ...], "error": str}
        """
        if not base_url or not api_key:
            return {"ok": False, "models": [], "error": "base_url 和 api_key 不能为空"}

        # 推导 models URL
        parsed = urlparse(base_url)
        path = parsed.path
        if path.endswith("/chat/completions"):
            models_path = path[:-len("/chat/completions")] + "/models"
        elif path.endswith("/completions"):
            models_path = path[:-len("/completions")] + "/models"
        else:
            # Fallback: 替换路径为 /v1/models
            models_path = "/v1/models"

        models_url = urlunparse((
            parsed.scheme, parsed.netloc, models_path,
            parsed.params, parsed.query, parsed.fragment,
        ))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.get(models_url, headers=headers, timeout=15)
            resp.raise_for_status()
            resp_data = resp.json()

            # 兼容 OpenAI / 中转站 格式: {"object": "list", "data": [{"id": "...", ...}]}
            models = []
            if isinstance(resp_data, dict) and "data" in resp_data:
                for item in resp_data["data"]:
                    if isinstance(item, dict) and "id" in item:
                        models.append(str(item["id"]))
            elif isinstance(resp_data, list):
                # 某些中转站直接返回字符串列表
                for item in resp_data:
                    if isinstance(item, str):
                        models.append(item)
                    elif isinstance(item, dict) and "id" in item:
                        models.append(str(item["id"]))

            if not models:
                return {"ok": False, "models": [], "error": "返回了空的模型列表"}

            models.sort()
            return {"ok": True, "models": models, "error": ""}

        except requests.exceptions.HTTPError as e:
            detail = ""
            try:
                detail = e.response.text[:300] if e.response is not None else ""
            except Exception:
                pass
            return {"ok": False, "models": [], "error": f"HTTP {e.response.status_code if e.response is not None else '?'}: {detail}"}
        except requests.exceptions.Timeout:
            return {"ok": False, "models": [], "error": "请求超时"}
        except requests.exceptions.ConnectionError:
            return {"ok": False, "models": [], "error": "连接失败，请检查 base_url"}
        except Exception as e:
            return {"ok": False, "models": [], "error": str(e)[:300]}

    # ── 编辑 / 添加 / 删除 Provider ──

    _EDITABLE_FIELDS = [
        "model", "base_url", "api_key_env", "priority",
        "task_types", "enabled", "timeout_chat_seconds",
        "timeout_background_seconds", "max_retries",
        "cooldown_seconds", "max_consecutive_failures",
        "disable_on_quota_exhausted", "thinking_enabled",
    ]

    def edit_provider(self, name: str, updates: dict) -> bool:
        """编辑某个 provider 的字段（仅允许 _EDITABLE_FIELDS 中的字段）。
        
        不允许修改 name（name 是唯一标识符）。
        返回是否成功。
        """
        path = _providers_yaml_path()
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or "providers" not in data:
                return False

            found = False
            for p in data["providers"]:
                if p.get("name") == name:
                    for key in self._EDITABLE_FIELDS:
                        if key in updates:
                            p[key] = updates[key]
                    found = True
                    break

            if not found:
                return False

            from bot.safe_io import atomic_write_yaml
            atomic_write_yaml(path, data)
            self._reload_providers()
            self._load_and_merge_state()
            logger.info("Provider '%s' edited: %s", name, [k for k in updates])
            return True
        except Exception as e:
            logger.warning("Failed to edit provider %s: %s", name, e)
            return False

    def add_provider(self, provider_config: dict) -> bool:
        """添加一个新的 provider。name 必须唯一。返回是否成功。"""
        name = (provider_config.get("name") or "").strip()
        if not name:
            return False
        if name in self._providers_by_name:
            logger.warning("Provider '%s' already exists, cannot add", name)
            return False

        path = _providers_yaml_path()
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                data = {"providers": []}
            if "providers" not in data:
                data["providers"] = []

            new_p = {
                "name": name,
                "enabled": provider_config.get("enabled", True),
                "priority": int(provider_config.get("priority", 99)),
                "task_types": provider_config.get("task_types", ["chat"]),
                "api_key_env": provider_config.get("api_key_env", ""),
                "base_url": provider_config.get("base_url", ""),
                "model": provider_config.get("model", ""),
                "timeout_chat_seconds": int(provider_config.get("timeout_chat_seconds", 60)),
                "timeout_background_seconds": int(provider_config.get("timeout_background_seconds", 30)),
                "max_retries": int(provider_config.get("max_retries", 1)),
                "cooldown_seconds": int(provider_config.get("cooldown_seconds", 300)),
                "max_consecutive_failures": int(provider_config.get("max_consecutive_failures", 3)),
                "disable_on_quota_exhausted": bool(provider_config.get("disable_on_quota_exhausted", True)),
                "thinking_enabled": bool(provider_config.get("thinking_enabled", False)),
            }

            data["providers"].append(new_p)

            from bot.safe_io import atomic_write_yaml
            atomic_write_yaml(path, data)
            self._reload_providers()
            self._load_and_merge_state()
            logger.info("Provider '%s' added", name)
            return True
        except Exception as e:
            logger.warning("Failed to add provider %s: %s", name, e)
            return False

    def delete_provider(self, name: str) -> bool:
        """删除一个 provider。返回是否成功。"""
        path = _providers_yaml_path()
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data or "providers" not in data:
                return False

            original_count = len(data["providers"])
            data["providers"] = [p for p in data["providers"] if p.get("name") != name]

            if len(data["providers"]) == original_count:
                return False  # 没找到

            from bot.safe_io import atomic_write_yaml
            atomic_write_yaml(path, data)
            self._reload_providers()
            self._load_and_merge_state()
            logger.info("Provider '%s' deleted", name)
            return True
        except Exception as e:
            logger.warning("Failed to delete provider %s: %s", name, e)
            return False
