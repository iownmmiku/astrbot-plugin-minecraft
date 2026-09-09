"""测试异步动作队列系统"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from action_queue import ActionQueue, ActionStatus, ActionTask


class MockBot:
    """模拟 MCBot 用于测试"""
    def __init__(self):
        self.move_to = AsyncMock(return_value=None)
        self.mine = AsyncMock(return_value=None)
        self.follow = AsyncMock(return_value=None)


class TestActionQueue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = MockBot()
        self.queue = ActionQueue(self.bot)
        # 不自动启动 worker，手动控制
        self.queue._running = True

    async def asyncTearDown(self):
        self.queue._running = False
        if self.queue._worker_task and not self.queue._worker_task.done():
            self.queue._worker_task.cancel()
            try:
                await self.queue._worker_task
            except asyncio.CancelledError:
                pass

    async def test_submit_action(self):
        """测试提交动作到队列"""
        action_id = self.queue.submit("move", {"x": 10.0, "z": 20.0}, priority=0)
        
        self.assertIsNotNone(action_id)
        self.assertEqual(len(action_id), 36)  # UUID 格式
        
        task = self.queue.get_status(action_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.action_type, "move")
        self.assertEqual(task.status, ActionStatus.PENDING)
        self.assertEqual(task.params, {"x": 10.0, "z": 20.0})
        self.assertEqual(task.priority, 0)

    async def test_priority_ordering(self):
        """测试优先级队列排序（高优先级先执行）"""
        id1 = self.queue.submit("move", {"x": 1.0, "z": 1.0}, priority=0)
        id2 = self.queue.submit("move", {"x": 2.0, "z": 2.0}, priority=100)
        id3 = self.queue.submit("move", {"x": 3.0, "z": 3.0}, priority=50)
        
        # 启动 worker 并等待处理
        worker_task = asyncio.create_task(self.queue._worker())
        await asyncio.sleep(0.1)  # 让 worker 有时间处理
        
        # 高优先级应该先执行
        self.assertEqual(self.bot.move_to.call_count, 3)
        calls = self.bot.move_to.call_args_list
        # 第一个调用应该是 priority=100 的任务
        self.assertEqual(calls[0][1]["x"], 2.0)
        # 第二个调用应该是 priority=50 的任务
        self.assertEqual(calls[1][1]["x"], 3.0)
        # 第三个调用应该是 priority=0 的任务
        self.assertEqual(calls[2][1]["x"], 1.0)
        
        worker_task.cancel()

    async def test_action_status_lifecycle(self):
        """测试动作状态生命周期：PENDING → RUNNING → COMPLETED"""
        action_id = self.queue.submit("move", {"x": 100.0, "z": 200.0})
        
        # 初始状态
        task = self.queue.get_status(action_id)
        self.assertEqual(task.status, ActionStatus.PENDING)
        
        # 启动 worker
        worker_task = asyncio.create_task(self.queue._worker())
        await asyncio.sleep(0.05)
        
        # 应该已完成
        task = self.queue.get_status(action_id)
        self.assertEqual(task.status, ActionStatus.COMPLETED)
        self.assertIsNone(task.result)  # move_to 成功返回 None
        
        worker_task.cancel()

    async def test_action_failure(self):
        """测试动作失败状态"""
        self.bot.mine.return_value = "超出可挖掘距离"
        
        action_id = self.queue.submit("mine", {"x": 10, "y": 64, "z": 20})
        
        worker_task = asyncio.create_task(self.queue._worker())
        await asyncio.sleep(0.05)
        
        task = self.queue.get_status(action_id)
        self.assertEqual(task.status, ActionStatus.FAILED)
        self.assertEqual(task.result, "超出可挖掘距离")
        
        worker_task.cancel()

    async def test_cancel_pending_action(self):
        """测试取消排队中的动作"""
        # 提交一个会阻塞的动作
        async def slow_move(**kw):
            await asyncio.sleep(10)
            return None
        self.bot.move_to = AsyncMock(side_effect=slow_move)
        
        id1 = self.queue.submit("move", {"x": 1.0, "z": 1.0})
        id2 = self.queue.submit("move", {"x": 2.0, "z": 2.0})
        
        # 启动 worker（id1 会开始执行并阻塞）
        worker_task = asyncio.create_task(self.queue._worker())
        await asyncio.sleep(0.05)
        
        # 此时 id1 正在执行，id2 还在排队
        task1 = self.queue.get_status(id1)
        task2 = self.queue.get_status(id2)
        self.assertEqual(task1.status, ActionStatus.RUNNING)
        self.assertEqual(task2.status, ActionStatus.PENDING)
        
        # 取消 id2（应该成功）
        self.assertTrue(self.queue.cancel(id2))
        task2 = self.queue.get_status(id2)
        self.assertEqual(task2.status, ActionStatus.CANCELLED)
        
        # 取消 id1（应该失败，因为已在执行）
        self.assertFalse(self.queue.cancel(id1))
        
        worker_task.cancel()

    async def test_cancel_nonexistent_action(self):
        """测试取消不存在的动作"""
        self.assertFalse(self.queue.cancel("nonexistent-id"))

    async def test_get_status_nonexistent(self):
        """测试查询不存在的动作状态"""
        self.assertIsNone(self.queue.get_status("nonexistent-id"))

    async def test_compound_action_move_and_mine(self):
        """测试复合动作 move_and_mine"""
        self.bot.move_and_mine = AsyncMock(return_value=None)
        
        action_id = self.queue.submit("move_and_mine", {"x": 10, "y": 64, "z": 20})
        
        worker_task = asyncio.create_task(self.queue._worker())
        await asyncio.sleep(0.05)
        
        task = self.queue.get_status(action_id)
        self.assertEqual(task.status, ActionStatus.COMPLETED)
        self.bot.move_and_mine.assert_called_once_with(x=10, y=64, z=20)
        
        worker_task.cancel()

    async def test_worker_handles_unknown_action_type(self):
        """测试 worker 处理未知动作类型"""
        action_id = self.queue.submit("unknown_action", {"param": "value"})
        
        worker_task = asyncio.create_task(self.queue._worker())
        await asyncio.sleep(0.05)
        
        task = self.queue.get_status(action_id)
        self.assertEqual(task.status, ActionStatus.FAILED)
        self.assertIn("未知动作类型", task.result)
        
        worker_task.cancel()


if __name__ == "__main__":
    unittest.main()
