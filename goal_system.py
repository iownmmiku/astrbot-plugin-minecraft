"""目标导向的自主游玩系统

参考 Numen 项目设计理念：
- Goal（目标）：机器人想要达成的事情，如"获取木头"、"建造小屋"、"击败末影龙"
- Skill（技能工作流）：达成目标的具体步骤序列
- DecisionEngine（决策引擎）：根据当前状态自动选择下一个目标
- ProgressTracker（进度追踪）：持久化当前目标和进度，断点续玩

设计原则：
1. 目标有优先级和前置条件
2. 技能是可复用的步骤序列
3. 决策引擎会根据当前装备、资源、环境选择最合适的目标
4. 即使没有 LLM，也能通过规则引擎自主决策
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .bot_client import MCBot

logger = logging.getLogger("astrbot_plugin_minecraft.goal_system")


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
    """目标抽象基类
    
    每个目标代表机器人想要完成的一件事。
    目标有优先级、前置条件、完成条件、执行步骤。
    """
    name: str                      # 目标名称
    description: str               # 目标描述
    priority: int = 50             # 优先级（越高越优先）
    status: GoalStatus = GoalStatus.PENDING
    progress: float = 0.0          # 进度 0.0-1.0
    parent: Optional[Goal] = None  # 父目标（用于目标树）
    metadata: dict = field(default_factory=dict)  # 额外元数据
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    
    @abstractmethod
    async def check_preconditions(self, bot: MCBot) -> bool:
        """检查前置条件是否满足"""
        pass
    
    @abstractmethod
    async def check_completion(self, bot: MCBot) -> bool:
        """检查目标是否已完成"""
        pass
    
    @abstractmethod
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        """执行一步
        
        Returns:
            (should_continue, message): 是否应该继续执行，以及状态消息
        """
        pass
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "name": self.name,
            "description": self.description,
            "priority": self.priority,
            "status": self.status.value,
            "progress": self.progress,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


# ============================================================
# 具体目标实现
# ============================================================
class GatherWoodGoal(Goal):
    """采集木头目标
    
    前置条件：无
    完成条件：背包中有至少 16 根原木
    步骤：寻找树木 → 靠近 → 砍伐
    """
    
    def __init__(self, target_count: int = 16):
        super().__init__(
            name="gather_wood",
            description=f"采集 {target_count} 根原木",
            priority=80,
        )
        self.target_count = target_count
        self.metadata["target_count"] = target_count
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        # 采集木头没有前置条件
        return bot.connected
    
    async def check_completion(self, bot: MCBot) -> bool:
        # 检查背包中的原木数量
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        # 统计所有类型的原木
        log_types = [
            "oak_log", "birch_log", "spruce_log", 
            "jungle_log", "acacia_log", "dark_oak_log"
        ]
        total_logs = sum(inventory.get(log_type, 0) for log_type in log_types)
        
        self.progress = min(1.0, total_logs / self.target_count)
        return total_logs >= self.target_count
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        """执行采集木头的一步"""
        # TODO: 实现寻找树木、移动、砍伐的逻辑
        # 当前版本：使用简化的随机移动 + 等待玩家手动砍树
        logger.info("【目标】寻找并采集木头...")
        
        # 随机移动一段距离寻找树木
        if bot.position:
            import random
            bx, by, bz = bot.position
            target_x = bx + random.uniform(-20, 20)
            target_z = bz + random.uniform(-20, 20)
            
            if bot.action_queue:
                bot.action_queue.submit(
                    "move",
                    {"x": target_x, "z": target_z, "timeout": 30.0},
                    priority=self.priority
                )
            
            return True, "正在寻找树木..."
        
        return False, "无法获取当前位置"


class MineStoneGoal(Goal):
    """挖掘石头目标
    
    前置条件：有木镐或更好的镐子
    完成条件：背包中有至少 64 块圆石
    """
    
    def __init__(self, target_count: int = 64):
        super().__init__(
            name="mine_stone",
            description=f"挖掘 {target_count} 块圆石",
            priority=70,
        )
        self.target_count = target_count
        self.metadata["target_count"] = target_count
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        # 检查是否有镐子
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        pickaxes = [
            "wooden_pickaxe", "stone_pickaxe", "iron_pickaxe",
            "golden_pickaxe", "diamond_pickaxe", "netherite_pickaxe"
        ]
        has_pickaxe = any(inventory.get(p, 0) > 0 for p in pickaxes)
        
        if not has_pickaxe:
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        cobblestone = inventory.get("cobblestone", 0)
        
        self.progress = min(1.0, cobblestone / self.target_count)
        return cobblestone >= self.target_count
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        logger.info("【目标】寻找并挖掘石头...")
        # TODO: 实现寻找石头、装备镐子、挖掘的逻辑
        return True, "正在挖掘石头..."


class BuildShelterGoal(Goal):
    """建造庇护所目标
    
    前置条件：有足够的建筑材料（原木或圆石）
    完成条件：建造一个简单的小屋
    """
    
    def __init__(self, size: tuple[int, int, int] = (5, 3, 5)):
        super().__init__(
            name="build_shelter",
            description=f"建造 {size[0]}x{size[1]}x{size[2]} 的庇护所",
            priority=60,
        )
        self.size = size
        self.metadata["size"] = size
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        # 检查是否有足够的建筑材料
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        building_materials = inventory.get("cobblestone", 0) + inventory.get("oak_planks", 0)
        required = self.size[0] * 2 + self.size[2] * 2  # 简化计算：只算墙
        
        if building_materials < required:
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        # 简化版本：通过 metadata 标记是否完成
        return self.metadata.get("shelter_built", False)
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        logger.info("【目标】建造庇护所...")
        # TODO: 实现建造逻辑
        return True, "正在建造庇护所..."


# ============================================================
# 决策引擎
# ============================================================
class DecisionEngine:
    """决策引擎：根据当前状态自动选择下一个目标
    
    决策逻辑：
    1. 检查当前目标是否完成，如果完成则选择下一个
    2. 按优先级和前置条件筛选可用目标
    3. 如果有 LLM，询问 LLM 选择；否则使用规则引擎
    """
    
    def __init__(self, llm_callback: Optional[callable] = None):
        self.llm_callback = llm_callback
        # 导入所有目标类
        from .survival_goals import (
            CollectWoodGoal, CraftWoodenToolsGoal, MineStoneGoal as SurvivalMineStoneGoal,
            CraftStoneToolsGoal, HuntForFoodGoal, MineIronGoal, CraftIronToolsGoal, MineDiamondsGoal
        )
        from .creative_goals import (
            BuildStarterHomeGoal, BuildFarmGoal, DecorateHomeGoal,
            ExploreNearbyGoal, FindVillageGoal
        )
        
        # 注册所有可用目标（按优先级排序）
        self.available_goals: list[type[Goal]] = [
            # 生存目标（高优先级，必须完成才能进步）
            CollectWoodGoal,           # 优先级 100：采集初始木头
            CraftWoodenToolsGoal,      # 优先级 95：制作木工具
            SurvivalMineStoneGoal,     # 优先级 90：挖掘石头
            CraftStoneToolsGoal,       # 优先级 85：制作石工具
            HuntForFoodGoal,           # 优先级 80：狩猎食物
            MineIronGoal,              # 优先级 75：挖掘铁矿
            CraftIronToolsGoal,        # 优先级 70：制作铁工具
            MineDiamondsGoal,          # 优先级 65：挖掘钻石
            
            # 建筑目标（中优先级，提升生活质量）
            BuildStarterHomeGoal,      # 优先级 50：建造小屋
            BuildFarmGoal,             # 优先级 45：建造农场
            
            # 探索目标（中等优先级，发现新资源）
            ExploreNearbyGoal,         # 优先级 40：探索周围
            FindVillageGoal,           # 优先级 35：寻找村庄
            
            # 装饰目标（低优先级，锦上添花）
            DecorateHomeGoal,          # 优先级 30：装饰家园
            
            # 向后兼容的旧目标（最低优先级）
            GatherWoodGoal,            # 优先级 80
            MineStoneGoal,             # 优先级 70
            BuildShelterGoal,          # 优先级 60
        ]
    
    async def select_next_goal(self, bot: MCBot, current_goal: Optional[Goal] = None) -> Optional[Goal]:
        """选择下一个目标"""
        # 如果当前目标未完成且未阻塞，继续当前目标
        if current_goal and current_goal.status == GoalStatus.IN_PROGRESS:
            if not await current_goal.check_completion(bot):
                return current_goal
            else:
                current_goal.status = GoalStatus.COMPLETED
                current_goal.completed_at = time.time()
                logger.info("【目标完成】%s", current_goal.description)
        
        # 筛选可用目标
        available = []
        for goal_class in self.available_goals:
            goal_instance = goal_class()
            if await goal_instance.check_preconditions(bot):
                if not await goal_instance.check_completion(bot):
                    available.append(goal_instance)
        
        if not available:
            logger.info("【决策引擎】没有可用的目标")
            return None
        
        # 如果有 LLM，询问 LLM
        if self.llm_callback:
            selected = await self._llm_select(bot, available)
            if selected:
                return selected
        
        # 规则引擎：按优先级选择
        available.sort(key=lambda g: g.priority, reverse=True)
        selected = available[0]
        logger.info("【决策引擎】选择目标：%s（优先级 %d）", selected.description, selected.priority)
        return selected
    
    async def _llm_select(self, bot: MCBot, goals: list[Goal]) -> Optional[Goal]:
        """使用 LLM 选择目标"""
        try:
            status = bot.get_status()
            prompt = (
                f"你是 Minecraft 世界中的 AI 玩家。\n\n"
                f"当前状态：\n"
                f"- 生命值：{status.get('health', 20):.0f}/20\n"
                f"- 饥饿度：{status.get('food', 20)}/20\n"
                f"- 位置：{status.get('position', 'unknown')}\n\n"
                f"可选目标：\n"
            )
            for i, goal in enumerate(goals, 1):
                prompt += f"{i}. {goal.description}（优先级 {goal.priority}）\n"
            
            prompt += "\n请选择一个你最想完成的目标，只回复数字（1-{})：".format(len(goals))
            
            response = await self.llm_callback(prompt)
            if response:
                try:
                    choice = int(response.strip()) - 1
                    if 0 <= choice < len(goals):
                        logger.info("【LLM 决策】选择：%s", goals[choice].description)
                        return goals[choice]
                except ValueError:
                    pass
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 决策失败：%s", e)
        
        return None


# ============================================================
# 进度追踪器
# ============================================================
class ProgressTracker:
    """进度追踪器：持久化目标和进度"""
    
    def __init__(self, save_path: Path):
        self.save_path = save_path
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
    
    def save_progress(self, current_goal: Optional[Goal], completed_goals: list[Goal]) -> None:
        """保存进度"""
        data = {
            "current_goal": current_goal.to_dict() if current_goal else None,
            "completed_goals": [g.to_dict() for g in completed_goals],
            "saved_at": time.time(),
        }
        
        try:
            with open(self.save_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("【进度保存】已保存到 %s", self.save_path)
        except Exception as e:  # noqa: BLE001
            logger.error("保存进度失败：%s", e)
    
    def load_progress(self) -> tuple[Optional[dict], list[dict]]:
        """加载进度"""
        if not self.save_path.exists():
            return None, []
        
        try:
            with open(self.save_path, encoding="utf-8") as f:
                data = json.load(f)
            
            current = data.get("current_goal")
            completed = data.get("completed_goals", [])
            
            logger.info("【进度加载】已加载 %d 个已完成目标", len(completed))
            return current, completed
        except Exception as e:  # noqa: BLE001
            logger.error("加载进度失败：%s", e)
            return None, []


# ============================================================
# 目标管理器
# ============================================================
class GoalManager:
    """目标管理器：统筹目标的选择、执行、进度追踪
    
    整合决策引擎和进度追踪器，提供统一的目标管理接口。
    在后台循环中执行当前目标，并在目标完成时自动选择下一个。
    """
    
    def __init__(self, bot: MCBot, save_path: Path, llm_callback: Optional[callable] = None):
        self.bot = bot
        self.decision_engine = DecisionEngine(llm_callback)
        self.progress_tracker = ProgressTracker(save_path)
        
        self.current_goal: Optional[Goal] = None
        self.completed_goals: list[Goal] = []
        
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # 目标执行间隔（秒）
        self.execution_interval = 10.0
        
        # 发言回调
        self.speak_callback: Optional[callable] = None
    
    def load_saved_progress(self) -> None:
        """加载已保存的进度"""
        current_dict, completed_dicts = self.progress_tracker.load_progress()
        
        # TODO: 从字典重建 Goal 对象
        # 当前版本简化：不恢复之前的目标，总是重新选择
        logger.info("【目标管理器】已加载进度（简化版本：重新选择目标）")
    
    async def start(self) -> None:
        """启动目标管理器"""
        if self._running:
            logger.warning("目标管理器已在运行")
            return
        
        self._running = True
        self.load_saved_progress()
        
        # 启动后台循环
        self._task = asyncio.create_task(self._goal_execution_loop())
        logger.info("【目标管理器】已启动")
        
        if self.speak_callback:
            await self.speak_callback("（眼睛一亮）我知道要做什么了！")
    
    async def stop(self) -> None:
        """停止目标管理器"""
        if not self._running:
            return
        
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        # 保存进度
        if self.current_goal:
            self.progress_tracker.save_progress(self.current_goal, self.completed_goals)
        
        logger.info("【目标管理器】已停止")
    
    async def _goal_execution_loop(self) -> None:
        """目标执行循环"""
        while self._running:
            try:
                if not self.bot.connected:
                    await asyncio.sleep(5.0)
                    continue
                
                # 选择目标（如果没有当前目标）
                if self.current_goal is None:
                    self.current_goal = await self.decision_engine.select_next_goal(self.bot)
                    
                    if self.current_goal:
                        self.current_goal.status = GoalStatus.IN_PROGRESS
                        self.current_goal.started_at = time.time()
                        
                        msg = f"【新目标】{self.current_goal.description}"
                        logger.info(msg)
                        if self.speak_callback:
                            await self.speak_callback(f"（认真）接下来{self.current_goal.description}！")
                    else:
                        # 没有可用目标，休息一会
                        await asyncio.sleep(30.0)
                        continue
                
                # 检查当前目标是否完成
                if await self.current_goal.check_completion(self.bot):
                    self.current_goal.status = GoalStatus.COMPLETED
                    self.current_goal.completed_at = time.time()
                    self.completed_goals.append(self.current_goal)
                    
                    msg = f"【目标完成】{self.current_goal.description}"
                    logger.info(msg)
                    if self.speak_callback:
                        await self.speak_callback(f"（开心）{self.current_goal.description}完成啦～")
                    
                    # 保存进度
                    self.progress_tracker.save_progress(None, self.completed_goals)
                    
                    # 清空当前目标，下一轮选择新目标
                    self.current_goal = None
                    await asyncio.sleep(5.0)
                    continue
                
                # 检查前置条件
                if not await self.current_goal.check_preconditions(self.bot):
                    self.current_goal.status = GoalStatus.BLOCKED
                    logger.warning("【目标阻塞】%s 的前置条件未满足", self.current_goal.description)
                    
                    if self.speak_callback:
                        await self.speak_callback(f"（挠头）{self.current_goal.description}好像还做不了...")
                    
                    # 目标被阻塞，选择其他目标
                    self.current_goal = None
                    await asyncio.sleep(10.0)
                    continue
                
                # 执行一步
                should_continue, message = await self.current_goal.execute_step(self.bot)
                
                if message:
                    logger.info("【目标执行】%s: %s", self.current_goal.name, message)
                
                if not should_continue:
                    self.current_goal.status = GoalStatus.FAILED
                    logger.error("【目标失败】%s", self.current_goal.description)
                    
                    if self.speak_callback:
                        await self.speak_callback(f"（沮丧）{self.current_goal.description}失败了...")
                    
                    self.current_goal = None
                
                # 等待下一轮
                await asyncio.sleep(self.execution_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.exception("目标执行循环出错：%s", e)
                await asyncio.sleep(10.0)
    
    def get_current_status(self) -> dict:
        """获取当前状态"""
        return {
            "running": self._running,
            "current_goal": self.current_goal.to_dict() if self.current_goal else None,
            "completed_count": len(self.completed_goals),
        }
