# AstrBot 集成说明

## 概述

插件已完全集成 AstrBot 的人格配置和 LLM 系统，机器人的所有 LLM 回复和决策都将使用 AstrBot 全局配置的人格和模型，而不再依赖插件内置的人格系统。

## 功能特性

### 1. 人格系统集成

机器人的 LLM 回复将按以下优先级使用人格配置：

1. **AstrBot Provider 人格** (最高优先级)
   - 从 `context.get_using_provider().personality` 获取
   - 在 AstrBot WebUI 的 Provider 设置中配置

2. **插件自定义人格描述** (降级)
   - 配置项: `persona_custom_desc`
   - 当 AstrBot Provider 未配置人格时使用

3. **插件内置人格** (最终降级)
   - 仅当以上两者都不可用时使用
   - 仅用于提供预设台词模板,不影响 LLM 回复

### 2. 游戏内聊天唤醒词

机器人不再回复游戏内的每一条消息，而是仅在检测到唤醒词时回复。

**配置项:**
```json
{
  "mc_chat_wake_words": ["AstrBot", "机器人", "小助手"],
  "auto_reply_in_game": true
}
```

**触发条件:**
- 消息包含任一唤醒词(不区分大小写)
- 消息以 `@` 开头
- 默认唤醒词:机器人的用户名(`bot_username`)

**示例:**
```
玩家: "AstrBot 你在哪里?"  ✓ 触发回复
玩家: "机器人过来帮忙"    ✓ 触发回复
玩家: "@有人吗?"           ✓ 触发回复
玩家: "这里有个洞穴"       ✗ 不触发回复
```

### 3. 生存行为系统

机器人现在具备真正的生存能力,会自动检测饥饿并吃食物。

**配置项:**
```json
{
  "enable_survival_behaviors": true,
  "survival_food_threshold": 14,
  "survival_food_cooldown": 10.0
}
```

**行为说明:**
- 当饥饿度 < 阈值(默认 14)时,自动在快捷栏寻找食物并吃掉
- 冷却时间(默认 10 秒)防止频繁进食
- 成功进食后会使用人格台词表达(如"吃饱啦,谢谢款待!")

**支持的食物:**
- 面包(393)、牛肉/猪肉(363、320)、苹果(260)等常见食物
- 仅在快捷栏(槽位 0-8)查找食物

## 配置完整示例

```json
{
  "bot_username": "AstrBot",
  "server_mode": "local",
  "local_port": 25565,

  // LLM 与人格配置
  "llm_model": "",  // 留空使用 Provider 默认模型
  "persona_custom_desc": "",  // 降级人格描述

  // 游戏内聊天唤醒词
  "auto_reply_in_game": true,
  "mc_chat_wake_words": ["AstrBot", "机器人", "小助手"],

  // 自主行为配置
  "enable_autonomous_behaviors": true,
  "enable_survival_behaviors": true,
  "survival_food_threshold": 14,
  "survival_food_cooldown": 10.0,

  // 空闲行为配置
  "enable_idle_behaviors": true,
  "idle_broadcast_interval": 180,
  "idle_llm_generation": true,
  "enable_mood": true
}
```

## 技术实现

### LLM 调用流程

```
用户消息/触发条件
    ↓
检查唤醒词 (_should_reply_to_message)
    ↓
获取 system_prompt (_get_system_prompt)
    ├→ AstrBot Provider.personality  [优先]
    ├→ 插件 persona_custom_desc      [降级]
    └→ 插件内置人格                  [最终降级]
    ↓
调用 LLM (_llm_chat)
    ├→ provider.text_chat(system_prompt=...)  [主路径]
    └→ context.llm_generate(merged_prompt)    [降级路径]
    ↓
返回回复
```

### 生存行为触发流程

```
行为管理器定期检查(每 5 秒)
    ↓
饥饿度 < 阈值 且 快捷栏有食物?
    ↓
切换手持槽位到食物槽
    ↓
发送 Use Item 数据包(右键吃东西)
    ↓
等待 1.5 秒(游戏内进食动画时间)
    ↓
检查饥饿度恢复情况,使用人格台词表达
```

## 使用建议

1. **配置 AstrBot 人格**:在 AstrBot WebUI 的 Provider 设置中配置全局人格,插件将自动使用

2. **设置唤醒词**:根据实际需求配置唤醒词列表,避免机器人过度回复刷屏

3. **准备食物**:确保机器人快捷栏(0-8 槽位)有食物,否则饥饿时会提示"快捷栏没有食物"

4. **监控行为**:通过 `/mc订阅` 订阅游戏内消息,实时查看机器人的自主行为和台词

## 已知限制

1. **食物检测**:仅支持快捷栏的食物,背包(9-35 槽位)的食物暂不支持自动转移

2. **唤醒词匹配**:简单包含匹配,无法识别复杂语境(如"我不想叫机器人")

3. **人格切换**:运行时更改 AstrBot 人格配置需要重启 AstrBot 才能生效

## 版本历史

- **v0.3.0** (2026-09-09)
  - 集成 AstrBot 人格配置系统
  - 添加游戏内聊天唤醒词功能
  - 实现自动进食生存行为
  - 修改 LLM 调用逻辑使用 AstrBot 全局配置
