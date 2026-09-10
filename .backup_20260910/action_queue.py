"""异步动作队列：让 LLM 工具立即返回，动作在后台排队执行。

设计说明：
- ActionQueue 维护一个优先级队列，后台 worker 逐个执行动作
- 每个动作有唯一 action_id（UUID），可查询状态、取消排队中的动作
- 优先级：用户指令默认 0，自主行为 50-100（数字越大越优先）
- 状态流转：PENDING -> RUNNING -> COMPLETED/FAILED/CANCELLED
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bot_client import MCBot


class ActionStatus(Enum):
    """动作状态"""
    PENDING = "pending"      # 排队中
    RUNNING = "running"      # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 已取消


@dataclass
class ActionTask:
    """动作任务：记录单个动作的完整信息"""
    action_id: str
    action_type: str  # "move", "mine", "follow", "move_and_mine", "collect_nearby", "patrol", "return_spawn"
    status: ActionStatus
    params: dict[str, Any]
    result: str | None = None  # 成功时为 None，失败时为错误信息
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    priority: int = 0  # 优先级（数字越大越优先）

    def to_dict(self) -> dict[str, Any]:
        """转为字典（供 LLM 工具返回）"""
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "status": self.status.value,
            "params": self.params,
            "result": self.result,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "priority": self.priority,
        }


class ActionQueue:
    """异步动作队列：管理动作提交、执行、状态查询、取消"""

    def __init__(self, bot: MCBot):
        self._bot = bot
        # PriorityQueue 按 (priority, counter, task) 排序，priority 取负数让大的先出队
        self._queue: asyncio.PriorityQueue[tuple[int, int, ActionTask]] = asyncio.PriorityQueue()
        self._tasks: dict[str, ActionTask] = {}  # action_id -> task
        self._current_task: ActionTask | None = None  # 当前正在执行的任务
        self._counter = 0  # 用于在优先级相同时按提交顺序排序
        self._worker_task: asyncio.Task | None = None
        self._running = False

    def submit(self, action_type: str, params: dict[str, Any], priority: int = 0) -> str:
        """提交动作到队列，返回 action_id"""
        action_id = str(uuid.uuid4())
        task = ActionTask(
            action_id=action_id,
            action_type=action_type,
            status=ActionStatus.PENDING,
            params=params,
            priority=priority,
        )
        self._tasks[action_id] = task
        # priority 取负数让大的先出队；counter 保证相同优先级按提交顺序
        self._queue.put_nowait((-priority, self._counter, task))
        self._counter += 1
        return action_id

    def get_status(self, action_id: str) -> ActionTask | None:
        """查询动作状态"""
        return self._tasks.get(action_id)

    def cancel(self, action_id: str) -> bool:
        """取消动作（仅能取消 PENDING 状态的动作，RUNNING 的无法取消）"""
        task = self._tasks.get(action_id)
        if task is None:
            return False
        if task.status == ActionStatus.PENDING:
            task.status = ActionStatus.CANCELLED
            task.completed_at = time.time()
            return True
        return False

    async def cancel_all(self) -> int:
        """取消所有尚未开始的动作，返回取消数量。"""
        count = 0
        for task in self._tasks.values():
            if task.status == ActionStatus.PENDING:
                task.status = ActionStatus.CANCELLED
                task.completed_at = time.time()
                count += 1
        return count

    def get_current_task(self) -> ActionTask | None:
        """获取当前正在执行的任务"""
        return self._current_task

    async def start_worker(self) -> None:
        """启动后台 worker"""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())

    async def stop_worker(self) -> None:
        """停止后台 worker"""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def _worker(self) -> None:
        """后台 worker：从队列取任务并执行"""
        while self._running:
            try:
                # 从优先级队列取任务
                _, _, task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # 检查是否已被取消
            if task.status == ActionStatus.CANCELLED:
                continue

            # 执行任务
            self._current_task = task
            task.status = ActionStatus.RUNNING
            task.started_at = time.time()

            result = await self._execute_action(task)

            task.status = ActionStatus.COMPLETED if result is None else ActionStatus.FAILED
            task.result = result
            task.completed_at = time.time()
            self._current_task = None

    async def _execute_action(self, task: ActionTask) -> str | None:
        """执行单个动作，返回 None（成功）或错误信息（失败）"""
        try:
            action_type = task.action_type
            params = task.params

            # 基础动作
            if action_type == "move":
                return await self._bot.move_to(**params)
            elif action_type == "mine":
                return await self._bot.mine(**params)
            elif action_type == "follow":
                return await self._bot.follow(**params)
            
            # 复合动作（阶段 2 会实现这些方法）
            elif action_type == "move_and_mine":
                if hasattr(self._bot, "move_and_mine"):
                    return await self._bot.move_and_mine(**params)
                return "复合动作 move_and_mine 未实现"
            elif action_type == "collect_nearby":
                if hasattr(self._bot, "collect_nearby_blocks"):
                    return await self._bot.collect_nearby_blocks(**params)
                return "复合动作 collect_nearby_blocks 未实现"
            elif action_type == "patrol":
                if hasattr(self._bot, "patrol_area"):
                    return await self._bot.patrol_area(**params)
                return "复合动作 patrol_area 未实现"
            elif action_type == "return_spawn":
                if hasattr(self._bot, "return_to_spawn"):
                    return await self._bot.return_to_spawn(**params)
                return "复合动作 return_to_spawn 未实现"
            
            return f"未知动作类型：{action_type}"

        except Exception as e:
            return f"执行动作时出错：{e}"
