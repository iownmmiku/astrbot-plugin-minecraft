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
| `auto_reply_in_game` | 游戏内聊天触发 LLM 回复（需包含唤醒词） | 开 |
| `mc_chat_wake_words` | 游戏内聊天唤醒词列表（默认为机器人名字） | `[]` |
| `persona_custom_desc` | 自定义人格描述（降级使用，优先使用 AstrBot Provider 人格） | 空 |
| `llm_model` | LLM 模型名（留空使用 Provider 默认模型） | 空 |
| `max_reconnect_times` | 断线自动重连次数（指数退避） | 5 |
| `enable_action_queue` | 启用异步动作队列（LLM 工具立即返回） | 开 |
| `enable_pathfinding` | 启用智能寻路（A* 绕障碍） | 开 |
| `pathfinding_max_cost` | 寻路最大代价（超过改用直线） | 1000 |
| `enable_autonomous_behaviors` | 启用自主行为（自动避险） | 开 |
| `enable_survival_behaviors` | 启用生存行为（自动进食） | 开 |
| `survival_food_threshold` | 自动进食饥饿值阈值 | 14 |
| `survival_food_cooldown` | 自动进食冷却时间（秒） | 10.0 |
| `auto_eat_threshold` | (已弃用，使用 survival_food_threshold) | 10 |
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

## 核心架构优化 (v0.3 - 2026-09-10)

本次更新完成 7 个阶段的系统性重构，将插件从"演示级实现"升级为**基于真实动作验证的可靠游戏客户端**。

### 关键改进

#### 1. 真实世界状态感知
- **方块缓存系统**：实时缓存 32 格半径内的方块状态，机器人可以"看见"周围环境
- **实体追踪系统**：自动分类追踪玩家、掉落物、敌对/被动生物，支持查找最近实体
- **完整库存模型**：100+ 种物品映射，按名称聚合数量，支持组件化物品

#### 2. 动作验证机制
- **移动验证**：不再"发送=完成"，等待服务器位置确认，检测卡住（3秒不动判定失败）
- **挖掘验证**：等待服务器方块变化确认，验证库存变化，超时返回明确错误
- **失败诊断**：所有动作失败带明确原因（"目标位置是空气"、"移动卡住"、"挖掘超时"）

#### 3. 自主目标系统 V2
- **真实执行**：`CollectWoodGoal` 真实搜索原木、移动、挖掘、拾取，验证库存变化
- **步骤重试**：每步失败自动重试（max_retries=3），超过限制返回诊断信息
- **断点续玩**：步骤进度、重试次数、错误信息持久化，死亡/断线后可恢复
- **无技术债务**：所有目标基于可观测完成条件，0 个 TODO 占位符

#### 4. 人格和模型统一配置
- **三级人格优先级**：AstrBot 全局人格 > AstrBot 指定人格 > 插件自定义人格
- **模型独立配置**：支持跟随 AstrBot 或使用插件独立模型
- **统一接口**：所有 LLM 调用（游戏内聊天、空闲台词、目标决策）自动应用配置

### 新增命令
- `/mc人格 [astrbot|select <ID>|custom <描述>]` - 管理人格来源
- `/mc模型 [default|custom <模型名>]` - 管理模型来源
- `/mc目标` - 查看当前目标进度（步骤/重试/错误）
- `/mc目标 启动` - 启动自主目标系统（机器人自动采集木头）

详见：[CHANGELOG_OPTIMIZE.md](CHANGELOG_OPTIMIZE.md) | [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md)

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

**解决问题**：机器人只能被动响应指令，遇到危险（如怪物、掉落）无法自主避险，无法真正「生存」。

**优化方案**：
- **自动进食**：饥饿值低于阈值时自动在快捷栏寻找食物并吃掉，成功后使用人格台词表达（如"吃饱啦！"）
- **自动逃离**：检测到实体靠近时自动向反方向移动（默认关闭，避免干扰用户意图）
- **行为优先级**：自主行为通过动作队列插队执行，但不会中断正在执行的用户指令

