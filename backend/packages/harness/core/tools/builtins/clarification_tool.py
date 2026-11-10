"""澄清工具 —— 让模型可以「反过来问用户」。

对应真实 deer-flow 的 deerflow/tools/builtins/clarification_tool.py（最小版）。

它和 query_report 最大的区别：**函数体永远不会执行**。
ClarificationMiddleware 在看到 tool_call 的那一刻就把它截下来，换成一条
ToolMessage，然后直接结束本轮（goto=END）。所以这里只需要把「工具长什么样」
写清楚，真正的逻辑在中间件里。

那不写这个函数行不行？不行 —— 模型只能调用注册在工具列表里的工具。
没有它，模型就没有 ask_clarification 这个选项，也就永远不会主动提问。

注意：下面的 docstring 会原样转成工具 schema 发给模型。这份文字就是模型判断
「什么时候该问、该问什么」的唯一依据，所以当说明书写，别当注释写。
"""

from __future__ import annotations

from typing import Literal, Required, TypedDict

from langchain_core.tools import tool

# 问题类型。用 Literal 而不是自由字符串：模型只能从这几个值里挑，
# 前端也能据此决定用什么样式展示。
ClarificationType = Literal[
    "missing_info",           # 缺少必要信息（时间范围、指标口径、拆分维度…）
    "ambiguous_requirement",  # 一句话能理解成好几种做法
    "approach_choice",        # 有多种做法，要用户选一个
    "risk_confirmation",      # 有风险的操作用户确认
    "suggestion",             # 自己有建议，问用户要不要
]


class ClarificationFormField(TypedDict, total=False):
    """表单里一个字段的定义（模型传给 ask_clarification 的形状）。

    真正的校验在 ClarificationMiddleware 里做，这里只把形状写进 schema 给
    模型看（中间件在工具执行前就截住，函数体不会真跑）。
    """

    name: Required[str]  # 字段唯一标识（不能用 constructor/toString 这类 JS 原型名）
    label: str           # 显示名，缺省用 name
    type: Literal["text", "textarea", "number", "select", "multi_select", "checkbox"]
    required: bool       # 是否必填，默认 False
    options: list[str]   # select / multi_select 的候选项
    placeholder: str     # 输入框提示


@tool("ask_clarification", parse_docstring=True, return_direct=True)
def ask_clarification_tool(
    question: str,
    clarification_type: ClarificationType,
    context: str | None = None,
    options: list[str] | None = None,
    fields: list[ClarificationFormField] | None = None,
) -> str:
    """当信息不足或需求有歧义时，先向用户提问，等用户回答后再继续。

    遇到下面这些情况要先问，不要自己猜：
    - 缺少必要信息：没说时间范围、指标口径、拆分维度
    - 需求有歧义：同一句话能理解成好几种做法
    - 有多种做法：需要用户选一个方向
    - 有风险的操作：可能要改数据、删东西，先确认

    怎么组织问题：
    - 一次只问一件事，问题要具体
    - 如果是「请用户选一个」，把候选放进 options，前端会画成按钮
    - 如果要一次收集多个值（比如好几个参数），用 fields 画成一个表单，别一个问题一个问题问
    - 如果要「多选」，用一个 type=multi_select 的字段，别塞进 options
    - 调用本工具后本轮会立刻结束，等用户下一条消息再继续

    Args:
        question: 要问用户的问题。写具体，不要写「请提供更多信息」这种空话。
        clarification_type: 问题属于哪一类，取值见上面的类型定义。
        context: 可选的补充说明，解释为什么要问，帮用户理解背景。
        options: 可选的候选项列表。只有「请用户选一个」时才填，最多 10 个。
        fields: 可选的表单字段定义，一次收集多个值。传了 fields 就优先用它，忽略 options。
            每个字段是 {name(唯一标识), label(显示名), type(text/textarea/number/select/multi_select/checkbox),
            required(是否必填), options(select/multi_select 必填), placeholder(提示)}。
            最多 16 个字段、每字段 24 个选项、名字/标签/选项/占位符最多 200 字，超了会整体降级成纯文本问题。
    """
    # 正常不会走到这里：ClarificationMiddleware 在工具执行前就截住并结束本轮。
    # 留一句兜底返回值，万一中间件没挂上，模型拿到的是这句话，而不是异常。
    return "已向用户提问，本轮结束。"
