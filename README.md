# astrbot_plugin_minecraft

让 **AstrBot** 的机器人以玩家身份进入 Minecraft 服务器「游玩」的插件：
进服、双向聊天桥、移动/挖掘/跟随动作，以及 LLM 工具让 AI 自主驱动机器人。

- 技术栈：**纯 Python + mcproto 协议库**（零 Node 依赖），目标协议 **Minecraft 1.20.1 (protocol 763)**
- 聊天平台：与 AstrBot 现有平台（NapCat QQ / Telegram 等）完全解耦，走 AstrBot 标准消息 API
- 服务器：**本地一键自建 Paper 1.20.1**（自动下载/起服/崩溃重启）或 **远程服务器直连**，配置二选一

> ⚠️ 机器人以**离线（非正版）账号**进服，仅支持未开启正版验证（`online-mode=false`）的服务器；
> 且协议客户端只适配**原版 / Paper / Spigot 类服务器**，无法进入 Forge/Fabric 模组服。

---

## 快速开始

### 1. 安装

把插件目录复制到 AstrBot 插件目录：

```
copy /Y astrbot_plugin_minecraft C:\Users\miku\.astrbot\data\plugins\
```

然后在 AstrBot WebUI → 插件管理 → 启用 `Minecraft 机器人游玩`。
插件首次加载会提示安装依赖 `mcproto`（已装到 `data/site-packages` 则直接使用）。

### 2. 配置

WebUI 插件配置页，关键项：

| 配置 | 说明 | 默认 |
|---|---|---|
| `server_mode` | `local`=插件自建本地服；`remote`=连接你填的地址；`launcher_api`=通过启动器 API 远程控制 | `local` |
| `launcher_api_url` | 启动器 API 地址（launcher_api 模式） | `http://127.0.0.1:8765` |
| `bot_username` | 机器人游戏内名字（离线账号） | `AstrBot` |
| `remote_host` / `remote_port` | 远程模式地址 | `127.0.0.1:25565` |
| `local_port` / `local_motd` | 本地服端口 / MOTD | `25565` |
| `java_path` | Java 路径（留空自动检测，需 Java 17+） | 空 |
| `auto_connect` | 插件加载后自动起服 + 进服 | 开 |
| `auto_reply_in_game` | 游戏内 @机器人 时用 LLM 回复 | 开 |
| `max_reconnect_times` | 断线自动重连次数（指数退避） | 5 |
| `enable_action_queue` | 启用异步动作队列（LLM 工具立即返回） | 开 |
| `enable_pathfinding` | 启用智能寻路（A* 绕障碍） | 开 |
| `pathfinding_max_cost` | 寻路最大代价（超过改用直线） | 1000 |
| `enable_autonomous_behaviors` | 启用自主行为（自动避险/进食） | 开 |
| `auto_eat_threshold` | 自动进食饥饿值阈值 | 10 |
| `auto_flee_enabled` | 自动逃离接近的实体 | 关 |

### 3. 三种服务器模式

#### 模式 1：本地自建服务器（local）
- 插件自动下载 Paper 1.20.1 服务器
- 自动启动和管理服务器进程
- 适合：开发调试、单机测试

#### 模式 2：远程服务器（remote）
- 连接到已有的 Minecraft 服务器
- 填写服务器 IP 和端口
- 适合：连接朋友的服务器、公共服务器