**配置项**：
- `enable_autonomous_behaviors`（默认开启）
- `enable_survival_behaviors`（生存行为开关，默认开启）
- `survival_food_threshold`（饥饿值阈值，默认 14）
- `survival_food_cooldown`（进食冷却时间，默认 10.0 秒）
- `auto_flee_enabled`（自动逃离开关，默认关闭）

**日志示例**：
```
[INFO] 触发生存行为：饥饿度 12，准备吃食物
[INFO] 正在吃食物（槽位 3）
[MC] AstrBot：（摸了摸肚子，满足地笑了）嗯～吃饱啦，谢谢款待！
```

**支持的食物**：面包、牛肉、猪肉、鸡肉、苹果、金苹果、胡萝卜、烤土豆、曲奇等常见食物（仅在快捷栏 0-8 槽位查找）

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

### 5. AstrBot 人格与 LLM 集成 **NEW!**

**解决问题**：插件内置的人格系统与 AstrBot 全局配置脱节，无法统一管理机器人的性格和语气；游戏内聊天机器人会回复每一条消息导致刷屏。

**优化方案**：
- **统一人格系统**：机器人的所有 LLM 回复（游戏内聊天、空闲台词）自动使用 AstrBot Provider 配置的全局人格
- **人格降级链**：AstrBot Provider 人格 → 插件自定义人格描述 → 插件内置人格（仅用于预设台词模板）
- **唤醒词机制**：游戏内聊天需包含唤醒词才触发 LLM 回复，避免对每条消息都响应

**配置项**：
- `mc_chat_wake_words`：唤醒词列表（默认为机器人名字），支持多个词
- `auto_reply_in_game`：启用游戏内聊天 LLM 回复（默认开启）
- `persona_custom_desc`：自定义人格描述（当 AstrBot Provider 未配置人格时使用）
- `llm_model`：指定 LLM 模型名（留空使用 Provider 默认模型）

**使用示例**：
```json
{
  "mc_chat_wake_words": ["AstrBot", "机器人", "小助手"],
  "persona_custom_desc": "你是一个活泼可爱的 Minecraft 玩家，喜欢探险和建造。"
}
```

**游戏内对话**：
```
玩家: "今天天气真好"           ✗ 不回复（无唤醒词）
玩家: "AstrBot 你在哪？"       ✓ 触发回复："我在坐标 (123, 64, 456) 呢！"
玩家: "机器人过来帮忙"         ✓ 触发回复："好的，马上来！"
玩家: "@有人在吗？"            ✓ 触发回复（@ 符号自动触发）
```

**人格配置步骤**：
1. 在 AstrBot WebUI → LLM Provider 设置中配置全局人格（推荐）
2. 或在插件配置中填写 `persona_custom_desc` 自定义人格描述
3. 插件将自动使用配置的人格，无需重启

详细说明请参考：[AstrBot 集成文档](docs/ASTRBOT_INTEGRATION.md)

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

## 已知边界

### 当前版本限制
- **不支持正版验证**：仅支持离线（非正版）账号
- **不支持模组服**：仅适配原版/Paper/Spigot 类服务器，无法进入 Forge/Fabric 模组服
- **不兼容反作弊**：部分反作弊插件（如 Grim/Vulcan）可能封禁机器人

### 待完整实现功能
- **合成系统**：`craft_item()` 目前是框架，需实现配方摆放和点击逻辑
- **路径规划**：当前移动是直线，遇障碍物会卡住，需要 A* 绕路算法（pathfinding 部分实现）
- **更多目标**：仅实现 `CollectWoodGoal`（采集木头），待添加：
  - `CraftWoodenToolsGoal` - 合成木镐、工作台
  - `MineStoneGoal` - 挖掘圆石
  - `HuntForFoodGoal` - 狩猎动物、采集食物

### 优化方向
- **性能优化**：方块缓存当前是字典，大区域可能占用内存
- **LLM 决策**：目标选择当前是固定规则，可接入 LLM 智能决策
- **多人协作**：支持多个机器人协同完成复杂任务
