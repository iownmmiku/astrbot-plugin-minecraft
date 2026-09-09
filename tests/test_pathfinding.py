"""测试智能寻路系统"""
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pathfinding import PathNode, WorldMap, PathfindingEngine


class TestPathNode(unittest.TestCase):
    def test_path_node_ordering(self):
        """测试 PathNode 按 f_cost 排序"""
        node1 = PathNode(f_cost=10.0, position=(0, 0, 0), g_cost=5.0, h_cost=5.0)
        node2 = PathNode(f_cost=8.0, position=(1, 0, 0), g_cost=4.0, h_cost=4.0)
        node3 = PathNode(f_cost=12.0, position=(2, 0, 0), g_cost=6.0, h_cost=6.0)
        
        nodes = sorted([node1, node2, node3])
        self.assertEqual(nodes[0].f_cost, 8.0)
        self.assertEqual(nodes[1].f_cost, 10.0)
        self.assertEqual(nodes[2].f_cost, 12.0)


class TestWorldMap(unittest.TestCase):
    def test_world_map_returns_none(self):
        """测试 WorldMap v1 版本始终返回 None（未实现地形数据）"""
        world_map = WorldMap()
        self.assertIsNone(world_map.get_height(0, 0))
        self.assertIsNone(world_map.get_height(100, 200))


class TestPathfindingEngine(unittest.TestCase):
    def setUp(self):
        self.world_map = WorldMap()
        self.engine = PathfindingEngine(self.world_map, max_cost=1000)

    def test_find_path_straight_line(self):
        """测试直线路径"""
        start = (0.0, 64.0, 0.0)
        goal = (10.0, 64.0, 0.0)
        
        path = self.engine.find_path(start, goal)
        
        self.assertIsNotNone(path)
        self.assertGreater(len(path), 1)
        self.assertEqual(path[0], start)
        # 最后一个点应该接近目标
        self.assertAlmostEqual(path[-1][0], goal[0], delta=1.0)
        self.assertAlmostEqual(path[-1][2], goal[2], delta=1.0)

    def test_find_path_diagonal(self):
        """测试对角线路径"""
        start = (0.0, 64.0, 0.0)
        goal = (10.0, 64.0, 10.0)
        
        path = self.engine.find_path(start, goal)
        
        self.assertIsNotNone(path)
        self.assertEqual(path[0], start)
        # 对角线路径应该利用 8 方向扩展
        self.assertLess(len(path), 21)  # 直线需要 20 步，对角线应该更短

    def test_find_path_same_position(self):
        """测试起点和终点相同时立即返回"""
        start = (10.0, 64.0, 20.0)
        goal = (10.0, 64.0, 20.0)
        
        path = self.engine.find_path(start, goal)
        
        self.assertIsNotNone(path)
        self.assertEqual(len(path), 1)
        self.assertEqual(path[0], start)

    def test_find_path_y_penalty(self):
        """测试 Y 坐标变化惩罚"""
        start = (0.0, 64.0, 0.0)
        goal_low = (10.0, 60.0, 0.0)  # Y 下降 4 格
        goal_high = (10.0, 68.0, 0.0)  # Y 上升 4 格
        
        # 两种情况都应该找到路径，但代价较高
        path_low = self.engine.find_path(start, goal_low)
        path_high = self.engine.find_path(start, goal_high)
        
        self.assertIsNotNone(path_low)
        self.assertIsNotNone(path_high)

    def test_find_path_max_cost_limit(self):
        """测试超过最大代价时返回 None"""
        engine_low_cost = PathfindingEngine(self.world_map, max_cost=5)
        
        start = (0.0, 64.0, 0.0)
        goal = (1000.0, 64.0, 0.0)  # 非常远的目标
        
        path = engine_low_cost.find_path(start, goal)
        
        # 代价超过 5 应该放弃寻路
        self.assertIsNone(path)

    def test_find_path_long_distance(self):
        """测试长距离寻路"""
        start = (0.0, 64.0, 0.0)
        goal = (50.0, 64.0, 50.0)
        
        path = self.engine.find_path(start, goal)
        
        self.assertIsNotNone(path)
        self.assertEqual(path[0], start)
        # 验证路径点逐步接近目标
        for i in range(1, len(path)):
            prev_x, prev_y, prev_z = path[i-1]
            curr_x, curr_y, curr_z = path[i]
            # 每步移动应该是 1 格（直线）或 sqrt(2) 格（对角线）
            import math
            step_dist = math.hypot(curr_x - prev_x, curr_z - prev_z)
            self.assertLessEqual(step_dist, 1.5)

    def test_heuristic_calculation(self):
        """测试启发式函数计算"""
        pos = (0.0, 0.0)  # XZ 平面坐标
        goal = (3.0, 4.0)
        
        h_cost = self.engine._heuristic(pos, goal)
        
        # 欧几里得距离：sqrt(3^2 + 4^2) = 5.0
        self.assertAlmostEqual(h_cost, 5.0, places=5)

    def test_reconstruct_path(self):
        """测试路径回溯"""
        # 手动构建一个简单的父子关系链
        node1 = PathNode(0.0, (0, 0, 0), 0.0, 0.0, parent=None)
        node2 = PathNode(0.0, (1, 0, 0), 0.0, 0.0, parent=node1)
        node3 = PathNode(0.0, (2, 0, 0), 0.0, 0.0, parent=node2)
        
        path = self.engine._reconstruct_path(node3)
        
        self.assertEqual(len(path), 3)
        self.assertEqual(path[0], (0, 0, 0))
        self.assertEqual(path[1], (1, 0, 0))
        self.assertEqual(path[2], (2, 0, 0))

    def test_eight_direction_expansion(self):
        """测试 8 方向扩展（包括对角线）"""
        start = (0.0, 64.0, 0.0)
        # 测试四个对角线方向
        goals = [
            (5.0, 64.0, 5.0),   # 东南
            (-5.0, 64.0, 5.0),  # 西南
            (5.0, 64.0, -5.0),  # 东北
            (-5.0, 64.0, -5.0), # 西北
        ]
        
        for goal in goals:
            path = self.engine.find_path(start, goal)
            self.assertIsNotNone(path, f"Failed to find path to {goal}")
            # 对角线路径应该比直线路径短
            import math
            expected_steps = math.hypot(goal[0] - start[0], goal[2] - start[2])
            # A* 应该找到接近最优的路径
            self.assertLessEqual(len(path), expected_steps + 5)


class TestPathfindingIntegration(unittest.TestCase):
    """集成测试：完整寻路场景"""
    
    def test_pathfinding_vs_straight_line(self):
        """对比寻路路径与直线距离"""
        world_map = WorldMap()
        engine = PathfindingEngine(world_map, max_cost=1000)
        
        start = (0.0, 64.0, 0.0)
        goal = (30.0, 64.0, 40.0)
        
        path = engine.find_path(start, goal)
        
        self.assertIsNotNone(path)
        
        # 计算路径总长度
        import math
        total_dist = 0.0
        for i in range(1, len(path)):
            px, py, pz = path[i-1]
            cx, cy, cz = path[i]
            total_dist += math.hypot(cx - px, cz - pz)
        
        # 直线距离
        straight_dist = math.hypot(goal[0] - start[0], goal[2] - start[2])
        
        # 路径长度应该接近直线距离（在平坦地形上）
        self.assertLess(total_dist, straight_dist * 1.5)


if __name__ == "__main__":
    unittest.main()
