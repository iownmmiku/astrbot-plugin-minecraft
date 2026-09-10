"""目标系统 V2 - 基于真实动作验证的自主游玩

设计原则：
1. 每个目标必须有可观测的完成条件（库存变化、方块变化、实体状态）
2. 每个步骤必须调用真实动作（move_to/mine/craft/attack）并等待服务器确认
3. 失败必须可诊断，带明确原因
4. 支持断点续玩：步骤、重试次数、失败原因持久化
5. 没有前置能力时不宣称目标可用（如无区块数据时不提供"寻找树木"目标）
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .bot_client import MCBot

logger = logging.getLogger("astrbot_plugin_minecraft.goal_system_v2")


# ============================================================
# 目标状态
# ============================================================
class GoalStatus(Enum):
    """目标状态"""
    PENDING = "pending"           # 等待执行
    IN_PROGRESS = "in_progress"   # 执行中
    COMPLETED = "completed"       # 已完成
    FAILED = "failed"             # 失败
    BLOCKED = "blocked"           # 被阻塞（前置条件未满足）


# ============================================================
# 目标基类
# ============================================================
@dataclass
class Goal(ABC):
    """目标抽象基类 - 基于真实动作验证"""
    name: str                      # 目标名称
    description: str               # 目标描述
    priority: int = 50             # 优先级（越高越优先）
    status: GoalStatus = GoalStatus.PENDING
    progress: float = 0.0          # 进度 0.0-1.0
    current_step: int = 0          # 当前步骤索引
    retry_count: int = 0           # 当前步骤重试次数
    max_retries: int = 3           # 每步最大重试次数
    last_error: str = ""           # 最近错误信息
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    @abstractmethod
    async def check_preconditions(self, bot: MCBot) -> tuple[bool, str]:
        """检查前置条件
        
        Returns:
            (满足, 原因): 是否满足前置条件及原因
        """
        pass
    
    @abstractmethod
    async def check_completion(self, bot: MCBot) -> tuple[bool, float]:
        """检查完成条件
        
        Returns:
            (已完成, 进度): 是否完成及当前进度 0.0-1.0
        """
        pass
    
    @abstractmethod
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        """执行当前步骤
        
        Returns:
            (成功, 消息): 步骤是否成功完成及状态消息
        """
        pass
    
    @abstractmethod
    def get_total_steps(self) -> int:
        """返回总步骤数"""
        pass
    
    def advance_step(self) -> None:
        """前进到下一步"""
        self.current_step += 1
        self.retry_count = 0
        self.last_error = ""
        self.progress = min(1.0, self.current_step / self.get_total_steps())
    
    def mark_failed(self, reason: str) -> None:
        """标记为失败"""
        self.status = GoalStatus.FAILED
        self.last_error = reason
        self.completed_at = time.time()
    
    def mark_completed(self) -> None:
        """标记为完成"""
        self.status = GoalStatus.COMPLETED
        self.progress = 1.0
        self.completed_at = time.time()
    
    def to_dict(self) -> dict:
        """序列化"""
        return {
            "name": self.name,
            "description": self.description,
            "priority": self.priority,
            "status": self.status.value,
            "progress": self.progress,
            "current_step": self.current_step,
            "total_steps": self.get_total_steps(),
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


# ============================================================
# 基础目标：采集木头
# ============================================================
class CollectWoodGoal(Goal):
    """采集木头：靠近附近树木并挖掘原木
    
    前置条件：能移动
    完成条件：库存中有 >= target_count 原木
    步骤：
      1. 查找附近原木方块（item_id 17/162，oak_log/dark_oak_log等）
      2. 移动到原木方块附近（距离 <= 4）
      3. 挖掘原木方块
      4. 等待掉落物并拾取
      5. 重复直到达到目标数量
    """
    
    def __init__(self, target_count: int = 16):
        super().__init__(
            name="collect_wood",
            description=f"采集 {target_count} 原木",
            priority=100,
        )
        self.target_count = target_count
        self.metadata["target_count"] = target_count
        self.metadata["collected"] = 0
        # 原木方块ID（1.20.1）：oak_log等对应的block_id
        # 注：需要根据bot_client.py的ITEM_NAMES反查block_id
        self.log_block_ids = [17, 162]  # 简化：仅oak_log, dark_oak_log
    
    async def check_preconditions(self, bot: MCBot) -> tuple[bool, str]:
        if not bot.connected:
            return False, "未连接到服务器"
        if bot.position is None:
            return False, "未获取到位置信息"
        # 检查是否有方块缓存能力
        if not hasattr(bot, "blocks") or not hasattr(bot, "get_block"):
            return False, "服务器未发送方块数据"
        return True, "满足前置条件"
    
    async def check_completion(self, bot: MCBot) -> tuple[bool, float]:
        # 统计库存中所有原木
        status = bot.get_status()
        inventory = status.get("inventory", {})
        log_types = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log"]
        total = sum(inventory.get(log_type, 0) for log_type in log_types)
        
        self.metadata["collected"] = total
        progress = min(1.0, total / self.target_count)
        return total >= self.target_count, progress
    
    def get_total_steps(self) -> int:
        # 动态步骤：每次挖一根原木算一步
        return self.target_count
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        """执行一步：寻找、移动、挖掘一根原木"""
        # 1. 检查当前是否已完成
        completed, _ = await self.check_completion(bot)
        if completed:
            return True, f"已采集足够原木（{self.metadata['collected']}/{self.target_count}）"
        
        # 2. 在缓存中查找最近的原木方块
        if bot.position is None:
            return False, "无法获取当前位置"
        
        bx, by, bz = bot.position
        search_radius = 32  # bot.block_cache_radius
        nearest_log = None
        nearest_dist = float('inf')
        
        for (wx, wy, wz), block_id in bot.blocks.items():
            if block_id in self.log_block_ids:
                dist = ((wx - bx)**2 + (wy - by)**2 + (wz - bz)**2) ** 0.5
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_log = (wx, wy, wz)
        
        if nearest_log is None:
            # 没有找到原木：随机移动到新区域加载区块
            import random
            target_x = bx + random.uniform(-20, 20)
            target_z = bz + random.uniform(-20, 20)
            logger.info(f"未找到原木，移动到 ({target_x:.1f}, {target_z:.1f}) 加载新区块")
            err = await bot.move_to(target_x, target_z, timeout=15.0)
            if err:
                return False, f"移动失败：{err}"
            # 移动后等待区块加载
            await asyncio.sleep(1.0)
            return False, "未找到原木，已移动到新位置"
        
        # 3. 移动到原木附近
        lx, ly, lz = nearest_log
        dist = ((lx - bx)**2 + (ly - by)**2 + (lz - bz)**2) ** 0.5
        if dist > 4.5:
            logger.info(f"移动到原木 ({lx}, {ly}, {lz})，距离 {dist:.1f}")
            err = await bot.move_to(float(lx), float(lz), timeout=20.0)
            if err:
                return False, f"移动到原木失败：{err}"
        
        # 4. 挖掘原木
        logger.info(f"挖掘原木 ({lx}, {ly}, {lz})")
        err = await bot.mine(lx, ly, lz, timeout=10.0)
        if err:
            # 挖掘失败可能是方块已被挖走或太远
            logger.warning(f"挖掘原木失败：{err}")
            return False, f"挖掘失败：{err}"
        
        # 5. 尝试拾取附近掉落物
        if hasattr(bot, "collect_drops"):
            logger.info("拾取附近掉落物")
            await bot.collect_drops(radius=5.0, timeout=2.0)
        
        # 6. 等待库存更新
        await asyncio.sleep(0.5)
        
        # 成功挖掘一根原木
        return True, f"成功挖掘原木 ({lx}, {ly}, {lz})"


# ============================================================
# 目标管理器
# ============================================================
class GoalManagerV2:
    """目标管理器 V2 - 基于真实动作验证"""
    
    def __init__(self, bot: MCBot, speak_callback=None, llm_callback=None):
        self.bot = bot
        self.speak_callback = speak_callback  # 语音反馈回调
        self.llm_callback = llm_callback      # LLM决策回调
        self.current_goal: Optional[Goal] = None
        self.goal_history: list[Goal] = []
        self.running = False
        self._task: Optional[asyncio.Task] = None
    
    def start(self) -> None:
        """启动目标系统"""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._goal_loop())
        logger.info("目标系统已启动")
    
    def stop(self) -> None:
        """停止目标系统"""
        self.running = False
        if self._task:
            self._task.cancel()
        logger.info("目标系统已停止")
    
    async def _goal_loop(self) -> None:
        """目标执行主循环"""
        try:
            while self.running:
                await asyncio.sleep(5.0)  # 每5秒检查一次
                
                # 如果没有当前目标，选择新目标
                if self.current_goal is None:
                    await self._select_next_goal()
                    continue
                
                # 检查前置条件
                ok, reason = await self.current_goal.check_preconditions(self.bot)
                if not ok:
                    logger.warning(f"目标 {self.current_goal.name} 前置条件不满足：{reason}")
                    self.current_goal.status = GoalStatus.BLOCKED
                    await self._speak(f"无法继续当前目标：{reason}")
                    self.current_goal = None
                    continue
                
                # 检查是否完成
                completed, progress = await self.current_goal.check_completion(self.bot)
                if completed:
                    logger.info(f"目标 {self.current_goal.name} 已完成")
                    self.current_goal.mark_completed()
                    await self._speak(f"目标完成：{self.current_goal.description}")
                    self.goal_history.append(self.current_goal)
                    self.current_goal = None
                    continue
                
                # 执行当前步骤
                self.current_goal.status = GoalStatus.IN_PROGRESS
                if self.current_goal.started_at is None:
                    self.current_goal.started_at = time.time()
                
                success, message = await self.current_goal.execute_step(self.bot)
                
                if success:
                    # 步骤成功，前进到下一步
                    self.current_goal.advance_step()
                    logger.info(f"步骤完成：{message}")
                else:
                    # 步骤失败，重试
                    self.current_goal.retry_count += 1
                    self.current_goal.last_error = message
                    logger.warning(f"步骤失败（{self.current_goal.retry_count}/{self.current_goal.max_retries}）：{message}")
                    
                    if self.current_goal.retry_count >= self.current_goal.max_retries:
                        # 超过最大重试次数，目标失败
                        self.current_goal.mark_failed(f"步骤重试{self.current_goal.max_retries}次后仍失败：{message}")
                        await self._speak(f"目标失败：{self.current_goal.description}，原因：{message}")
                        self.goal_history.append(self.current_goal)
                        self.current_goal = None
        
        except asyncio.CancelledError:
            logger.info("目标循环被取消")
        except Exception as e:
            logger.exception(f"目标循环异常：{e}")
    
    async def _select_next_goal(self) -> None:
        """选择下一个目标"""
        # 简化版本：先只实现采集木头目标
        # 后续扩展：根据库存、装备、进度选择合适目标
        logger.info("选择新目标：采集木头")
        self.current_goal = CollectWoodGoal(target_count=16)
        await self._speak("新目标：采集16根原木")
    
    async def _speak(self, text: str) -> None:
        """发言回调"""
        if self.speak_callback:
            await self.speak_callback(text)
    
    def get_status(self) -> dict:
        """获取当前状态"""
        if self.current_goal is None:
            return {"running": self.running, "current_goal": None}
        return {
            "running": self.running,
            "current_goal": self.current_goal.to_dict(),
        }
