"""完整的生存目标体系：从零开始到钻石装备

参考 Minecraft 原版进度系统和 Numen 的 tier_progression 技能，
实现完整的工具链和生存循环。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING, Optional

from .goal_system import Goal, GoalStatus

if TYPE_CHECKING:
    from .bot_client import MCBot

logger = logging.getLogger("astrbot_plugin_minecraft.survival_goals")


# ============================================================
# 阶段 1：获取木头和基础工具
# ============================================================
class CollectWoodGoal(Goal):
    """采集木头：生存的第一步
    
    前置条件：无（徒手可做）
    完成条件：背包中有至少 8 根原木
    执行步骤：
    1. 扫描附近的树木（oak_log, birch_log等）
    2. 移动到树旁边
    3. 徒手砍伐原木
    """
    
    def __init__(self, target_count: int = 8):
        super().__init__(
            name="collect_wood",
            description=f"采集 {target_count} 根原木（生存第一步）",
            priority=90,
        )
        self.target_count = target_count
        self.log_types = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log"]
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        return bot.connected
    
    async def check_completion(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        total_logs = sum(inventory.get(log_type, 0) for log_type in self.log_types)
        self.progress = min(1.0, total_logs / self.target_count)
        return total_logs >= self.target_count
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        """寻找并砍伐树木"""
        logger.info("【采集木头】寻找附近的树木...")
        
        # TODO: 实现扫描和砍伐逻辑
        # 1. scan_blocks(log_types, radius=30)
        # 2. 选择最近的树
        # 3. 移动过去
        # 4. 循环挖掘该树的所有原木
        
        # 当前简化版：随机移动
        if bot.position:
            bx, by, bz = bot.position
            target_x = bx + random.uniform(-15, 15)
            target_z = bz + random.uniform(-15, 15)
            
            if bot.action_queue:
                bot.action_queue.submit(
                    "move",
                    {"x": target_x, "z": target_z, "timeout": 20.0},
                    priority=self.priority
                )
            
            return True, f"正在寻找树木...（已有 {self._count_logs(bot)}/{self.target_count} 根）"
        
        return False, "无法获取位置"
    
    def _count_logs(self, bot: MCBot) -> int:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        return sum(inventory.get(log_type, 0) for log_type in self.log_types)


class CraftWoodenToolsGoal(Goal):
    """制作木制工具：木镐是挖石头的前提
    
    前置条件：至少 4 根原木
    完成条件：拥有木镐、木剑、工作台
    执行步骤：
    1. 将原木合成木板
    2. 用木板合成工作台
    3. 放置工作台
    4. 用木板合成木棍
    5. 制作木镐和木剑
    """
    
    def __init__(self):
        super().__init__(
            name="craft_wooden_tools",
            description="制作木制工具（木镐、木剑、工作台）",
            priority=85,
        )
        self.crafting_table_placed = False
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        # 检查是否有足够的原木
        log_types = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log"]
        total_logs = sum(inventory.get(log_type, 0) for log_type in log_types)
        
        if total_logs < 4:
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        has_pickaxe = inventory.get("wooden_pickaxe", 0) > 0
        has_sword = inventory.get("wooden_sword", 0) > 0
        has_table = inventory.get("crafting_table", 0) > 0 or self.crafting_table_placed
        
        completed = has_pickaxe and has_sword and has_table
        
        # 简单进度计算
        progress_count = sum([has_pickaxe, has_sword, has_table])
        self.progress = progress_count / 3.0
        
        return completed
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        """执行合成步骤"""
        logger.info("【制作工具】开始合成木制工具...")
        
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        # 检查当前拥有什么
        has_pickaxe = inventory.get("wooden_pickaxe", 0) > 0
        has_sword = inventory.get("wooden_sword", 0) > 0
        has_planks = inventory.get("oak_planks", 0) + inventory.get("spruce_planks", 0) + \
                     inventory.get("birch_planks", 0) + inventory.get("jungle_planks", 0) > 0
        has_sticks = inventory.get("stick", 0) >= 2
        
        # TODO: 实现真实的合成逻辑
        # 1. lookup_recipe("oak_planks") -> 找到配方
        # 2. transfer 原木到合成格
        # 3. transfer 木板出来
        # 4. lookup_recipe("crafting_table")
        # 5. 制作工作台并放置
        # 6. interact_at 工作台打开 GUI
        # 7. 制作木镐和木剑
        
        # 当前简化版：假设玩家手动合成
        if not has_pickaxe:
            return True, "正在制作木镐...（需要手动合成：3木板+2木棍）"
        elif not has_sword:
            return True, "正在制作木剑...（需要手动合成：2木板+1木棍）"
        else:
            return True, "工具制作中..."


# ============================================================
# 阶段 2：石器时代
# ============================================================
class MineStoneGoal(Goal):
    """挖掘圆石：升级到石器工具
    
    前置条件：拥有任意镐子
    完成条件：背包中有至少 20 块圆石
    """
    
    def __init__(self, target_count: int = 20):
        super().__init__(
            name="mine_stone",
            description=f"挖掘 {target_count} 块圆石（升级石器）",
            priority=80,
        )
        self.target_count = target_count
    
    async def check_preconditions(self, bot: MCBot) -> bool:
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
        logger.info("【挖掘石头】寻找石头矿脉...")
        
        # TODO: 实现挖掘逻辑
        # 1. 装备镐子 equip_item("wooden_pickaxe")
        # 2. 向下挖或寻找石头 scan_blocks(["stone"], radius=20)
        # 3. mine(x, y, z) 挖掘石头
        
        current = self._count_cobblestone(bot)
        return True, f"正在挖掘石头...（已有 {current}/{self.target_count} 块）"
    
    def _count_cobblestone(self, bot: MCBot) -> int:
        status = bot.get_status()
        return status.get("inventory", {}).get("cobblestone", 0)


class CraftStoneToolsGoal(Goal):
    """制作石制工具：石镐可以挖铁矿
    
    前置条件：至少 11 块圆石
    完成条件：拥有石镐、石剑、熔炉
    """
    
    def __init__(self):
        super().__init__(
            name="craft_stone_tools",
            description="制作石制工具（石镐、石剑、熔炉）",
            priority=75,
        )
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        cobblestone = inventory.get("cobblestone", 0)
        
        if cobblestone < 11:  # 石镐3 + 石剑3 + 熔炉8 = 14，简化为11
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        has_pickaxe = inventory.get("stone_pickaxe", 0) > 0
        has_sword = inventory.get("stone_sword", 0) > 0
        has_furnace = inventory.get("furnace", 0) > 0
        
        completed = has_pickaxe and has_sword and has_furnace
        progress_count = sum([has_pickaxe, has_sword, has_furnace])
        self.progress = progress_count / 3.0
        
        return completed
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        # TODO: 合成石制工具
        return True, "正在制作石制工具..."


# ============================================================
# 阶段 3：获取食物
# ============================================================
class HuntForFoodGoal(Goal):
    """狩猎获取食物：维持生存必需
    
    前置条件：拥有剑
    完成条件：背包中有至少 8 个熟食
    """
    
    def __init__(self, target_count: int = 8):
        super().__init__(
            name="hunt_for_food",
            description=f"狩猎并烹饪食物（{target_count}个熟肉）",
            priority=70,
        )
        self.target_count = target_count
        self.cooked_foods = ["cooked_beef", "cooked_porkchop", "cooked_chicken", "cooked_mutton", "cooked_rabbit"]
        self.raw_foods = ["beef", "porkchop", "chicken", "mutton", "rabbit"]
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        swords = ["wooden_sword", "stone_sword", "iron_sword", "golden_sword", "diamond_sword", "netherite_sword"]
        has_sword = any(inventory.get(s, 0) > 0 for s in swords)
        
        if not has_sword:
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        total_cooked = sum(inventory.get(food, 0) for food in self.cooked_foods)
        self.progress = min(1.0, total_cooked / self.target_count)
        
        return total_cooked >= self.target_count
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        logger.info("【狩猎食物】寻找动物...")
        
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        # 检查是否有生肉需要烹饪
        total_raw = sum(inventory.get(food, 0) for food in self.raw_foods)
        total_cooked = sum(inventory.get(food, 0) for food in self.cooked_foods)
        
        if total_raw > 0:
            # TODO: 使用熔炉烹饪
            # 1. 找到或放置熔炉
            # 2. interact_at 熔炉
            # 3. transfer 生肉到上方槽位
            # 4. transfer 煤炭/木板到燃料槽
            # 5. wait 烹饪完成
            # 6. transfer 熟肉出来
            return True, f"正在烹饪食物...（生肉 {total_raw} / 熟肉 {total_cooked}/{self.target_count}）"
        else:
            # TODO: 狩猎动物
            # 1. scan_nearby_entities 寻找牛/猪/鸡/羊
            # 2. 装备剑
            # 3. attack(entity_id) 击杀动物
            # 4. collect_items 捡起掉落物
            return True, f"正在狩猎动物...（已有 {total_cooked}/{self.target_count} 熟肉）"


# ============================================================
# 阶段 4：铁器时代
# ============================================================
class MineIronGoal(Goal):
    """挖掘铁矿：进入铁器时代
    
    前置条件：拥有石镐或更好的镐子
    完成条件：背包中有至少 10 个铁锭
    """
    
    def __init__(self, target_count: int = 10):
        super().__init__(
            name="mine_iron",
            description=f"挖掘并冶炼 {target_count} 个铁锭",
            priority=65,
        )
        self.target_count = target_count
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        # 需要石镐或更好
        valid_pickaxes = ["stone_pickaxe", "iron_pickaxe", "golden_pickaxe", "diamond_pickaxe", "netherite_pickaxe"]
        has_pickaxe = any(inventory.get(p, 0) > 0 for p in valid_pickaxes)
        
        if not has_pickaxe:
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        iron_ingots = inventory.get("iron_ingot", 0)
        self.progress = min(1.0, iron_ingots / self.target_count)
        
        return iron_ingots >= self.target_count
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        logger.info("【挖掘铁矿】寻找铁矿...")
        
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        raw_iron = inventory.get("raw_iron", 0)
        iron_ingots = inventory.get("iron_ingot", 0)
        
        if raw_iron > 0:
            # 需要冶炼
            return True, f"正在冶炼铁矿...（生铁 {raw_iron} / 铁锭 {iron_ingots}/{self.target_count}）"
        else:
            # TODO: 下矿挖铁
            # 1. 前往 Y=16 层（铁矿最多）
            # 2. scan_blocks(["iron_ore", "deepslate_iron_ore"])
            # 3. mine铁矿
            # 4. 返回地面冶炼
            return True, f"正在挖掘铁矿...（已有 {iron_ingots}/{self.target_count} 铁锭）"


class CraftIronToolsGoal(Goal):
    """制作铁制工具和护甲：大幅提升生存能力
    
    前置条件：至少 24 个铁锭
    完成条件：拥有铁镐、铁剑、铁护甲
    """
    
    def __init__(self):
        super().__init__(
            name="craft_iron_tools",
            description="制作铁制装备（铁镐、铁剑、全套铁甲）",
            priority=60,
        )
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        iron_ingots = inventory.get("iron_ingot", 0)
        
        # 铁镐3 + 铁剑2 + 铁甲(头盔5+胸甲8+护腿7+靴子4) = 29，简化为24
        if iron_ingots < 24:
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        has_pickaxe = inventory.get("iron_pickaxe", 0) > 0
        has_sword = inventory.get("iron_sword", 0) > 0
        has_helmet = inventory.get("iron_helmet", 0) > 0
        has_chestplate = inventory.get("iron_chestplate", 0) > 0
        has_leggings = inventory.get("iron_leggings", 0) > 0
        has_boots = inventory.get("iron_boots", 0) > 0
        
        completed = has_pickaxe and has_sword and has_helmet and has_chestplate and has_leggings and has_boots
        
        progress_count = sum([has_pickaxe, has_sword, has_helmet, has_chestplate, has_leggings, has_boots])
        self.progress = progress_count / 6.0
        
        return completed
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        # TODO: 制作铁装备
        return True, "正在制作铁装备..."


# ============================================================
# 阶段 5：钻石！
# ============================================================
class MineDiamondsGoal(Goal):
    """挖掘钻石：游戏的里程碑
    
    前置条件：拥有铁镐或更好的镐子
    完成条件：背包中有至少 5 颗钻石
    """
    
    def __init__(self, target_count: int = 5):
        super().__init__(
            name="mine_diamonds",
            description=f"挖掘 {target_count} 颗钻石（游戏里程碑！）",
            priority=55,
        )
        self.target_count = target_count
    
    async def check_preconditions(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        valid_pickaxes = ["iron_pickaxe", "golden_pickaxe", "diamond_pickaxe", "netherite_pickaxe"]
        has_pickaxe = any(inventory.get(p, 0) > 0 for p in valid_pickaxes)
        
        if not has_pickaxe:
            self.status = GoalStatus.BLOCKED
            return False
        
        return True
    
    async def check_completion(self, bot: MCBot) -> bool:
        status = bot.get_status()
        inventory = status.get("inventory", {})
        
        diamonds = inventory.get("diamond", 0)
        self.progress = min(1.0, diamonds / self.target_count)
        
        return diamonds >= self.target_count
    
    async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
        logger.info("【挖掘钻石】前往深层矿洞...")
        
        status = bot.get_status()
        inventory = status.get("inventory", {})
        diamonds = inventory.get("diamond", 0)
        
        # TODO: 挖钻石
        # 1. 前往 Y=-58 ~ -59 层（钻石最多，参考Numen）
        # 2. scan_blocks(["diamond_ore", "deepslate_diamond_ore"])
        # 3. 小心岩浆，检查健康值
        # 4. mine钻石矿
        
        return True, f"正在寻找钻石...（已有 {diamonds}/{self.target_count} 颗）"
