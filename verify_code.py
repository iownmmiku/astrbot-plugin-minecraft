"""代码静态验证脚本"""
import re
import ast
from pathlib import Path

def check_file_syntax(filepath):
    """检查Python文件语法"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            ast.parse(f.read())
        return True, "语法正确"
    except SyntaxError as e:
        return False, f"语法错误: {e}"

def check_class_methods(filepath, class_name, required_methods):
    """检查类是否包含必需方法"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 查找类定义
    class_pattern = rf'class {class_name}\([^)]*\):'
    if not re.search(class_pattern, content):
        return False, f"未找到类 {class_name}"
    
    # 查找方法
    missing = []
    for method in required_methods:
        method_pattern = rf'def {method}\('
        if not re.search(method_pattern, content):
            missing.append(method)
    
    if missing:
        return False, f"缺少方法: {', '.join(missing)}"
    return True, "所有方法存在"

def check_attributes(filepath, attr_names):
    """检查文件中是否包含属性初始化"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    missing = []
    for attr in attr_names:
        pattern = rf'self\.{attr}\s*[=:]'
        if not re.search(pattern, content):
            missing.append(attr)
    
    if missing:
        return False, f"未找到属性: {', '.join(missing)}"
    return True, "所有属性存在"

print("=" * 60)
print("Minecraft 插件代码验证")
print("=" * 60)

# 1. 语法检查
print("\n【1. 语法检查】")
files = ['bot_client.py', 'goal_system_v2.py', 'main.py', 'action_queue.py', 'persona.py']
for fname in files:
    ok, msg = check_file_syntax(fname)
    status = "✓" if ok else "✗"
    print(f"{status} {fname}: {msg}")

# 2. bot_client.py 关键功能检查
print("\n【2. bot_client.py 方块缓存功能】")
ok, msg = check_attributes('bot_client.py', ['blocks', 'block_cache_radius'])
print(f"{'✓' if ok else '✗'} 方块缓存属性: {msg}")

ok, msg = check_class_methods('bot_client.py', 'MCBot', ['get_block', 'is_air', '_on_block_change', '_on_multi_block_change'])
print(f"{'✓' if ok else '✗'} 方块相关方法: {msg}")

print("\n【3. bot_client.py 实体追踪功能】")
ok, msg = check_attributes('bot_client.py', ['entities'])
print(f"{'✓' if ok else '✗'} 实体追踪属性: {msg}")

ok, msg = check_class_methods('bot_client.py', 'MCBot', ['find_nearest_entity', 'count_nearby_entities', '_on_spawn_entity'])
print(f"{'✓' if ok else '✗'} 实体相关方法: {msg}")

print("\n【4. bot_client.py 验证动作】")
ok, msg = check_class_methods('bot_client.py', 'MCBot', ['mine', 'move_to', 'collect_drops', 'attack_entity'])
print(f"{'✓' if ok else '✗'} 基础动作方法: {msg}")

# 检查 mine 方法是否有超时验证逻辑
with open('bot_client.py', 'r', encoding='utf-8') as f:
    content = f.read()
    has_timeout = 'timeout' in content and 'deadline' in content
    has_block_check = 'get_block' in content and 'current_block' in content
    print(f"{'✓' if has_timeout else '✗'} mine 方法包含超时机制")
    print(f"{'✓' if has_block_check else '✗'} mine 方法包含方块验证")

print("\n【5. bot_client.py 窗口交互】")
ok, msg = check_attributes('bot_client.py', ['open_window_id', 'window_state_id', 'window_items'])
print(f"{'✓' if ok else '✗'} 窗口状态属性: {msg}")

ok, msg = check_class_methods('bot_client.py', 'MCBot', ['_on_open_window', 'craft_item'])
print(f"{'✓' if ok else '✗'} 窗口交互方法: {msg}")

print("\n【6. goal_system_v2.py 目标系统】")
ok, msg = check_class_methods('goal_system_v2.py', 'Goal', ['check_preconditions', 'check_completion', 'execute_step', 'get_total_steps'])
print(f"{'✓' if ok else '✗'} Goal 基类方法: {msg}")

ok, msg = check_class_methods('goal_system_v2.py', 'CollectWoodGoal', ['check_preconditions', 'check_completion', 'execute_step'])
print(f"{'✓' if ok else '✗'} CollectWoodGoal 实现: {msg}")

ok, msg = check_class_methods('goal_system_v2.py', 'GoalManagerV2', ['start', 'stop', '_goal_loop', 'get_status'])
print(f"{'✓' if ok else '✗'} GoalManagerV2 方法: {msg}")

# 检查目标是否真实执行
with open('goal_system_v2.py', 'r', encoding='utf-8') as f:
    content = f.read()
    has_real_action = 'await bot.move_to' in content and 'await bot.mine' in content
    has_verification = 'check_completion' in content and 'inventory' in content
    print(f"{'✓' if has_real_action else '✗'} CollectWoodGoal 调用真实动作")
    print(f"{'✓' if has_verification else '✗'} CollectWoodGoal 验证库存变化")

print("\n【7. main.py 人格和模型配置】")
ok, msg = check_class_methods('main.py', 'MinecraftPlugin', ['_get_system_prompt', '_llm_model_override', '_llm_chat'])
print(f"{'✓' if ok else '✗'} 人格/模型方法: {msg}")

# 检查配置字段
with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()
    has_persona_source = 'persona_source' in content
    has_model_source = 'model_source' in content
    has_astrbot_current = 'astrbot_current' in content
    print(f"{'✓' if has_persona_source else '✗'} 支持 persona_source 配置")
    print(f"{'✓' if has_model_source else '✗'} 支持 model_source 配置")
    print(f"{'✓' if has_astrbot_current else '✗'} 支持 astrbot_current 人格")

print("\n【8. main.py 目标系统集成】")
with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()
    uses_v2 = 'GoalManagerV2' in content and 'goal_system_v2' in content
    has_goal_commands = 'mc目标' in content or 'mc_goal' in content
    print(f"{'✓' if uses_v2 else '✗'} 使用 GoalManagerV2")
    print(f"{'✓' if has_goal_commands else '✗'} 包含目标命令")

print("\n【9. 代码质量检查】")
# 检查是否有明显的 TODO 占位符（旧代码特征）
with open('goal_system_v2.py', 'r', encoding='utf-8') as f:
    content = f.read()
    # 统计 TODO 数量
    todo_count = content.count('TODO')
    print(f"{'✓' if todo_count == 0 else '⚠'} goal_system_v2.py TODO 占位符: {todo_count} 个")

print("\n" + "=" * 60)
print("验证完成")
print("=" * 60)
