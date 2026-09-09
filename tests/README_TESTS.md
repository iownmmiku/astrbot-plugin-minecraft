# 测试说明

## 测试覆盖

本项目包含 4 个新增测试套件，覆盖四大优化特性：

### 1. test_action_queue.py（9 个测试）

测试异步动作队列系统：

- ✅ `test_submit_action` - 提交动作到队列
- ✅ `test_priority_ordering` - 优先级队列排序（高优先级先执行）
- ✅ `test_action_status_lifecycle` - 动作状态生命周期（PENDING → RUNNING → COMPLETED）
- ✅ `test_action_failure` - 动作失败状态
- ✅ `test_cancel_pending_action` - 取消排队中的动作
- ✅ `test_cancel_nonexistent_action` - 取消不存在的动作
- ✅ `test_get_status_nonexistent` - 查询不存在的动作状态
- ✅ `test_compound_action_move_and_mine` - 复合动作 move_and_mine
- ✅ `test_worker_handles_unknown_action_type` - worker 处理未知动作类型

### 2. test_compound_actions.py（10 个测试）

测试复合动作逻辑：

- ✅ `test_move_and_mine_within_reach` - 目标在可达范围内，直接挖掘
- ✅ `test_move_and_mine_need_approach` - 需要先移动接近
- ✅ `test_move_and_mine_move_fails` - 移动失败时返回错误
- ✅ `test_collect_nearby_blocks` - 螺旋扫描收集方块
- ✅ `test_collect_nearby_timeout` - 超时时返回部分完成
- ✅ `test_patrol_area` - 在矩形区域巡逻
- ✅ `test_patrol_area_move_fails` - 移动失败时停止巡逻
- ✅ `test_return_to_spawn_success` - 成功返回出生点
- ✅ `test_return_to_spawn_no_spawn_recorded` - 未记录出生点时返回错误
- ✅ `test_return_to_spawn_not_connected` - 未连接时返回错误

### 3. test_autonomous_behaviors.py（13 个测试）

测试自主行为系统：

**EatWhenHungryBehavior (3 个测试)**：
- ✅ `test_should_trigger_when_hungry` - 饥饿时触发条件
- ✅ `test_should_not_trigger_when_not_hungry` - 不饥饿时不触发
- ✅ `test_execute_logs_warning` - 执行时记录日志

**FleeFromMobsBehavior (5 个测试)**：
- ✅ `test_should_trigger_when_entity_nearby` - 实体靠近时触发
- ✅ `test_should_not_trigger_when_no_entities` - 无实体时不触发
- ✅ `test_should_not_trigger_when_entities_far` - 实体距离较远时不触发
- ✅ `test_execute_submits_flee_action` - 执行时提交高优先级逃离动作
- ✅ `test_execute_handles_no_action_queue` - 未启用动作队列时使用阻塞执行

**AutonomousBehaviorManager (5 个测试)**：
- ✅ `test_manager_registers_behaviors` - 管理器注册行为
- ✅ `test_manager_respects_flee_disabled` - 关闭自动逃离时不注册 FleeFromMobsBehavior
- ✅ `test_manager_run_checks_behaviors` - 管理器循环检查行为触发
- ✅ `test_manager_stop` - 停止管理器
- ✅ `test_behavior_check_interval` - 行为的 check_interval 属性

### 4. test_pathfinding.py（12 个测试）

测试智能寻路系统：

**PathNode (1 个测试)**：
- ✅ `test_path_node_ordering` - PathNode 按 f_cost 排序

**WorldMap (1 个测试)**：
- ✅ `test_world_map_returns_none` - WorldMap v1 版本始终返回 None

**PathfindingEngine (9 个测试)**：
- ✅ `test_find_path_straight_line` - 直线路径
- ✅ `test_find_path_diagonal` - 对角线路径
- ✅ `test_find_path_same_position` - 起点和终点相同时立即返回
- ✅ `test_find_path_y_penalty` - Y 坐标变化惩罚
- ✅ `test_find_path_max_cost_limit` - 超过最大代价时返回 None
- ✅ `test_find_path_long_distance` - 长距离寻路
- ✅ `test_heuristic_calculation` - 启发式函数计算
- ✅ `test_reconstruct_path` - 路径回溯
- ✅ `test_eight_direction_expansion` - 8 方向扩展（包括对角线）

**集成测试 (1 个测试)**：
- ✅ `test_pathfinding_vs_straight_line` - 对比寻路路径与直线距离

## 运行测试

### 运行单个测试套件

```bash
# 动作队列测试
py tests/test_action_queue.py

# 复合动作测试
py tests/test_compound_actions.py

# 自主行为测试
py tests/test_autonomous_behaviors.py

# 寻路测试
py tests/test_pathfinding.py
```

### 运行所有新增测试

```bash
py tests/test_action_queue.py && \
py tests/test_compound_actions.py && \
py tests/test_autonomous_behaviors.py && \
py tests/test_pathfinding.py
```

### 运行原有测试

```bash
py tests/run_tests.py
```

## 测试统计

- **总测试数**: 44 个
- **通过率**: 100%
- **覆盖模块**:
  - `action_queue.py`
  - `bot_client.py`（复合动作部分）
  - `autonomous_behaviors.py`
  - `pathfinding.py`

## 测试设计原则

1. **隔离性**: 使用 MockBot 避免依赖真实的 Minecraft 连接
2. **覆盖性**: 覆盖正常流程、边界情况、错误处理
3. **可读性**: 每个测试有清晰的文档字符串说明意图
4. **快速性**: 所有测试在 6 秒内完成（无需真实服务器）

## 已知限制

- 测试使用 mock 对象，不验证真实 Minecraft 协议交互
- 寻路测试在简化地图上运行（WorldMap v1 返回 None）
- 自主行为的实际触发频率需要端到端测试验证

## 后续扩展

可以添加的测试：
- 端到端测试：真实起服 + 进服 + 执行复合动作
- 性能测试：寻路算法在大地图上的性能
- 压力测试：动作队列处理大量并发任务
- 集成测试：多个自主行为同时触发的优先级处理
