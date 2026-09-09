"""测试复合动作逻辑"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os
import math

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class MockBot:
    """模拟 MCBot，包含复合动作方法"""
    REACH_DISTANCE = 4.0
    
    def __init__(self):
        self.position = (0.0, 64.0, 0.0)
        self.connected = True
        self.spawn_position = (0.0, 64.0, 0.0)
        self.move_to = AsyncMock(return_value=None)
        self.mine = AsyncMock(return_value=None)
        self.move_and_mine = AsyncMock()
    
    async def move_and_mine_impl(self, x: int, y: int, z: int, *, timeout: float = 90.0):
        """实际的 move_and_mine 实现逻辑"""
        if not self.connected:
            return "机器人未连接"
        if self.position is None:
            return "尚未同步位置"
        
        bx, by, bz = self.position
        dist = math.hypot(x - bx, z - bz)
        
        if dist > self.REACH_DISTANCE:
            dx, dz = x - bx, z - bz
            norm = math.hypot(dx, dz)
            approach_x = x - dx / norm * (self.REACH_DISTANCE - 0.5)
            approach_z = z - dz / norm * (self.REACH_DISTANCE - 0.5)
            
            move_timeout = min(timeout * 0.7, 60.0)
            err = await self.move_to(approach_x, approach_z, timeout=move_timeout)
            if err:
                return err
        
        return await self.mine(x, y, z)
    
    async def collect_nearby_blocks_impl(self, center_x: int, center_y: int, center_z: int, radius: int = 5, *, timeout: float = 180.0):
        """实际的 collect_nearby_blocks 实现逻辑"""
        if not self.connected:
            return "机器人未连接"
        
        import time
        deadline = time.monotonic() + timeout
        collected = 0
        total = (2 * radius + 1) ** 2
        
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if time.monotonic() >= deadline:
                    return f"收集超时，已收集 {collected}/{total} 个方块"
                
                target_x = center_x + dx
                target_z = center_z + dz
                
                remaining = deadline - time.monotonic()
                err = await self.move_and_mine(target_x, center_y, target_z, timeout=remaining)
                if err and "超时" not in err:
                    return f"收集中断（{err}），已收集 {collected}/{total} 个方块"
                
                collected += 1
        
        return None
    
    async def patrol_area_impl(self, x1: float, z1: float, x2: float, z2: float, *, loops: int = 1):
        """实际的 patrol_area 实现逻辑"""
        if not self.connected:
            return "机器人未连接"
        
        corners = [(x1, z1), (x2, z1), (x2, z2), (x1, z2)]
        
        for _ in range(loops):
            for x, z in corners:
                err = await self.move_to(x, z)
                if err:
                    return err
        
        return None
    
    async def return_to_spawn_impl(self):
        """实际的 return_to_spawn 实现逻辑"""
        if not self.connected:
            return "机器人未连接"
        if self.spawn_position is None:
            return "未记录出生点"
        
        sx, sy, sz = self.spawn_position
        return await self.move_to(sx, sz)


class TestCompoundActions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """创建一个模拟的 MCBot 实例"""
        self.bot = MockBot()

    async def test_move_and_mine_within_reach(self):
        """测试 move_and_mine：目标在可达范围内，直接挖掘"""
        self.bot.position = (10.0, 64.0, 20.0)
        
        result = await self.bot.move_and_mine_impl(12, 64, 21)
        
        # 距离约 2.2 格，在 4 格内，不需要移动
        self.bot.move_to.assert_not_called()
        self.bot.mine.assert_called_once_with(12, 64, 21)
        self.assertIsNone(result)

    async def test_move_and_mine_need_approach(self):
        """测试 move_and_mine：需要先移动接近"""
        self.bot.position = (0.0, 64.0, 0.0)
        
        result = await self.bot.move_and_mine_impl(100, 64, 200)
        
        # 距离超过 4 格，需要先移动到接近点
        self.bot.move_to.assert_called_once()
        call_args = self.bot.move_to.call_args
        # 接近点应该在目标周围 REACH_DISTANCE 内
        ax, az = call_args[0][0], call_args[0][1]
        import math
        dist_to_target = math.hypot(ax - 100, az - 200)
        self.assertLess(dist_to_target, 4.0)
        
        self.bot.mine.assert_called_once_with(100, 64, 200)
        self.assertIsNone(result)

    async def test_move_and_mine_move_fails(self):
        """测试 move_and_mine：移动失败时返回错误"""
        self.bot.position = (0.0, 64.0, 0.0)
        self.bot.move_to.return_value = "移动超时"
        
        result = await self.bot.move_and_mine_impl(100, 64, 200)
        
        self.assertEqual(result, "移动超时")
        self.bot.mine.assert_not_called()

    async def test_collect_nearby_blocks(self):
        """测试 collect_nearby_blocks：螺旋扫描收集方块"""
        self.bot.position = (0.0, 64.0, 0.0)
        self.bot.move_and_mine = AsyncMock(return_value=None)
        
        result = await self.bot.collect_nearby_blocks_impl(0, 64, 0, radius=2, timeout=60.0)
        
        # 半径 2 应该扫描 (2*2+1)^2 = 25 个坐标
        self.assertEqual(self.bot.move_and_mine.call_count, 25)
        self.assertIsNone(result)

    async def test_collect_nearby_timeout(self):
        """测试 collect_nearby_blocks：超时时返回部分完成"""
        self.bot.position = (0.0, 64.0, 0.0)
        # 每次挖掘耗时 1 秒
        async def slow_mine(*a, **kw):
            await asyncio.sleep(1)
            return None
        self.bot.move_and_mine = AsyncMock(side_effect=slow_mine)
        
        # 半径 2 = 25 个方块，但只给 2.5 秒超时，只能完成部分
        result = await self.bot.collect_nearby_blocks_impl(0, 64, 0, radius=2, timeout=2.5)
        
        # 应该处理了 2-3 个方块后超时
        self.assertGreaterEqual(self.bot.move_and_mine.call_count, 2)
        self.assertLess(self.bot.move_and_mine.call_count, 25)
        self.assertIn("超时", result)
        self.assertIn("已收集", result)

    async def test_patrol_area(self):
        """测试 patrol_area：在矩形区域巡逻"""
        self.bot.position = (0.0, 64.0, 0.0)
        self.bot.move_to = AsyncMock(return_value=None)
        
        result = await self.bot.patrol_area_impl(0.0, 0.0, 100.0, 100.0, loops=2)
        
        # 2 圈巡逻 = 8 次 move_to 调用（每圈 4 个角点）
        self.assertEqual(self.bot.move_to.call_count, 8)
        
        # 检查四个角点
        calls = self.bot.move_to.call_args_list
        self.assertEqual(calls[0][0], (0.0, 0.0))
        self.assertEqual(calls[1][0], (100.0, 0.0))
        self.assertEqual(calls[2][0], (100.0, 100.0))
        self.assertEqual(calls[3][0], (0.0, 100.0))
        # 第二圈
        self.assertEqual(calls[4][0], (0.0, 0.0))
        
        self.assertIsNone(result)

    async def test_patrol_area_move_fails(self):
        """测试 patrol_area：移动失败时停止巡逻"""
        self.bot.position = (0.0, 64.0, 0.0)
        self.bot.move_to = AsyncMock(side_effect=[None, None, "移动超时", None])
        
        result = await self.bot.patrol_area_impl(0.0, 0.0, 100.0, 100.0, loops=2)
        
        # 前两次成功，第三次失败，应该停止
        self.assertEqual(self.bot.move_to.call_count, 3)
        self.assertEqual(result, "移动超时")

    async def test_return_to_spawn_success(self):
        """测试 return_to_spawn：成功返回出生点"""
        self.bot.position = (100.0, 64.0, 200.0)
        self.bot.spawn_position = (0.0, 64.0, 0.0)
        self.bot.move_to = AsyncMock(return_value=None)
        
        result = await self.bot.return_to_spawn_impl()
        
        self.bot.move_to.assert_called_once_with(0.0, 0.0)
        self.assertIsNone(result)

    async def test_return_to_spawn_no_spawn_recorded(self):
        """测试 return_to_spawn：未记录出生点时返回错误"""
        self.bot.spawn_position = None
        
        result = await self.bot.return_to_spawn_impl()
        
        self.assertEqual(result, "未记录出生点")
        self.bot.move_to.assert_not_called()

    async def test_return_to_spawn_not_connected(self):
        """测试 return_to_spawn：未连接时返回错误"""
        self.bot.connected = False
        self.bot.spawn_position = (0.0, 64.0, 0.0)
        
        result = await self.bot.return_to_spawn_impl()
        
        self.assertEqual(result, "机器人未连接")
        self.bot.move_to.assert_not_called()


if __name__ == "__main__":
    unittest.main()
