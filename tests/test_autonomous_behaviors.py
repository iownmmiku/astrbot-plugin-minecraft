"""测试自主行为系统"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from autonomous_behaviors import (
    Behavior,
    EatWhenHungryBehavior,
    FleeFromMobsBehavior,
    AutonomousBehaviorManager,
)


class MockBot:
    """模拟 MCBot 用于测试"""
    def __init__(self):
        self.food = 20
        self.connected = True
        self.position = (0.0, 64.0, 0.0)
        self.entities = {}
        self.entity_id = 0
        self.action_queue = MagicMock()
        self.action_queue.submit = MagicMock(return_value="action-id-123")


class TestEatWhenHungryBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_should_trigger_when_hungry(self):
        """测试饥饿时触发条件"""
        bot = MockBot()
        bot.food = 8
        behavior = EatWhenHungryBehavior(threshold=10)
        
        result = await behavior.should_trigger(bot)
        self.assertTrue(result)

    async def test_should_not_trigger_when_not_hungry(self):
        """测试不饥饿时不触发"""
        bot = MockBot()
        bot.food = 15
        behavior = EatWhenHungryBehavior(threshold=10)
        
        result = await behavior.should_trigger(bot)
        self.assertFalse(result)

    async def test_execute_logs_warning(self):
        """测试执行时记录日志（当前版本未实现真实进食）"""
        bot = MockBot()
        bot.food = 5
        behavior = EatWhenHungryBehavior(threshold=10)
        
        result = await behavior.execute(bot)
        
        # execute 返回 None，实际逻辑是记录日志
        self.assertIsNone(result)


class TestFleeFromMobsBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_should_trigger_when_entity_nearby(self):
        """测试实体靠近时触发"""
        bot = MockBot()
        bot.position = (0.0, 64.0, 0.0)
        bot.entities = {
            "entity-1": {"x": 3.0, "y": 64.0, "z": 2.0, "type": "zombie"}
        }
        behavior = FleeFromMobsBehavior()
        
        result = await behavior.should_trigger(bot)
        self.assertTrue(result)

    async def test_should_not_trigger_when_no_entities(self):
        """测试无实体时不触发"""
        bot = MockBot()
        bot.entities = {}
        behavior = FleeFromMobsBehavior()
        
        result = await behavior.should_trigger(bot)
        self.assertFalse(result)

    async def test_should_not_trigger_when_entities_far(self):
        """测试实体距离较远时不触发"""
        bot = MockBot()
        bot.position = (0.0, 64.0, 0.0)
        bot.entities = {
            "entity-1": {"x": 10.0, "y": 64.0, "z": 10.0, "type": "zombie"}
        }
        behavior = FleeFromMobsBehavior()
        
        result = await behavior.should_trigger(bot)
        self.assertFalse(result)

    async def test_execute_submits_flee_action(self):
        """测试执行时提交高优先级逃离动作"""
        bot = MockBot()
        bot.position = (0.0, 64.0, 0.0)
        bot.entity_id = 999
        bot.entities = {
            "entity-1": {"x": 3.0, "y": 64.0, "z": 2.0, "type": "zombie"}
        }
        behavior = FleeFromMobsBehavior()
        
        result = await behavior.execute(bot)
        
        # 应该提交了移动动作
        bot.action_queue.submit.assert_called_once()
        call_args = bot.action_queue.submit.call_args
        self.assertEqual(call_args[0][0], "move")
        self.assertEqual(call_args[1]["priority"], 80)
        
        # 逃离方向应该远离实体
        params = call_args[0][1]
        import math
        flee_x, flee_z = params["x"], params["z"]
        # 逃离点应该在反方向
        self.assertLess(flee_x, 0.0)  # 实体在 x=3，应该往 x<0 方向逃
        self.assertLess(flee_z, 0.0)  # 实体在 z=2，应该往 z<0 方向逃
        
        self.assertIsNone(result)

    async def test_execute_handles_no_action_queue(self):
        """测试未启用动作队列时使用阻塞执行"""
        bot = MockBot()
        bot.position = (0.0, 64.0, 0.0)
        bot.entity_id = 999
        bot.entities = {
            "entity-1": {"x": 3.0, "y": 64.0, "z": 2.0, "type": "zombie"}
        }
        bot.action_queue = None
        bot.move_to = AsyncMock(return_value=None)
        behavior = FleeFromMobsBehavior()
        
        result = await behavior.execute(bot)
        
        # 应该调用了阻塞 move_to
        bot.move_to.assert_called_once()
        self.assertIsNone(result)


class TestAutonomousBehaviorManager(unittest.IsolatedAsyncioTestCase):
    async def test_manager_registers_behaviors(self):
        """测试管理器注册行为"""
        bot = MockBot()
        config = {
            "enable_autonomous_behaviors": True,
            "auto_eat_threshold": 10,
            "auto_flee_enabled": True,
        }
        manager = AutonomousBehaviorManager(bot, config)
        
        self.assertEqual(len(manager._behaviors), 2)
        self.assertIsInstance(manager._behaviors[0], EatWhenHungryBehavior)
        self.assertIsInstance(manager._behaviors[1], FleeFromMobsBehavior)

    async def test_manager_respects_flee_disabled(self):
        """测试关闭自动逃离时不注册 FleeFromMobsBehavior"""
        bot = MockBot()
        config = {
            "enable_autonomous_behaviors": True,
            "auto_eat_threshold": 10,
            "auto_flee_enabled": False,
        }
        manager = AutonomousBehaviorManager(bot, config)
        
        self.assertEqual(len(manager._behaviors), 1)
        self.assertIsInstance(manager._behaviors[0], EatWhenHungryBehavior)

    async def test_manager_run_checks_behaviors(self):
        """测试管理器循环检查行为触发"""
        bot = MockBot()
        bot.food = 5  # 触发进食
        config = {
            "enable_autonomous_behaviors": True,
            "auto_eat_threshold": 10,
            "auto_flee_enabled": False,
        }
        manager = AutonomousBehaviorManager(bot, config)
        
        # 启动管理器并运行一小段时间
        run_task = asyncio.create_task(manager.run())
        await asyncio.sleep(1.5)  # check_interval=1.0，等待至少一次检查
        manager.stop()
        await asyncio.sleep(0.1)
        
        # 应该触发了进食行为（虽然只是记录日志）
        # 验证 _running 状态
        self.assertFalse(manager._running)

    async def test_manager_stop(self):
        """测试停止管理器"""
        bot = MockBot()
        manager = AutonomousBehaviorManager(bot, {})
        
        run_task = asyncio.create_task(manager.run())
        await asyncio.sleep(0.1)
        self.assertTrue(manager._running)
        
        manager.stop()
        await asyncio.sleep(0.1)
        self.assertFalse(manager._running)

    async def test_behavior_check_interval(self):
        """测试行为的 check_interval 属性"""
        bot = MockBot()
        bot.food = 5
        
        behavior = EatWhenHungryBehavior(threshold=10)
        
        # 检查默认的 check_interval
        self.assertEqual(behavior.check_interval, 5.0)
        
        # 修改 check_interval
        behavior.check_interval = 2.0
        self.assertEqual(behavior.check_interval, 2.0)
        
        # should_trigger 应该正确判断
        result = await behavior.should_trigger(bot)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
