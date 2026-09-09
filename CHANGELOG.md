# Changelog

## 0.2.0 (2026-09-09)

四大优化方向全面提升机器人流畅性与智能性。

### 新增功能

#### 1. 异步动作队列系统
- 新增 `action_queue.py` 模块实现优先级任务队列
- 新增 5 个异步 LLM 工具：
  - `mc_submit_move(x, z)` - 提交移动任务，立即返回 action_id
  - `mc_submit_mine(x, y, z)` - 提交挖掘任务
  - `mc_submit_follow(player)` - 提交跟随任务
  - `mc_action_status(action_id)` - 查询任务状态和进度
  - `mc_cancel_action(action_id)` - 取消排队任务
- 动作在后台排队执行，LLM 调用立即返回（<100ms），不再阻塞对话
- 支持优先级队列：用户指令优先级 0，自主行为 50-100
- 配置项：`enable_action_queue`（默认开启）

#### 2. 复合动作
- 新增 4 个高级动作方法（`bot_client.py`）：
  - `move_and_mine(x, y, z)` - 移动到方块 4 格内并挖掘
  - `collect_nearby_blocks(center_x, center_y, center_z, radius)` - 螺旋扫描收集半径内方块
  - `patrol_area(x1, z1, x2, z2, loops)` - 在矩形区域巡逻
  - `return_to_spawn()` - 返回出生点
- 新增 4 个对应 LLM 工具：`mc_move_and_mine` / `mc_collect_nearby` / `mc_patrol` / `mc_return_spawn`
- 自动记录出生点（处理 CB_SPAWN_POSITION 包 0x50）

#### 3. 自主行为系统
- 新增 `autonomous_behaviors.py` 模块实现行为驱动架构
- 内置 2 种自主行为：
  - `EatWhenHungryBehavior` - 饥饿值低于阈值时自动进食（当前版本记录日志）
  - `FleeFromMobsBehavior` - 检测实体靠近时自动逃离
- 行为管理器 `AutonomousBehaviorManager` 后台循环检查触发条件
- 自主行为通过动作队列插队执行，不中断正在执行的用户指令
- 配置项：
  - `enable_autonomous_behaviors`（默认开启）
  - `auto_eat_threshold`（饥饿值阈值，默认 10）
  - `auto_flee_enabled`（自动逃离开关，默认关闭）

#### 4. 智能寻路系统
- 新增 `pathfinding.py` 模块实现 A* 寻路算法
- XZ 平面 8 方向扩展，Y 坐标变化惩罚（高度差 >3 格代价 +100，>1 格代价 +10）
- 自动回退：寻路失败或代价超过阈值时改用直线移动
- 触发条件：目标距离 >10 格时启用寻路
- 重构 `move_to` 方法：提取直线移动逻辑到 `_move_direct`，寻路成功沿路径点依次移动
- 配置项：
  - `enable_pathfinding`（默认开启）
  - `pathfinding_max_cost`（最大代价阈值，默认 1000）

### 改进
- LLM 工具总数从 7 个扩展到 16 个
- 所有新特性可通过配置独立启用/禁用，向后兼容
- 原有阻塞工具（`mc_move` / `mc_mine` / `mc_follow`）保持不变

### 协议排坑记录
- **CB_SPAWN_POSITION 位置解包**：单个 i64 打包 x(26bit)<<38 | z(26bit)<<12 | y(12bit)，
  需要正确解包并进行符号扩展（x/z 26位有符号，y 12位有符号）

---

## 0.1.0 (2026-09-09)

首个可用版本。

### 功能
- 机器人以离线账号进服游玩 Minecraft 1.20.1（protocol 763）
  - mcproto 协议库实现登录/压缩/心跳/聊天；play 状态包按 1.20.1 协议自建
  - 双向聊天：收玩家聊天（player_chat 含未签名内容）、收系统聊天（system_chat）、
    收控制台公告（profileless_chat）、发未签名聊天；常用 translate 键自动翻译成中文
  - 动作原语：直线移动（20Hz 小步幅 + 传送确认）、挖掘（自动面向 + 挥臂 + 开始/完成挖掘）、
    跟随玩家（实体跟踪）、转向
  - 状态：坐标/朝向/生命/饥饿/在线玩家列表缓存
  - 断线自动重连（指数退避，3s~60s 封顶）
- 服务器双模式
  - local：一键自建 Paper 1.20.1（fill.papermc.io v3 下载，失败回退 Mojang 官方源），
    自动写 eula/server.properties（离线模式 + allow-flight），Java 起服，状态查询式就绪探测，崩溃自动重启（最多 3 次），优雅停服
  - remote：填写 host:port 直连
- 聊天桥：`/mc 订阅` / `退订` 持久化订阅会话，MC 聊天实时推送到 QQ
- 指令集 10 条（状态/起停服/连接/订阅/说话/移动/挖掘/跟随/看向/玩家）
- LLM 工具 7 个（mc_status/mc_move/mc_mine/mc_follow/mc_look/mc_chat/mc_players），Agent 可自主驱动机器人
- 游戏内 @机器人 → LLM 自动回复（可配置）
- 无头单测 31 项 + 端到端实测脚本（真实起服验证全流程）

### 协议排坑记录（实测发现）
- PaperMC 下载 API 从已废弃的 api.papermc.io v2 迁移到 fill.papermc.io v3
- 服务器就绪探测由裸 TCP 改为状态查询，避免「端口已开但登录态未就绪」的启动竞态
- **1.20.1 没有 LoginAcknowledged**（该包 1.20.2 才引入）；且 Client Information(0x08) 必须等
  收到第一个 play 包（0x28 login）后再发，否则会在登录态被拒（Bad packet id 8）
- 聊天包的 offset 用 0（不是 -1），否则服务器报 "Failed to validate message acknowledgements" 踢人
- 聊天 salt 必须是有符号 i64 范围（用 random.getrandbits(63)，int.from_bytes 无符号会越界）
- 移动必须 20Hz、每包步进 ≤0.22 格（服务器对每个移动包校验位移，超 0.25 格报
  "moved wrongly" 并橡皮筋弹回）
- 控制台 /say 等无档案消息走 profileless_chat(0x1b) 而非 system_chat(0x64)，需要单独解析
- **pack_position 必须转为有符号 i64**：Minecraft 位置打包 `(x&0x3FFFFFF)<<38 | (z&0x3FFFFFF)<<12 | (y&0xFFF)`
  产生无符号整数，超过 2^63-1 时需减 2^64 转为有符号，否则 `struct.pack('q')` 抛异常
- **allow-flight=true**：服务器默认禁飞，机器人悬空移动会被反作弊踢出（"Flying is not enabled"）

