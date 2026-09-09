"""Minecraft 操作的人格化反馈包装器

通过更详细的 LLM 工具描述，让 Agent 用人格化的方式表达操作结果。

核心思路：
1. LLM 工具返回详细的结构化数据（坐标、状态、耗时等）
2. 在工具的 docstring 中明确说明「用你的人格和语气向用户汇报」
3. Agent 会根据这些数据和提示，自动用当前人格表达

无需手动调用 LLM 重新包装，AstrBot 的 Agent 会自动处理。
"""

from __future__ import annotations


def make_persona_hint(action: str) -> str:
    """生成人格化提示，附加到工具返回值中
    
    Args:
        action: 操作类型（如 "移动"、"挖掘"）
    
    Returns:
        人格化提示文本
    """
    return f"\n\n[提示：请用你的人格和语气向用户汇报这次{action}的结果，可以加入情绪和评论]"


def format_action_result(
    success: bool,
    action: str,
    details: str,
    error: str | None = None,
    add_persona_hint: bool = True
) -> str:
    """格式化操作结果，附带人格化提示
    
    Args:
        success: 操作是否成功
        action: 操作类型（如 "移动到 (100, 200)"）
        details: 详细信息（如耗时、状态等）
        error: 错误信息（如果失败）
        add_persona_hint: 是否添加人格化提示
    
    Returns:
        格式化的结果文本
    """
    if success:
        result = f"✓ {action}成功。{details}"
    else:
        result = f"✗ {action}失败：{error}。{details}"
    
    if add_persona_hint:
        result += make_persona_hint(action.split()[0])  # 提取操作类型
    
    return result
