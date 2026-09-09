"""智能寻路系统：使用 A* 算法规划路径，绕过障碍物。

设计说明：
- PathfindingEngine 实现 A* 算法在 XZ 平面寻路
- WorldMap 记录已知方块信息（v1 简化实现，未来可从服务器接收地形数据）
- Y 坐标变化通过启发式代价处理（大幅升降会增加路径代价）
- 寻路失败时回退到直线移动，保证功能总是可用
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass(order=True)
class PathNode:
    """A* 路径节点（支持优先队列排序）"""
    f_cost: float  # f = g + h
    position: tuple[float, float, float] = field(compare=False)
    g_cost: float = field(compare=False)  # 从起点到此节点的实际代价
    h_cost: float = field(compare=False)  # 到终点的启发式估计代价
    parent: PathNode | None = field(default=None, compare=False)


class WorldMap:
    """简化的世界地图：记录已知方块的 Y 坐标（用于障碍检测）
    
    v1 实现：不记录真实地形数据（需要服务器发送区块包），
    仅提供接口供未来扩展。
    """

    def __init__(self):
        self._blocks: dict[tuple[int, int, int], bool] = {}  # (x,y,z) -> is_solid

    def is_walkable(self, x: int, y: int, z: int) -> bool:
        """判断该位置是否可行走（v1 总是返回 True）"""
        # 未来扩展：检查方块类型，判断是否固体、岩浆等
        return True

    def get_height(self, x: int, z: int) -> int | None:
        """获取该 XZ 坐标的地面高度（Y 坐标）
        
        v1 实现：返回 None（需要服务器发送地形数据包）
        未来扩展：解析 Chunk Data 包，记录每个方块的类型和高度
        """
        return None


class PathfindingEngine:
    """A* 寻路引擎：在 XZ 平面规划路径，Y 坐标使用启发式估计"""

    def __init__(self, world_map: WorldMap, max_cost: int = 1000):
        self._world_map = world_map
        self._max_cost = max_cost

    def find_path(
        self,
        start: tuple[float, float, float],
        goal: tuple[float, float, float],
    ) -> list[tuple[float, float, float]] | None:
        """使用 A* 算法寻路，返回路径点列表或 None（无法到达或代价过高）
        
        Args:
            start: 起点坐标 (x, y, z)
            goal: 终点坐标 (x, y, z)
        
        Returns:
            路径点列表（包含起点和终点），或 None（寻路失败）
        """
        sx, sy, sz = start
        gx, gy, gz = goal

        # 简化实现：只在 XZ 平面寻路，Y 坐标使用启发式代价
        open_set: list[PathNode] = []
        closed_set: set[tuple[int, int]] = set()  # (x, z) 网格坐标

        start_node = PathNode(
            f_cost=0,
            position=(sx, sy, sz),
            g_cost=0,
            h_cost=self._heuristic((sx, sz), (gx, gz)),
        )
        start_node.f_cost = start_node.g_cost + start_node.h_cost
        heapq.heappush(open_set, start_node)

        while open_set:
            current = heapq.heappop(open_set)
            cx, cy, cz = current.position

            # 到达目标（XZ 平面距离 < 1 格）
            if math.hypot(cx - gx, cz - gz) < 1.0:
                return self._reconstruct_path(current)

            grid_pos = (int(cx), int(cz))
            if grid_pos in closed_set:
                continue
            closed_set.add(grid_pos)

            # 检查代价上限
            if current.g_cost > self._max_cost:
                return None  # 放弃寻路

            # 扩展邻居（8 方向）
            for dx, dz in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
                nx, nz = cx + dx, cz + dz

                if (int(nx), int(nz)) in closed_set:
                    continue

                # 计算移动代价（对角线 √2，直线 1）
                move_cost = 1.414 if dx != 0 and dz != 0 else 1.0

                # Y 坐标变化惩罚（避免大幅度升降）
                ny = self._world_map.get_height(int(nx), int(nz))
                if ny is None:
                    ny = cy  # 没有地形数据时假设平坦

                y_diff = abs(ny - cy)
                if y_diff > 3:
                    move_cost += 100  # 大幅度升降，高代价
                elif y_diff > 1:
                    move_cost += 10  # 中等升降，中等代价

                g = current.g_cost + move_cost
                h = self._heuristic((nx, nz), (gx, gz))

                neighbor = PathNode(
                    f_cost=g + h,
                    position=(nx, ny, nz),
                    g_cost=g,
                    h_cost=h,
                    parent=current,
                )
                heapq.heappush(open_set, neighbor)

        return None  # 无法到达

    def _heuristic(self, pos: tuple[float, float], goal: tuple[float, float]) -> float:
        """启发式函数：欧几里得距离"""
        return math.hypot(pos[0] - goal[0], pos[1] - goal[1])

    def _reconstruct_path(self, node: PathNode) -> list[tuple[float, float, float]]:
        """回溯构建路径"""
        path = []
        current = node
        while current:
            path.append(current.position)
            current = current.parent
        return list(reversed(path))
