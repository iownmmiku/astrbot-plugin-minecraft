# Minecraft 插件全面优化 (2026-09-10)

## 概述
本次优化重构了插件的核心架构，将"只移动不做事"的演示级实现升级为基于真实动作验证的可靠游戏客户端。

---

## 阶段一：稳定连接与生命周期 ✓

### 问题
- 死亡后无法自动重生，需手动重连
- 服务器管理和自动启服混在一起
- 连接不稳定，协议错位后连续重连

### 解决方案
1. **完整生命周期状态**：`connecting/playing/dead/respawning/disconnected`
2. **死亡检测与自动重生**：
   - 检测 `ClientboundRespawn` 包和生命值归零
   - 暂停自主行为、取消动作队列
   - 发送 `ClientCommand.PERFORM_RESPAWN` 请求
   - 收到 Respawn/Position 后重置状态
3. **服务器管理分离**：
   - "插件是否管理服务器" 与 "是否自动启服" 解耦
   - 关闭启服时不占用 25565 端口

### 提交
- `33bf4dd` 自动检测死亡并请求重生
- `3588d87` 优化配置与启服开关

---

## 阶段二：建立真实世界状态模型 ✓

### 问题
- 库存模型不完整，无法区分物品种类和数量
- 没有方块状态缓存，无法"看见"周围环境
- 没有实体追踪，无法找到生物、玩家、掉落物

### 解决方案
1. **扩展物品映射**：`ITEM_NAMES` 从 17 项扩展到 100+ 项
   - 涵盖方块、工具、武器、护甲、材料、食物
2. **方块状态缓存**：
   ```python
   self.blocks: dict[tuple[int, int, int], int] = {}
   self.block_cache_radius = 32
   ```
   - 监听 `ClientboundBlockChange` 和 `ClientboundMultiBlockChange`
   - 提供 `get_block()`, `is_air()` 查询接口
3. **实体分类追踪**：
   ```python
   self.entities[entity_id] = {
       "type": entity_category,  # player/item/xp_orb/hostile/passive/other
       "type_id": entity_type_id,
       "uuid": str(uuid),
       "x": x, "y": y, "z": z,
   }
   ```
   - 提供 `find_nearest_entity()`, `count_nearby_entities()`
4. **库存聚合**：按物品名称聚合数量，支持组件化物品

### 提交
- `045febe` 扩展物品映射、方块状态缓存和实体分类追踪
- `0ad3ce1` 增加按名称聚合库存状态

---

## 阶段三：可验证基础动作闭环 ✓

### 问题
- 动作"发送即成功"，不等待服务器确认
- `move_to` 固定延时后直接返回，不管是否到达
- `mine` 固定延时后返回成功，不验证方块是否消失
- 动作失败无诊断信息

### 解决方案
1. **移动验证**：
   - 分段移动，每段等待服务器位置确认
   - 卡住检测（3秒不动判定失败）
   - 返回明确错误："移动卡住"、"超时未到达"
2. **挖掘验证**：
   ```python
   async def mine(self, x: int, y: int, z: int, *, timeout: float = 30.0) -> str | None:
       block_id = self.get_block(x, y, z)
       if block_id == 0 or block_id is None:
           return "目标位置是空气或未加载"
       # ... 发送挖掘包 ...
       while time.monotonic() < deadline:
           await asyncio.sleep(0.1)
           current_block = self.get_block(x, y, z)
           if current_block == 0 or current_block is None:
               return None  # 成功
       return "挖掘超时（服务器未确认方块变化）"
   ```
3. **新方法**：
   - `collect_drops()`：拾取附近掉落物
   - `attack_entity()`：攻击指定实体

### 提交
- `c194ac9` 实现可验证的基础动作闭环

---

## 阶段四：制作/熔炼/战斗框架 ✓

### 问题
- 没有窗口交互能力，无法合成、熔炼
- 没有战斗逻辑

### 解决方案
1. **窗口交互框架**：
   ```python
   self.open_window_id: int | None = None
   self.window_state_id = 0
   self.window_items: dict[int, dict[str, Any]] = {}
   
   @define
   class SBTClickWindow(PlayServerBoundPacket):
       window_id: int
       state_id: int
       slot: int
       button: int
       mode: int
       slots: list[tuple[int, dict[str, Any]]]
       carried_item: dict[str, Any] | None
   ```
   - 监听 `ClientboundOpenWindow`, `ClientboundSetContainerContent`, `ClientboundSetSlot`
   - 提供 `craft_item()` 框架（待完整实现）
2. **战斗基础**：`attack_entity()` 支持按实体 ID 攻击

### 提交
- `08e487c` 窗口交互和合成系统框架

---

## 阶段五：自主目标系统重写 ✓

