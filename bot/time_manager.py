"""时间流逝管理器（用户驱动模式）。

时间推进由用户消息关键词检测或手动命令触发。
不再机械地按 AI 回复次数自动推进。
"""

import json
import logging
import threading
import time as _time

from config import prompts, settings


logger = logging.getLogger(__name__)

TIME_PERIODS = ["清晨", "上午", "中午", "下午", "傍晚", "夜晚", "深夜"]

# ── 关键词规则表 ──

CROSS_DAY_KEYWORDS = [
    "第二天早上", "第二天早晨", "次日清晨", "天亮后", "天亮之后",
    "第二天", "次日", "隔天", "过了一夜", "睡醒后", "一觉醒来",
    "第二天一早", "过了一天", "一天后",
]

PERIOD_KEYWORDS: dict[str, list[str]] = {
    "清晨": ["一大早", "天刚亮", "黎明", "天蒙蒙亮", "晨光"],
    "上午": ["上午", "早上", "早晨"],
    "中午": ["中午", "午饭", "午餐", "正午", "午时"],
    "下午": ["下午", "午后"],
    "傍晚": ["傍晚", "黄昏", "晚饭", "晚餐", "吃完饭后", "饭后",
             "天色渐暗", "太阳落山", "吃晚饭", "用过晚饭", "吃过晚饭"],
    "夜晚": ["晚上", "夜里", "入夜", "天黑了", "天黑后", "夜深了", "夜幕"],
    "深夜": ["深夜", "半夜", "凌晨", "三更", "午夜"],
}

VAGUE_ADVANCE_KEYWORDS = [
    "过了一会儿", "过了一会", "不久后", "片刻后", "一阵子后",
    "几个小时后", "半天后", "又过了一阵",
]