#### 模式 3：启动器 API 模式（launcher_api）**NEW!**
- 通过 [AstrBot Minecraft 启动器](https://github.com/iownmmiku/astrbot-minecraft-launcher) 的 HTTP API 远程控制
- 插件和启动器可以在不同电脑上
- 插件可以通过 API 启动/停止服务器、发送命令
- 适合：生产环境、服务器分离部署

**启动器 API 模式配置步骤：**
1. 在服务器电脑上运行 AstrBot Minecraft 启动器
2. 启动器中进入「远程访问」页面，启动 API 服务
3. 记下显示的 URL（如 `http://192.168.1.100:8765`）
4. 在插件配置中：
   - `server_mode` 选择 `launcher_api`
   - `launcher_api_url` 填写启动器的 API URL
5. 插件将通过 API 自动管理服务器

### 4. 进服

本地模式开箱即用：启用插件后自动下载 Paper 1.20.1（首次约 43MB）、起服、进服。
也可手动控制：

```
/mc 起服    下载并启动本地服务器（首次较慢）
/mc 连接    机器人进服
/mc 状态    查看机器人位置/生命/在线玩家/服务器状态
```

### 4. 双向聊天

```
/mc 订阅    把当前 QQ 会话加入桥接（游戏内聊天会推送到这里）
/mc 退订    取消
/mc 说话 大家好   让机器人在游戏内说一句话
```

### 5. 让机器人「玩」

指令控制：

```
/mc 移动 <x> <z>      走到指定坐标（可带小数）
/mc 挖掘 <x> <y> <z>  挖掘方块（4 格内，自动面向）
/mc 跟随 <玩家名>     跟在玩家身边 2 格内
/mc 看向 <yaw> <pitch>
/mc 玩家              在线玩家列表
```

LLM 驱动（对 QQ 机器人说自然语言即可）：

```
「让机器人去挖铁矿」
「让机器人跟着张三」
「机器人现在在哪个坐标？」
```

插件注册了 **16 个 LLM 工具**，AstrBot 的 Agent 会按需自动调用：

**基础工具**（阻塞执行）：
- `mc_status` / `mc_move` / `mc_mine` / `mc_follow` / `mc_look` / `mc_chat` / `mc_players`

**异步动作队列**（立即返回 action_id）：
- `mc_submit_move(x, z)` → 提交移动任务，返回 action_id
- `mc_submit_mine(x, y, z)` → 提交挖掘任务
- `mc_submit_follow(player)` → 提交跟随任务
- `mc_action_status(action_id)` → 查询任务状态和进度
- `mc_cancel_action(action_id)` → 取消排队中的任务

**复合动作**（高级操作）：
- `mc_move_and_mine(x, y, z)` → 移动到方块 4 格内并挖掘
- `mc_collect_nearby(x, y, z, radius)` → 收集中心点周围指定半径内的方块
- `mc_patrol(x1, z1, x2, z2, loops)` → 在矩形区域巡逻指定圈数
- `mc_return_spawn()` → 返回出生点

---

## 优化特性

插件内置四大优化方向，让机器人行动更流畅、更智能：

### 1. 异步动作队列

**解决问题**：原有工具（如 `mc_move`）是阻塞的，LLM 调用后需等待几十秒才能返回，无法同时处理其他对话或动作。

**优化方案**：
- 新增 `mc_submit_*` 系列工具，提交任务后立即返回 `action_id`（<100ms）
- 动作在后台排队执行，LLM 通过 `mc_action_status(action_id)` 查询进度
- 支持优先级队列：用户指令优先级 0，自主行为优先级 50-100
- 可通过 `mc_cancel_action(action_id)` 取消排队中的任务

**配置项**：`enable_action_queue`（默认开启）

**示例对话**：
```
用户：让机器人去挖坐标 (100, 64, 200) 的石头
LLM：已提交挖掘任务 action_a3f8b2，预计需要 30 秒
用户：现在到哪了？
LLM：[调用 mc_action_status] 正在移动中，已走了 15 格，还剩 8 格
```

---

### 2. 复合动作

**解决问题**：完成一个高级任务需要多次工具调用（如「先移动再挖掘」需调用 2 次），增加 LLM 调用开销和延迟。

**优化方案**：
- `mc_move_and_mine(x, y, z)`：自动计算接近点，移动到 4 格内后挖掘
- `mc_collect_nearby(x, y, z, radius)`：螺旋扫描半径内方块，逐个移动并挖掘
- `mc_patrol(x1, z1, x2, z2, loops)`：在矩形区域巡逻指定圈数
- `mc_return_spawn()`：返回出生点（自动记录 spawn position）

**示例对话**：
```
用户：让机器人收集坐标 (0, 64, 0) 周围 5 格内的所有方块
LLM：[调用 mc_collect_nearby(0, 64, 0, 5)] 已开始收集，预计需要 3 分钟
```

---

### 3. 自主行为系统

**解决问题**：机器人只能被动响应指令，遇到危险（如怪物、掉落）无法自主避险。

**优化方案**：
- **自动进食**：饥饿值低于阈值时尝试使用食物（当前版本记录日志，未来扩展背包操作）
- **自动逃离**：检测到实体靠近时自动向反方向移动（默认关闭，避免干扰用户意图）
- **行为优先级**：自主行为通过动作队列插队执行，但不会中断正在执行的用户指令

**配置项**：
- `enable_autonomous_behaviors`（默认开启）
- `auto_eat_threshold`（饥饿值阈值，默认 10）
- `auto_flee_enabled`（自动逃离开关，默认关闭）

**日志示例**：
```
[INFO] [自主行为] 检测到饥饿（food=8 < 10），尝试自动进食
[INFO] [自主行为] 检测到实体 (僵尸) 在 3.2 格内，触发逃离行为
```

---

### 4. 智能寻路

**解决问题**：直线移动遇到障碍物（墙、坑）会卡住或掉落。

**优化方案**：
- 基于 **A\* 算法**在 XZ 平面寻路（8 方向扩展）
- Y 坐标变化惩罚：高度差 >3 格代价 +100，>1 格代价 +10（避免陡坡）
- 自动回退：寻路失败或代价超过阈值时改用直线移动
- 触发条件：目标距离 >10 格时启用寻路

**配置项**：
- `enable_pathfinding`（默认开启）
- `pathfinding_max_cost`（最大代价阈值，默认 1000，约等于 1000 格搜索半径）

**限制**：
- 当前版本未加载服务器地形数据，寻路基于简化地图（假设地面平坦）
- 未来版本可扩展：接收 Chunk Data 包后构建真实地形地图

---

## 指令总表

| 指令 | 别名 | 说明 |
|---|---|---|
| `/mc 状态` | 查服 | 机器人 + 服务器状态 |
| `/mc 起服` / `/mc 停服` | — | 本地服启停 |
| `/mc 连接` / `/mc 断开` | 进服 / 退服 | 机器人进/出服 |
| `/mc 订阅` / `/mc 退订` | — | 聊天桥订阅 |
| `/mc 说话 <内容>` | — | 游戏内说话 |
| `/mc 移动 <x> <z>` | — | 直线移动 |
| `/mc 挖掘 <x> <y> <z>` | — | 挖掘方块 |
| `/mc 跟随 <玩家>` | — | 跟随玩家 |
| `/mc 看向 <yaw> <pitch>` | — | 转向 |
| `/mc 玩家` | 在线 | 在线玩家 |

---

## 数据与目录

- 插件数据目录：`AstrBot/data/plugin_data/astrbot_plugin_minecraft/`
  - `server/`：本地服务器（jar、世界、配置）
  - `mc_subscribers.json`：聊天桥订阅会话列表
- 第三方依赖安装位置：`AstrBot/data/site-packages/`（mcproto 及其依赖）

## 断线与重连

- 服务器崩溃 / 网络断开后，机器人按 `max_reconnect_times` 指数退避重连（3s → 6s → 12s …封顶 60s）
- 本地服务器崩溃会自动重启（最多 3 次，间隔递增），`/mc 停服` 或卸载插件时优雅停止

## 常见问题

**Q：机器人进服提示「服务器开启了正版验证」**
服务器 `online-mode=true`，机器人不支持正版登录。本地服已默认关闭；远程服请关闭正版验证，或在服务器上为机器人单独放行。

**Q：连不上远程服务器 / 被踢**
确认目标服务器为原版/Paper 类、版本 **1.20.1**（协议 763）、未开启正版验证、且无反作弊插件封禁未签名聊天。

**Q：本地服务器起不来**
查看插件日志 `[mc-server]` 行；常见原因：Java 版本 <17（到配置里指定 `java_path`）、端口被占用（改 `local_port`）、下载被墙（会回退 Mojang 官方源）。

**Q：`/mc 挖掘` 报「超出可挖掘距离」**
机器人需要站到方块 4.5 格内；先 `/mc 移动` 靠近再挖。

## 开发与测试

```
D:\AstrBot\backend\python\python.exe tests\run_tests.py    # 无头单测（30 项，纯逻辑，不起服）
D:\AstrBot\backend\python\python.exe tests\e2e_local.py    # 端到端实测（真实起服 + 进服 + 聊天/移动/挖掘）
```

## 已知边界（v0.2）

- 不做正版（Microsoft）账号验证
- 不进 Forge / Fabric 模组服（协议差异，需要服务端插件桥接方案）
- 不做背包/合成系统：当前版本自主行为「自动进食」仅记录日志，未来扩展背包操作后可真实使用食物
- 不兼容带反作弊（如 Grim/Vulcan）的服务器
- 机器人不加载区块地图，寻路基于简化地图（假设地面平坦），未来版本可扩展真实地形寻路
