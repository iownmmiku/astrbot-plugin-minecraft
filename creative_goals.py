"""建筑和探索目标：让机器人有创造力和好奇心

不只是生存，还要建造、探索、装饰自己的世界。
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Optional

from .goal_system import Goal, GoalStatus

if TYPE_CHECKING:
    from .bot_client import MCBot

logger = logging.getLogger("astrbot_plugin_minecraft.creative_goals")


# ============================================================
# 建筑目标
# ============================================================
class BuildStarterHomeGoal(Goal):
    """建造新手小屋：一个舒适的家
    
    前置条件：至少 64 块建筑材料（木板或圆石）
    完成条件：建造一个 7x4x7 的房子，包含门、窗户、床、工作台、箱子、熔炉
    
    设计灵感：
    - 参考 Numen 的 building_design 技能
    - 不只是堆方块，要有设计感
    - 包含基础生活设施
    """
    
    def __init__(self):
        super().__init__(
            name="build_starter_home",
            description="建造新手小屋（7x4x7，包含生活设施）",
            priority=50,
        )
        self.build_plan = {
            "size": (7, 4, 7),
            "materials_needed": 100,  # 估算
            "facilities": ["door", "bed", "crafting_table", "chest", "furnace"],
        }
        self.build_stage = "foundation"  # foundation -> walls -> roof -> interior
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        # 检查建筑材料
        building_materials = (
            inventory.get("cobblestone", 0) +
            inventory.get("oak_planks", 0) +
            inventory.get("spruce_planks", 0) +
            inventory.get("birch_planks", 0)
        )
        
        if building_materials < 64:
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        # 简化版本：通过 metadata 标记完成
        completed = self.metadata.get("home_built", False)
        
        # 根据建造阶段更新进度
        stage_progress = {
            "foundation": 0.25,
            "walls": 0.50,
            "roof": 0.75,
            "interior": 1.0,
        }
        self.progress = stage_progress.get(self.build_stage, 0.0)
        
        return completed
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        logger.info("【建造小屋】当前阶段：%s", self.build_stage)
        
        # TODO: 实现建造逻辑
        # 1. 选择建造位置（平坦地面）
        # 2. 打地基（放置基础方块）
        # 3. 建造墙壁（留出门窗位置）
        # 4. 搭建屋顶
        # 5. 放置内部设施
        
        stage_messages = {
            "foundation": "正在打地基...（选择平坦位置，标记建造区域）",
            "walls": "正在建造墙壁...（留出门窗位置）",
            "roof": "正在搭建屋顶...（防止怪物从上方进入）",
            "interior": "正在布置内部设施...（床、工作台、箱子、熔炉）",
        }
        
        return True, stage_messages.get(self.build_stage, "建造中...")


class BuildFarmGoal(Goal):
    """建造农场：自动化食物来源
    
    前置条件：
    - 有锄头
    - 有种子或作物
    - 靠近水源
    
    完成条件：建造 9x9 的农田，种植作物
    """
    
    def __init__(self):
        super().__init__(
            name="build_farm",
            description="建造 9x9 农场（自动化食物来源）",
            priority=45,
        )
        self.farm_size = 9
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        # 需要锄头
        hoes = ["wooden_hoe", "stone_hoe", "iron_hoe", "golden_hoe", "diamond_hoe", "netherite_hoe"]
        has_hoe = any(inventory.get(h, 0) > 0 for h in hoes)
        
        # 需要种子
        seeds = ["wheat_seeds", "carrot", "potato", "beetroot_seeds"]
        has_seeds = any(inventory.get(s, 0) > 0 for s in seeds)
        
        if not (has_hoe and has_seeds):
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        completed = self.metadata.get("farm_built", False)
        return completed
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        # TODO: 建造农场
        # 1. 寻找水源附近的平地
        # 2. 使用锄头耕地
        # 3. 种植种子
        # 4. 围栏防止动物踩踏
        
        return True, "正在建造农场...（寻找水源，耕地种植）"


class DecorateHomeGoal(Goal):
    """装饰家园：让家更温馨
    
    前置条件：已经有基础建筑
    完成条件：添加装饰元素（画、花盆、地毯、灯具等）
    
    这个目标体现机器人的"个性"和"品味"
    """
    
    def __init__(self):
        super().__init__(
            name="decorate_home",
            description="装饰家园（画、花、地毯、灯具）",
            priority=30,  # 低优先级，生存需求满足后再做
        )
        self.decoration_items = ["painting", "flower_pot", "carpet", "torch", "lantern"]
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        # 需要已经有家
        # TODO: 检查是否已完成建造目标
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        completed = self.metadata.get("decorated", False)
        return completed
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        # TODO: 装饰
        # 1. 收集装饰材料
        # 2. 制作装饰物品
        # 3. 放置到合适的位置
        
        return True, "正在装饰家园...（让家更温馨）"


# ============================================================
# 探索目标
# ============================================================
class ExploreNearbyGoal(Goal):
    """探索附近区域：发现新地点
    
    完成条件：探索半径 100 格内的区域，记录重要地点
    
    重要地点包括：
    - 村庄
    - 沙漠神殿、丛林神庙
    - 矿洞入口
    - 峡谷、熔岩湖
    - 稀有生物群系
    """
    
    def __init__(self, explore_radius: int = 100):
        super().__init__(
            name="explore_nearby",
            description=f"探索周围 {explore_radius} 格区域",
            priority=40,
        )
        self.explore_radius = explore_radius
        self.discovered_locations = []
        self.explored_area = 0
        self.target_area = 3.14 * explore_radius * explore_radius
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        # 探索前最好有基本装备
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        # 建议有剑和食物
        has_sword = any("sword" in item for item in inventory.keys())
        has_food = status.get("food", 20) > 10
        
        return has_sword and has_food
    
    async def check_completion(self, bot: MCBot) -> bool:
        # 简化版本：探索一定时间或距离
        self.progress = min(1.0, self.explored_area / self.target_area)
        return self.progress >= 1.0
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        logger.info("【探索区域】发现新地点...")
        
        # TODO: 实现探索逻辑
        # 1. 螺旋式移动，覆盖区域
        # 2. locate_structure 寻找结构
        # 3. 记录重要地点坐标
        # 4. 在地图上标记
        
        if bot.position:
            # 随机选择方向探索
            bx, by, bz = bot.position
            angle = random.uniform(0, 6.28)  # 0-2π
            distance = 30
            
            import math
            target_x = bx + math.cos(angle) * distance
            target_z = bz + math.sin(angle) * distance
            
            if bot.action_queue:
                bot.action_queue.submit(
                    "move",
                    {"x": target_x, "z": target_z, "timeout": 60.0},
                    priority=self.priority
                )
            
            self.explored_area += 900  # 粗略估算
            
            return True, f"正在探索...（已探索 {self.progress*100:.1f}%）"
        
        return False, "无法获取位置"


class FindVillageGoal(Goal):
    """寻找村庄：与村民交易
    
    完成条件：找到村庄并记录坐标
    """
    
    def __init__(self):
        super().__init__(
            name="find_village",
            description="寻找村庄（与村民交易）",
            priority=35,
        )
        self.village_location = None
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        return bot.connected
    
    async def check_completion(self, bot: MCBot) -> bool:
        return self.village_location is not None
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        # TODO: 使用 locate_structure("village")
        # 1. 调用 locate_structure
        # 2. 记录坐标
        # 3. 移动到村庄
        
        return True, "正在寻找村庄...（使用村庄定位）"


# ============================================================
# 社交和协作目标
# ============================================================
class HelpPlayerGoal(Goal):
    """帮助玩家：响应玩家的请求
    
    这是一个动态目标，根据玩家的聊天内容生成
    
    例如：
    - "给我一些木头" -> 采集木头并给玩家
    - "帮我挖个洞" -> 挖掘指定区域
    - "跟我来" -> 跟随玩家
    """
    
    def __init__(self, player_name: str, request: str, task_type: str):
        super().__init__(
            name=f"help_{player_name}",
            description=f"帮助 {player_name}：{request}",
            priority=95,  # 高优先级，玩家请求优先处理
        )
        self.player_name = player_name
        self.request = request
        self.task_type = task_type  # "give_items", "dig", "follow", "build"
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        # 检查玩家是否在线
        players = bot.player_names()
        return self.player_name in players
    
    async def check_completion(self, bot: MCBot) -> bool:
        completed = self.metadata.get("task_completed", False)
        return completed
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        # TODO: 根据 task_type 执行不同的任务
        task_messages = {
            "give_items": f"正在收集物品给 {self.player_name}...",
            "dig": f"正在为 {self.player_name} 挖掘...",
            "follow": f"正在跟随 {self.player_name}...",
            "build": f"正在为 {self.player_name} 建造...",
        }
        
        return True, task_messages.get(self.task_type, "正在执行任务...")


class ShareResourcesGoal(Goal):
    """分享资源：主动帮助其他玩家
    
    当机器人资源充足时，主动分享给需要的玩家
    体现"善良"和"慷慨"的性格
    """
    
    def __init__(self, target_player: str, item_type: str, amount: int):
        super().__init__(
            name="share_resources",
            description=f"分享 {amount} 个 {item_type} 给 {target_player}",
            priority=25,
        )
        self.target_player = target_player
        self.item_type = item_type
        self.amount = amount
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        # 检查是否有足够的物品可分享
        current_amount = inventory.get(self.item_type, 0)
        
        # 只在有多余时分享（保留一半给自己）
        if current_amount < self.amount * 2:
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        completed = self.metadata.get("shared", False)
        return completed
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        # TODO: 分享物品
        # 1. 找到目标玩家
        # 2. 靠近玩家
        # 3. drop_items 在玩家附近
        # 4. 在聊天中告知
        
        return True, f"正在分享资源给 {self.target_player}..."
