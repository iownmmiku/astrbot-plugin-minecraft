"""自主行为系统：让机器人自动响应环境变化（避险、进食、逃跑等）。

设计说明：
- Behavior 抽象基类定义行为的触发条件和执行动作
- AutonomousBehaviorManager 后台循环检查所有行为的触发条件
- 触发的行为通过 action_queue 提交高优先级动作（可插队）
- 自主行为优先级 50-100，用户指令默认优先级 0
"""

from __future__ import annotations

import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot_client import MCBot

logger = logging.getLogger("astrbot_plugin_minecraft.behaviors")


@dataclass
class Behavior(ABC):
    """自主行为抽象基类"""
    name: str
    priority: int  # 动作优先级（越高越优先）
    check_interval: float  # 检查间隔（秒）
    last_check: float = 0.0

    @abstractmethod
    async def should_trigger(self, bot: MCBot) -> bool:
        """判断是否应该触发该行为"""
        pass

    @abstractmethod
    async def execute(self, bot: MCBot) -> str | None:
        """执行行为，返回 None（成功）或错误信息（失败）"""
        pass


class EatWhenHungryBehavior(Behavior):
    """饥饿时自动进食（v1 仅记录日志，未来扩展背包操作）"""

    def __init__(self, threshold: int = 10):
        super().__init__(
            name="eat_when_hungry",
            priority=50,
            check_interval=5.0,
        )
        self.threshold = threshold

    async def should_trigger(self, bot: MCBot) -> bool:
        return bot.connected and bot.food < self.threshold

    async def execute(self, bot: MCBot) -> str | None:
        logger.info(
            "检测到饥饿（%d/20），需要进食（当前版本暂不支持背包操作，仅记录日志）",
            bot.food,
        )
        # 未来扩展：发送使用食物包（需要背包系统）
        return None


class FleeFromMobsBehavior(Behavior):
    """逃离靠近的实体（简化实现：检测任何接近的实体）"""

    def __init__(self):
        super().__init__(
            name="flee_from_mobs",
            priority=80,
            check_interval=2.0,
        )
        self.flee_distance = 5.0  # 实体在此距离内触发逃跑
        self.last_flee_time = 0.0
        self.flee_cooldown = 10.0  # 逃跑冷却时间（避免频繁触发）

    async def should_trigger(self, bot: MCBot) -> bool:
        if not bot.connected or bot.position is None:
            return False
        
        # 检查冷却时间
        if time.time() - self.last_flee_time < self.flee_cooldown:
            return False

        bx, by, bz = bot.position
        
        # 检查是否有实体接近
        for eid, ent in bot.entities.items():
            if eid == bot.entity_id:
                continue  # 跳过自己
            
            ex = ent.get("x", 0)
            ez = ent.get("z", 0)
            dist = math.hypot(ex - bx, ez - bz)
            
            if dist < self.flee_distance:
                return True
        
        return False

    async def execute(self, bot: MCBot) -> str | None:
        if bot.position is None:
            return "无当前坐标"
        
        bx, by, bz = bot.position
        
        # 找到最近的实体
        closest = None
        min_dist = float("inf")
        
        for eid, ent in bot.entities.items():
            if eid == bot.entity_id:
                continue
            ex = ent.get("x", 0)
            ez = ent.get("z", 0)
            dist = math.hypot(ex - bx, ez - bz)
            if dist < min_dist:
                min_dist = dist
                closest = ent
        
        if closest is None:
            return "未找到威胁实体"
        
        ex = closest.get("x", 0)
        ez = closest.get("z", 0)
        
        # 计算逃跑方向（远离实体）
        dx = bx - ex
        dz = bz - ez
        flee_dist = 8.0  # 逃跑距离
        
        if math.hypot(dx, dz) > 0:
            flee_x = bx + (dx / math.hypot(dx, dz)) * flee_dist
            flee_z = bz + (dz / math.hypot(dx, dz)) * flee_dist
        else:
            # 实体正好在同一位置，随机方向逃跑
            import random
            angle = random.uniform(0, 2 * math.pi)
            flee_x = bx + math.cos(angle) * flee_dist
            flee_z = bz + math.sin(angle) * flee_dist
        
        logger.info("检测到实体接近（距离 %.1f 格），逃跑到 (%.1f, %.1f)", min_dist, flee_x, flee_z)
        
        # 提交高优先级移动动作
        if bot.action_queue:
            bot.action_queue.submit("move", {"x": flee_x, "z": flee_z, "timeout": 15.0}, priority=self.priority)
        else:
            # 如果没有动作队列，直接阻塞执行（不推荐，但保证功能可用）
            await bot.move_to(flee_x, flee_z, timeout=15.0)
        
        self.last_flee_time = time.time()
        return None


class AutonomousBehaviorManager:
    """自主行为管理器：后台循环检查并触发行为"""

    def __init__(self, bot: MCBot, config: dict):
        self._bot = bot
        self._config = config
        self._behaviors: list[Behavior] = []
        self._running = False
        
        # 根据配置注册行为
        if config.get("enable_autonomous_behaviors", True):
            if config.get("auto_eat_threshold", 10) > 0:
                self._behaviors.append(
                    EatWhenHungryBehavior(threshold=config.get("auto_eat_threshold", 10))
                )
            
            if config.get("auto_flee_enabled", False):
                self._behaviors.append(FleeFromMobsBehavior())
        
        logger.info("自主行为管理器初始化，已注册 %d 个行为", len(self._behaviors))

    async def run(self) -> None:
        """后台循环：定期检查并触发行为"""
        self._running = True
        logger.info("自主行为管理器开始运行")
        
        while self._running and self._bot.connected:
            now = time.time()
            
            # 按优先级排序检查行为
            for behavior in sorted(self._behaviors, key=lambda b: -b.priority):
                if now - behavior.last_check < behavior.check_interval:
                    continue
                
                behavior.last_check = now
                
                try:
                    if await behavior.should_trigger(self._bot):
                        logger.info("触发自主行为：%s", behavior.name)
                        result = await behavior.execute(self._bot)
                        if result:
                            logger.warning("自主行为 %s 执行失败：%s", behavior.name, result)
                except Exception:
                    logger.exception("执行自主行为 %s 时出错", behavior.name)
            
            # 每秒检查一次
            import asyncio
            await asyncio.sleep(1.0)
        
        logger.info("自主行为管理器已停止")

    def stop(self) -> None:
        """停止后台循环"""
        self._running = False