### 问题
- 所有 `execute_step()` 只返回"正在执行..."，不做实际工作
- 目标"完成"是假的，没有验证库存、方块、实体变化
- 失败无法诊断，没有重试机制

### 解决方案
1. **创建 `goal_system_v2.py`**：
   - 每个目标必须有可观测完成条件（库存/方块/实体状态）
   - 每个步骤调用真实动作并等待服务器确认
   - 失败带明确原因，支持步骤重试（`max_retries=3`）
   - 支持断点续玩：`current_step`, `retry_count`, `last_error`
2. **实现第一个真实目标**：`CollectWoodGoal`
   ```python
   async def execute_step(self, bot: MCBot) -> tuple[bool, str]:
       # 1. 在方块缓存中搜索最近原木
       # 2. 移动到原木附近（距离 <= 4.5）
       # 3. 挖掘原木并等待服务器确认
       # 4. 拾取掉落物
       # 5. 验证库存变化
   ```
   - 前置条件：检查连接、位置、方块缓存能力
   - 完成条件：库存中有 >= target_count 原木
3. **新 GoalManagerV2**：
   - 目标执行主循环（每5秒检查）
   - 自动检查前置条件、完成条件
   - 步骤失败自动重试，超过 max_retries 则目标失败

### 提交
- `77972cf` 重写目标系统为真实动作验证版本

---

## 阶段六：人格、模型和操作体验统一 ✓

### 问题
- 人格配置混乱：插件人格、AstrBot 人格、指令人格三套系统
- 模型配置不清晰
- 命令接口不统一

### 解决方案
1. **统一人格优先级**：
   ```python
   def _get_system_prompt(self) -> str:
       source = self._cfg("persona_source", "astrbot_current")
       # 1. AstrBot 全局当前人格
       # 2. AstrBot 指定人格（astrbot_persona_id）
       # 3. 插件自定义人格（persona_custom_desc）
       # 4. 插件默认人格（降级）
   ```
2. **统一模型配置**：
   ```python
   def _llm_model_override(self) -> str | None:
       if self._cfg("model_source", "default") == "custom":
           return self._cfg("llm_model", "")
       return None  # 跟随 AstrBot
   ```
3. **重写命令**：
   - `/mc人格 [astrbot|select <ID>|custom <描述>]`
   - `/mc模型 [default|custom <模型名>]`
   - `/mc状态` 显示人格来源和模型来源
4. **所有 LLM 调用统一**：通过 `_llm_chat()` 自动应用人格和模型

### 提交
- `34b7b41` 统一人格和模型配置

---

## 核心设计原则

1. **真实动作验证**：不再有"发送=完成"，所有动作等待服务器确认
2. **可观测完成条件**：目标完成基于库存、方块、实体状态，不是内部标志
3. **失败可诊断**：每个失败返回明确原因，不是静默假装成功
4. **没有能力不宣称**：无方块缓存时拒绝采集目标，而非假装执行
5. **断点续玩**：步骤、重试、错误持久化，死亡/断线后可恢复

---

## 测试建议

1. **基础连接**：
   - 进服、受伤、死亡、自动重生、断线重连
2. **动作验证**：
   - 移动到远处坐标，验证是否真正到达
   - 挖掘方块，验证库存是否增加
   - 移动到障碍物，验证是否报告"卡住"
3. **目标系统**：
   - `/mc目标 启动`，观察机器人寻找、移动、挖掘原木
   - 检查 `/mc状态` 显示库存原木数量增加
   - 故意清空周围原木，验证是否移动到新区域
4. **人格模型**：
   - 切换 `/mc人格 astrbot`，验证游戏内回复使用 AstrBot 人格
   - 切换 `/mc模型 custom <模型>`，验证调用指定模型

---

## 下一步扩展

1. **更多真实目标**：
   - `CraftWoodenToolsGoal`（合成木镐、工作台）
   - `MineStoneGoal`（挖掘圆石）
   - `HuntForFoodGoal`（狩猎动物、采集食物）
2. **完整合成系统**：实现 `craft_item()` 的配方摆放和点击逻辑
3. **路径规划**：当前移动是直线，遇障碍物会卡住，需要绕路算法
4. **多目标决策**：LLM 根据当前进度、库存、环境选择下一个目标

---

## 兼容性说明

- 旧 `goal_system.py` 保留但不再使用，可安全删除
- 配置字段变化：
  - 废弃：`use_astrbot_persona`, `use_astrbot_config`, `persona`（旧）
  - 新增：`persona_source`, `astrbot_persona_id`, `persona_custom_desc`
  - 新增：`model_source`, `llm_model`（复用）
- 所有旧命令仍可用，新命令向后兼容