class TimeManager:
    """管理当前世界的时间状态。"""

    def __init__(self, world_name: str):
        settings.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.world_name = world_name
        self.file_path = settings.MEMORY_DIR / f"{world_name}_time_state.json"
        self._lock = threading.Lock()

        self.day: int = 1
        self.time_period: str = "上午"
        self.season: str = "夏"
        self.last_advance_turn: int = 0
        self.current_period_start_turn: int = 0  # 进入当前时段的轮次
        self.last_advance_real_time: float = _time.time()
        self.recent_days: list[str] = []

        self._load()

    # ═══════════════════════════════════════════════════════
    # 持久化
    # ═══════════════════════════════════════════════════════

    def _load(self) -> None:
        if not self.file_path.exists():
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            self.day = data.get("day", 1)
            self.time_period = data.get("time_period", "上午")
            self.season = data.get("season", "夏")
            self.last_advance_turn = data.get("last_advance_turn", 0)
            self.current_period_start_turn = data.get(
                "current_period_start_turn", self.last_advance_turn,
            )
            self.last_advance_real_time = data.get("last_advance_real_time", _time.time())
            self.recent_days = data.get("recent_days", [])
            logger.info("Loaded time state for '%s': day %d, %s", self.world_name, self.day, self.time_period)
        except Exception as exc:
            logger.error("Failed to load time state: %s", exc)

    def save(self) -> None:
        with self._lock:
            self._atomic_write()

    def _atomic_write(self) -> None:
        from bot.safe_io import atomic_write_json
        atomic_write_json(self.file_path, {
            "day": self.day,
            "time_period": self.time_period,
            "season": self.season,
            "last_advance_turn": self.last_advance_turn,
            "current_period_start_turn": self.current_period_start_turn,
            "last_advance_real_time": self.last_advance_real_time,
            "recent_days": self.recent_days,
        })

    # ═══════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════

    @property
    def rounds_in_current_period(self) -> int:
        """当前时段已持续的轮数。"""
        return self.last_advance_turn - self.current_period_start_turn

    def get_summary(self) -> str:
        """生成注入 system prompt 的时间摘要。"""
        recent = "；".join(self.recent_days[-3:]) if self.recent_days else "暂无记录"
        return (
            f"\n[当前时间]\n"
            f"第{self.day}天，{self.season}，{self.time_period}。\n"
            f"近日：{recent}"
        )

    def get_status_text(self) -> str:
        """供 /time 命令使用。"""
        rounds = self.rounds_in_current_period
        return (
            f"⏰ 第{self.day}天 · {self.season} · {self.time_period}"
            f"（已持续 {rounds} 轮）\n"
            f"近日：{'；'.join(self.recent_days[-3:]) if self.recent_days else '暂无记录'}"
        )

    def get_long_scene_hint(self) -> str:
        """如果当前时段持续过久，返回温和提示（每5轮触发一次）。"""
        if self.rounds_in_current_period >= settings.TIME_LONG_SCENE_HINT_THRESHOLD:
            if self.rounds_in_current_period % 5 == 0:
                return (
                    "\n\n（当前时段已持续较久，如需推进时间可使用 /next_time 或 /next_day）"
                )
        return ""

    # ═══════════════════════════════════════════════════════
    # 用户消息关键词检测
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def detect_advance(user_text: str, current_period: str) -> dict | None:
        """检测用户消息中的时间跳转意图。

        Returns:
            None 表示不推进。
            {"action": "advance_day" | "advance_period" | "jump_to",
             "target": str | None,
             "reason": str}
        """
        if not settings.TIME_USER_DRIVEN_ADVANCE_ENABLED:
            return None
        if not user_text:
            return None

        # 排除纯问候 —— "早上好""晚安"不触发真实时间变化
        if user_text.strip() in ("早上好", "早安", "晚安", "午安", "下午好", "晚上好"):
            return None

        # 排除回忆/假设 —— 含"记得""那天""如果"通常不是真的时间跳转
        recall_markers = ["还记得", "那天早上", "那天晚上", "那天", "如果", "要是",
                          "昨天梦到", "梦到", "想起", "回忆"]
        if any(m in user_text for m in recall_markers):
            return None

        # 1. 检查跨天关键词
        for kw in CROSS_DAY_KEYWORDS:
            if kw in user_text:
                return {"action": "advance_day", "target": "清晨", "reason": f"用户提到「{kw}」"}

        # 2. 检查明确时段关键词（按"晚→早"顺序，避免误匹配）
        for period in reversed(TIME_PERIODS):
            for kw in PERIOD_KEYWORDS.get(period, []):
                if kw in user_text:
                    # 如果用户说的时段比当前"早"，可能是回忆 → 不推进
                    current_idx = TIME_PERIODS.index(current_period)
                    target_idx = TIME_PERIODS.index(period)
                    if target_idx < current_idx and not any(
                        m in user_text for m in ["已经是", "到了", "到了该", "已是"]
                    ):
                        logger.debug("Time advance skipped: target %s earlier than current %s", period, current_period)
                        return None
                    if period == current_period:
                        return None
                    return {"action": "jump_to", "target": period, "reason": f"用户提到「{kw}」"}

        # 3. 检查模糊前进关键词
        for kw in VAGUE_ADVANCE_KEYWORDS:
            if kw in user_text:
                return {"action": "advance_period", "target": None, "reason": f"用户提到「{kw}」"}

        return None

    # ═══════════════════════════════════════════════════════
    # 推进
    # ═══════════════════════════════════════════════════════

    def reset(self) -> None:
        self.day = 1
        self.time_period = "上午"
        self.last_advance_turn = 0
        self.current_period_start_turn = 0
        self.recent_days = []
        self.save()
        logger.info("Time state reset for '%s'", self.world_name)

    def advance_period(self) -> str:
        """推进一个时段。如果已是深夜且允许跨天则 +1 天。"""
        idx = TIME_PERIODS.index(self.time_period)
        if idx < len(TIME_PERIODS) - 1:
            self.time_period = TIME_PERIODS[idx + 1]
        elif settings.TIME_AUTO_CROSS_DAY:
            self.day += 1
            self.time_period = TIME_PERIODS[0]
        # else: 深夜不再推进（等用户手动跨天）
        self._finalize_advance()
        return self.time_period

    def advance_day(self) -> str:
        """推进到第二天清晨。"""
        self.day += 1
        self.time_period = TIME_PERIODS[0]
        self._finalize_advance()
        return self.time_period

    def jump_to(self, target_period: str) -> str:
        """跳转到指定时段（同一天内）。"""
        if target_period in TIME_PERIODS:
            self.time_period = target_period
        self._finalize_advance()
        return self.time_period

    def _finalize_advance(self) -> None:
        self.last_advance_real_time = _time.time()
        self.save()

    def on_assistant_reply(self, reply_count: int) -> None:
        """每次 AI 回复后调用，仅更新计数，不自动推进。"""
        self.last_advance_turn = reply_count
        self.save()

    def mark_period_start(self, turn: int) -> None:
        """记录进入当前时段的轮次（推进后调用）。"""
        self.current_period_start_turn = turn

    async def generate_day_summary(self, memory: list[dict], client) -> None:
        """调用 AI 生成昨日生活摘要。"""
        if not memory:
            summary = f"第{self.day - 1}天：平静的一天。"
        else:
            recent = memory[-settings.AUTO_MEMORY_LOOKBACK:]
            dialogue = "\n".join(
                f"[{m.get('role','?')}]: {m.get('content','')[:300]}"
                for m in recent[-8:]
            )
            try:
                result, _ = await client.chat(
                    [
                        {"role": "system", "content": prompts.DAY_SUMMARY_PROMPT},
                        {"role": "user", "content": f"最近对话：\n\n{dialogue[:3000]}"},
                    ],
                    max_tokens=120,
                    temperature=0.3,
                    purpose="day_summary",
                )
                summary = f"第{self.day - 1}天：{result.strip()}"
            except Exception as exc:
                logger.warning("Day summary generation failed: %s", exc)
                summary = f"第{self.day - 1}天：平静的一天。"

        if summary not in self.recent_days:
            self.recent_days.append(summary)
        if len(self.recent_days) > 7:
            self.recent_days = self.recent_days[-7:]
        self.save()
