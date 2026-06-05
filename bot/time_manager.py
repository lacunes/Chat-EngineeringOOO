"""时间流逝管理器。

管理每个世界的虚拟时间（天数、时段、季节）。
纯背景系统，不强行推动剧情。
"""

import json
import logging
import os
import tempfile
import time as _time

from config import prompts, settings


logger = logging.getLogger(__name__)

TIME_PERIODS = ["清晨", "上午", "中午", "下午", "傍晚", "夜晚", "深夜"]


class TimeManager:
    """管理当前世界的时间状态。"""

    def __init__(self, world_name: str):
        settings.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.world_name = world_name
        self.file_path = settings.MEMORY_DIR / f"{world_name}_time_state.json"

        self.day: int = 1
        self.time_period: str = "上午"
        self.season: str = "夏"
        self.last_advance_turn: int = 0
        self.last_advance_real_time: float = _time.time()
        self.recent_days: list[str] = []

        self._load()

    # ═══════════════════════════════════════════════════════
    # 持久化
    # ═══════════════════════════════════════════════════════

    def _load(self) -> None:
        if not self.file_path.exists():
            logger.info("No time state for '%s', starting fresh", self.world_name)
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            self.day = data.get("day", 1)
            self.time_period = data.get("time_period", "上午")
            self.season = data.get("season", "夏")
            self.last_advance_turn = data.get("last_advance_turn", 0)
            self.last_advance_real_time = data.get("last_advance_real_time", _time.time())
            self.recent_days = data.get("recent_days", [])
            logger.info("Loaded time state for '%s': day %d, %s", self.world_name, self.day, self.time_period)
        except Exception as exc:
            logger.error("Failed to load time state: %s", exc)

    def save(self) -> None:
        self._atomic_write()

    def _atomic_write(self) -> None:
        tmp = None
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=str(self.file_path.parent),
                prefix=".tmp_time_", suffix=".json",
                delete=False, encoding="utf-8",
            ) as f:
                tmp = f.name
                json.dump({
                    "day": self.day,
                    "time_period": self.time_period,
                    "season": self.season,
                    "last_advance_turn": self.last_advance_turn,
                    "last_advance_real_time": self.last_advance_real_time,
                    "recent_days": self.recent_days,
                }, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.file_path)
        except Exception as exc:
            logger.error("Failed to save time state: %s", exc)
            if tmp:
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    # ═══════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════

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
        return (
            f"⏰ 第{self.day}天 · {self.season} · {self.time_period}\n"
            f"距上次推进：{self.last_advance_turn} 轮前\n"
            f"近日：{'；'.join(self.recent_days[-3:]) if self.recent_days else '暂无记录'}"
        )

    # ═══════════════════════════════════════════════════════
    # 推进
    # ═══════════════════════════════════════════════════════

    def reset(self) -> None:
        """清空时间状态。"""
        self.day = 1
        self.time_period = "上午"
        self.last_advance_turn = 0
        self.recent_days = []
        self.save()
        logger.info("Time state reset for '%s'", self.world_name)

    def advance_period(self) -> str:
        """推进一个时段。如果已是深夜则跨天。"""
        idx = TIME_PERIODS.index(self.time_period)
        if idx < len(TIME_PERIODS) - 1:
            self.time_period = TIME_PERIODS[idx + 1]
        else:
            self.day += 1
            self.time_period = TIME_PERIODS[0]
        self._finalize_advance()
        return self.time_period

    def advance_day(self) -> str:
        """推进到第二天清晨。"""
        self.day += 1
        self.time_period = TIME_PERIODS[0]
        self._finalize_advance()
        return self.time_period

    def _finalize_advance(self) -> None:
        self.last_advance_real_time = _time.time()
        self.save()

    def on_assistant_reply(self, reply_count: int) -> None:
        """每次 AI 回复后调用，检查是否需要自动推进。"""
        if reply_count - self.last_advance_turn < settings.TIME_ADVANCE_INTERVAL:
            return
        self.last_advance_turn = reply_count
        self.advance_period()

    async def generate_day_summary(self, memory: list[dict], client) -> None:
        """调用 AI 生成昨日生活摘要（供 /next_day 使用）。"""
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
