"""自主行为系统：让机器人自动响应环境变化（避险、进食、逃跑等）。

设计说明：
- Behavior 抽象基类定义行为的触发条件和执行动作
- AutonomousBehaviorManager 后台循环检查所有行为的触发条件
- 触发的行为通过 action_queue 提交高优先级动作（可插队）
- 自主行为优先级 50-100，用户指令默认优先级 0

「生动反应」层（参考车万女仆模组 MaidBrain/MaidSchedule/JoyTask 设计）：
- AmbientIdleBehavior：空闲时的小动作 + 自言自语（分清晨/白天/黄昏/深夜四个时段，
  结合心情系统 MoodSystem，用 LLM 生成台词、本地人格模板兜底）
- NearbyEntityReactionBehavior：实体接近时发表反应（好奇/警惕）
- HurtReactionBehavior：受伤时喊疼
- HungryReactionBehavior：饥饿时喊饿
- MoodSystem：心情/好感，随互动上升、久无互动缓降，影响台词语气
- 所有发言通过 speak 回调推送到 QQ 群订阅者；带节流，避免刷屏
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .bot_client import MCBot
    from .persona import Persona

logger = logging.getLogger("astrbot_plugin_minecraft.behaviors")


# ============================================================
# 时段（参考 MaidSchedule：按一天的时间调度活动与情绪）
# ============================================================
def time_slot(hour: Optional[int] = None) -> str:
    """把一天分为 4 个时段，影响空闲台词的情境。"""
    if hour is None:
        hour = time.localtime().tm_hour
    if 5 <= hour < 8:
        return "dawn"    # 清晨
    if 8 <= hour < 17:
        return "day"     # 白天
    if 17 <= hour < 20:
        return "dusk"    # 黄昏
    return "night"       # 深夜


SLOT_DESCRIPTION = {
    "dawn": "清晨，太阳刚升起",
    "day": "白天，阳光正好",
    "dusk": "黄昏，晚霞满天",
    "night": "夜晚，星光点点",
}


# ============================================================
# 心情系统（参考车万女仆的 mood / 好感）
# ============================================================
class MoodSystem:
    """心情/好感：互动上升、久无互动缓降，影响发言语气。"""

    def __init__(self, start: int = 60, *, enabled: bool = True):
        self.value = max(0, min(100, start))
        self.enabled = enabled
        self.interactions = 0
        self.last_interact = time.time()

    def note_interaction(self, delta: int = 4) -> None:
        if not self.enabled:
            return
        self.value = min(100, self.value + delta)
        self.interactions += 1
        self.last_interact = time.time()

    def tick(self) -> None:
        """后台循环调用：无互动时心情缓缓回落。"""
        if not self.enabled:
            return
        idle_seconds = time.time() - self.last_interact
        if idle_seconds > 900:  # 15 分钟无互动开始回落
            decay = (idle_seconds - 900) / 60 * 0.02
            self.value = max(15, self.value - decay)

    @property
    def level(self) -> str:
        if self.value >= 75:
            return "high"
        if self.value >= 45:
            return "mid"
        return "low"

    def describe(self, persona: "Persona") -> str:
        return persona.describe_mood(self.level)


# ============================================================
# 行为基类
# ============================================================
@dataclass
class Behavior(ABC):
    """自主行为抽象基类"""
    name: str
    priority: int  # 动作优先级（越高越优先）
    check_interval: float  # 检查间隔（秒）
    last_check: float = 0.0

    @abstractmethod
    async def should_trigger(self, bot: "MCBot", manager: "AutonomousBehaviorManager") -> bool:
        """判断是否应该触发该行为"""
        pass

    @abstractmethod
    async def execute(self, bot: "MCBot", manager: "AutonomousBehaviorManager") -> str | None:
        """执行行为，返回 None（成功）或错误信息（失败）"""
        pass


# ============================================================
# 原有行为（改签名后保留）
# ============================================================
class EatWhenHungryBehavior(Behavior):
    """饥饿时自动进食（v1 仅记录日志，未来扩展背包操作）"""

    def __init__(self, threshold: int = 10):
        super().__init__(name="eat_when_hungry", priority=50, check_interval=5.0)
        self.threshold = threshold

    async def should_trigger(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> bool:
        return bot.connected and bot.food < self.threshold

    async def execute(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> str | None:
        logger.info(
            "检测到饥饿（%d/20），需要进食（当前版本暂不支持背包操作，仅记录日志）", bot.food
        )
        return None


class FleeFromMobsBehavior(Behavior):
    """逃离靠近的实体（简化实现：检测任何接近的实体）"""

    def __init__(self):
        super().__init__(name="flee_from_mobs", priority=80, check_interval=2.0)
        self.flee_distance = 5.0
        self.last_flee_time = 0.0
        self.flee_cooldown = 10.0

    async def should_trigger(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> bool:
        if not bot.connected or bot.position is None:
            return False
        if time.time() - self.last_flee_time < self.flee_cooldown:
            return False
        bx, by, bz = bot.position
        for eid, ent in bot.entities.items():
            if eid == bot.entity_id:
                continue
            if math.hypot(ent.get("x", 0) - bx, ent.get("z", 0) - bz) < self.flee_distance:
                return True
        return False

    async def execute(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> str | None:
        if bot.position is None:
            return "无当前坐标"
        bx, by, bz = bot.position
        closest = None
        min_dist = float("inf")
        for eid, ent in bot.entities.items():
            if eid == bot.entity_id:
                continue
            dist = math.hypot(ent.get("x", 0) - bx, ent.get("z", 0) - bz)
            if dist < min_dist:
                min_dist = dist
                closest = ent
        if closest is None:
            return "未找到威胁实体"
        ex, ez = closest.get("x", 0), closest.get("z", 0)
        dx, dz = bx - ex, bz - ez
        flee_dist = 8.0
        if math.hypot(dx, dz) > 0:
            flee_x = bx + (dx / math.hypot(dx, dz)) * flee_dist
            flee_z = bz + (dz / math.hypot(dx, dz)) * flee_dist
        else:
            angle = random.uniform(0, 2 * math.pi)
            flee_x = bx + math.cos(angle) * flee_dist
            flee_z = bz + math.sin(angle) * flee_dist
        logger.info("检测到实体接近（距离 %.1f 格），逃跑到 (%.1f, %.1f)", min_dist, flee_x, flee_z)
        if bot.action_queue:
            bot.action_queue.submit("move", {"x": flee_x, "z": flee_z, "timeout": 15.0}, priority=self.priority)
        else:
            await bot.move_to(flee_x, flee_z, timeout=15.0)
        self.last_flee_time = time.time()
        return None


# ============================================================
# 「生动反应」新行为（参考车万女仆）
# ============================================================
class AmbientIdleBehavior(Behavior):
    """空闲时的小动作 + 自言自语。

    参考车万女仆 MaidJoyTask / MaidAwaitTask / MaidFindSitTask / MaidSchedule：
    - 按时段（清晨/白天/黄昏/深夜）选择情境
    - 按心情（high/mid/low）选择语气
    - 优先用 LLM 生成台词（带人格 system_prompt），失败回退本地人格模板
    - 有节流：两次发言间隔不小于 min_gap 秒
    """

    def __init__(self, *, min_gap: float = 180.0, llm_enabled: bool = True,
                 trigger_probability: float = 0.25):
        super().__init__(name="ambient_idle", priority=10, check_interval=5.0)
        self.min_gap = min_gap
        self.llm_enabled = llm_enabled
        self.trigger_probability = trigger_probability
        self.last_emit = 0.0
        self._rng = random.Random()

    async def should_trigger(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> bool:
        if not bot.connected:
            return False
        # 正在执行动作时不打扰（女仆在忙）
        if bot.action_queue and bot.action_queue.get_current_task() is not None:
            return False
        if time.time() - self.last_emit < self.min_gap:
            return False
        # 概率触发：避免过于规律，显得更自然
        return self._rng.random() < self.trigger_probability

    async def execute(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> str | None:
        slot = time_slot()
        persona = manager.persona
        mood_level = manager.mood.level
        now = time.time()

        # 1) 优先 LLM 生成（更生动、不重样）
        text: Optional[str] = None
        if self.llm_enabled and manager.llm_cb is not None:
            status = bot.get_status()
            players = status.get("players") or []
            player_desc = ("，身边有玩家：" + "、".join(players[:4])) if players else ""
            prompt = (
                f"{persona.system_prompt()}\n"
                f"现在是{SLOT_DESCRIPTION[slot]}，{persona.describe_mood(mood_level)}。\n"
                f"你正在 Minecraft 世界中（生命 {status.get('health', 20):.0f}/20，"
                f"饥饿 {status.get('food', 20)}/20{player_desc}）。\n"
                "请用一句话（35 字以内）自然地自言自语或对主人说点什么，"
                "格式为「（小动作描述）＋台词」，例如「（歪了歪头）主人，那边好像有什么东西呢」。"
                "不要加引号和前缀。"
            )
            try:
                raw = await manager.llm(prompt)
                if raw:
                    text = raw.strip().strip('"“”‘’')
            except Exception:  # noqa: BLE001
                text = None
            if text:
                text = text[:120]

        # 2) 模板兜底
        if not text:
            text = persona.pick_idle(slot, mood_level)

        await manager.speak(text)
        self.last_emit = now
        return None


class NearbyEntityReactionBehavior(Behavior):
    """实体接近时发表反应（参考女仆的警觉/害怕表现）。"""

    def __init__(self, *, distance: float = 6.0, cooldown: float = 90.0):
        super().__init__(name="entity_reaction", priority=40, check_interval=3.0)
        self.distance = distance
        self.cooldown = cooldown
        self.last_reaction = 0.0

    async def should_trigger(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> bool:
        if not bot.connected or bot.position is None:
            return False
        if time.time() - self.last_reaction < self.cooldown:
            return False
        bx, by, bz = bot.position
        for eid, ent in bot.entities.items():
            if eid == bot.entity_id:
                continue
            if math.hypot(ent.get("x", 0) - bx, ent.get("z", 0) - bz) < self.distance:
                return True
        return False

    async def execute(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> str | None:
        await manager.speak(manager.persona.pick_reaction("entity_near"))
        self.last_reaction = time.time()
        return None


class HurtReactionBehavior(Behavior):
    """受伤时喊疼（参考女仆 hurt 音效反应）。"""

    def __init__(self, *, cooldown: float = 120.0):
        super().__init__(name="hurt_reaction", priority=60, check_interval=2.0)
        self.cooldown = cooldown
        self.last_health = 20.0
        self.last_reaction = 0.0

    async def should_trigger(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> bool:
        if not bot.connected:
            return False
        if time.time() - self.last_reaction < self.cooldown:
            # 即使冷却中也要持续记录血量，否则冷却结束后误判
            self.last_health = bot.health
            return False
        dropped = self.last_health - bot.health
        self.last_health = bot.health
        return dropped >= 1.0 and bot.health < 20.0

    async def execute(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> str | None:
        await manager.speak(manager.persona.pick_reaction("hurt"))
        self.last_reaction = time.time()
        return None


class HungryReactionBehavior(Behavior):
    """饥饿时喊饿（比 EatWhenHungry 更进一步：会说出来）。"""

    def __init__(self, *, threshold: int = 6, cooldown: float = 300.0):
        super().__init__(name="hungry_reaction", priority=55, check_interval=8.0)
        self.threshold = threshold
        self.cooldown = cooldown
        self.last_reaction = 0.0

    async def should_trigger(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> bool:
        if not bot.connected:
            return False
        if time.time() - self.last_reaction < self.cooldown:
            return False
        return bot.food <= self.threshold

    async def execute(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> str | None:
        await manager.speak(manager.persona.pick_reaction("hungry"))
        self.last_reaction = time.time()
        return None


class SurvivalFoodBehavior(Behavior):
    """生存行为：饥饿时自动吃食物（真正的 AI 玩家行为）。
    
    优先级高于空闲行为，低于紧急避险。当饥饿度低于阈值且库存有食物时，
    自动切换槽位并吃掉食物。未来可扩展为：没有食物时主动寻找和收集。
    """

    def __init__(self, *, threshold: int = 14, cooldown: float = 10.0):
        super().__init__(name="survival_food", priority=70, check_interval=5.0)
        self.threshold = threshold  # 饥饿度低于此值时吃食物
        self.cooldown = cooldown
        self.last_eat = 0.0

    async def should_trigger(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> bool:
        if not bot.connected:
            return False
        if time.time() - self.last_eat < self.cooldown:
            return False
        # 饿了且有食物
        if bot.food < self.threshold:
            return bot.find_food_slot() is not None
        return False

    async def execute(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> str | None:
        logger.info("触发生存行为：饥饿度 %d，准备吃食物", bot.food)
        
        # 尝试吃食物
        err = await bot.eat_food()
        if err:
            logger.warning("吃食物失败：%s", err)
            await manager.speak(f"（肚子咕咕叫）主人，{err}...")
            return err
        
        # 成功吃掉，等待效果生效
        await asyncio.sleep(1.5)
        self.last_eat = time.time()
        
        # 吃完后的反应
        if bot.food > self.threshold:
            await manager.speak(manager.persona.pick_reaction("eat_success"))
        else:
            await manager.speak("（吃了点东西）嗯...还是有点饿呢")
        
        return None


class AmbientWanderBehavior(Behavior):
    """空闲时小范围闲逛，让角色看起来「活着」（参考女仆 FindSit/Joy 的移动表现）。

    会每隔一段随机时间，朝出生点附近随机方向走几步；用户命令（动作队列）优先，
    有任务执行时不会闲逛。
    """

    def __init__(self, *, radius: float = 10.0, min_interval: float = 120.0,
                 max_interval: float = 300.0):
        super().__init__(name="ambient_wander", priority=8, check_interval=10.0)
        self.radius = radius
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.last_wander = 0.0
        self._rng = random.Random()

    async def should_trigger(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> bool:
        if not bot.connected or bot.position is None:
            return False
        # 有动作在执行时不闲逛
        if bot.action_queue and bot.action_queue.get_current_task() is not None:
            return False
        gap = self._rng.uniform(self.min_interval, self.max_interval)
        if time.time() - self.last_wander < gap:
            return False
        return self._rng.random() < 0.5

    async def execute(self, bot: MCBot, manager: "AutonomousBehaviorManager") -> str | None:
        if bot.position is None:
            return "无当前坐标"
        bx, by, bz = bot.position
        angle = self._rng.uniform(0, 2 * math.pi)
        dist = self._rng.uniform(2.0, self.radius)
        tx = bx + math.cos(angle) * dist
        tz = bz + math.sin(angle) * dist
        logger.info("空闲闲逛：走向 (%.1f, %.1f)", tx, tz)
        if bot.action_queue:
            bot.action_queue.submit("move", {"x": tx, "z": tz, "timeout": 30.0}, priority=self.priority)
        else:
            await bot.move_to(tx, tz, timeout=30.0)
        self.last_wander = time.time()
        return None


# ============================================================
# 管理器
# ============================================================
class AutonomousBehaviorManager:
    """自主行为管理器：后台循环检查并触发行为。"""

    def __init__(self, bot: MCBot, config: dict, persona: Optional["Persona"] = None):
        self._bot = bot
        self._config = config
        self._behaviors: list[Behavior] = []
        self._running = False

        # 发言/LLM 通道（由插件在接线时注入）
        self.speak_cb: Optional[Callable[[str], None]] = None
        self.llm_cb: Optional[Callable[[str], str | None]] = None

        # 人格与心情
        self.persona: Persona = persona
        self.mood = MoodSystem(enabled=bool(config.get("enable_mood", True)))

        self._register_behaviors()

    def _register_behaviors(self) -> None:
        cfg = self._config
        if not cfg.get("enable_autonomous_behaviors", True):
            return

        # 原有行为
        if cfg.get("auto_eat_threshold", 10) > 0:
            self._behaviors.append(EatWhenHungryBehavior(threshold=cfg.get("auto_eat_threshold", 10)))
        if cfg.get("auto_flee_enabled", False):
            self._behaviors.append(FleeFromMobsBehavior())

        # 生存行为（真正的 AI 玩家）
        if cfg.get("enable_survival_behaviors", True):
            self._behaviors.append(SurvivalFoodBehavior(
                threshold=int(cfg.get("survival_food_threshold", 14)),
                cooldown=float(cfg.get("survival_food_cooldown", 10.0)),
            ))

        # 生动反应层
        if cfg.get("enable_idle_behaviors", True):
            self._behaviors.append(AmbientIdleBehavior(
                min_gap=float(cfg.get("idle_broadcast_interval", 180)),
                llm_enabled=bool(cfg.get("idle_llm_generation", True)),
            ))
            self._behaviors.append(NearbyEntityReactionBehavior())
            self._behaviors.append(HurtReactionBehavior())
            self._behaviors.append(HungryReactionBehavior(threshold=max(1, min(20, int(cfg.get("idle_hungry_threshold", 6))))))
            self._behaviors.append(AmbientWanderBehavior())

        logger.info("自主行为管理器初始化，已注册 %d 个行为（人格：%s）",
                    len(self._behaviors), self.persona.name if self.persona else "无")

    # ---------- 通道 ----------
    def set_channel(self, speak_cb: Optional[Callable[[str], None]] = None,
                    llm_cb: Optional[Callable[[str], str | None]] = None) -> None:
        """注入发言与 LLM 回调（由插件接线时调用）。"""
        self.speak_cb = speak_cb
        self.llm_cb = llm_cb

    async def speak(self, text: str) -> None:
        """把角色的「话语/动作」推送给订阅者。"""
        if self.speak_cb:
            try:
                await self.speak_cb(text)
            except Exception:  # noqa: BLE001
                logger.exception("发送自主发言失败")

    async def llm(self, prompt: str) -> str | None:
        """调用 LLM 生成内容（失败返回 None）。"""
        if self.llm_cb:
            try:
                return await self.llm_cb(prompt)
            except Exception:  # noqa: BLE001
                logger.exception("LLM 生成失败")
        return None

    def note_interaction(self, delta: int = 4) -> None:
        """记录一次互动（玩家聊天/收到指令），心情上升。"""
        self.mood.note_interaction(delta)

    # ---------- 主循环 ----------
    async def run(self) -> None:
        self._running = True
        logger.info("自主行为管理器开始运行")
        while self._running and self._bot.connected:
            now = time.time()
            self.mood.tick()
            for behavior in sorted(self._behaviors, key=lambda b: -b.priority):
                if now - behavior.last_check < behavior.check_interval:
                    continue
                behavior.last_check = now
                try:
                    if await behavior.should_trigger(self._bot, self):
                        logger.info("触发自主行为：%s", behavior.name)
                        result = await behavior.execute(self._bot, self)
                        if result:
                            logger.warning("自主行为 %s 执行失败：%s", behavior.name, result)
                except Exception:  # noqa: BLE001
                    logger.exception("执行自主行为 %s 时出错", behavior.name)
            await asyncio.sleep(1.0)
        logger.info("自主行为管理器已停止")

    def stop(self) -> None:
        self._running = False
