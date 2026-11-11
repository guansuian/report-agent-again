"""统一工具装饰器 —— 在 @tool 之上补齐本项目业务工具的硬性约定。

deer-flow 是每个工具手写 @tool(parse_docstring=True)，再用一个「扫描测试」
断言参数都有描述（见 test_tool_args_schema_no_pydantic_warning.py）。本项目
刻意偏离这一点：把三件每个业务工具都要的事打包进一个装饰器，一次写对、全员一致：

    1. parse_docstring=True            —— 把 Google 风格 docstring 的 Args 转成参数描述
    2. error_on_invalid_docstring=True —— docstring 写错时 import 期就报错，而不是静默生成错 schema
    3. extra="forbid"                  —— 未声明参数直接拒绝，而不是被 pydantic 静默丢弃

第 3 点是关键偏离：LangChain 1.7.3 生成的 args_schema 默认 extra=None（等于
pydantic 的 ignore），模型拼错参数（如 start_tim）会被静默吞掉，工具带着
「缺了该参数」的错误口径继续跑。报表工具宁可报错重试，不能静默算错。

注意：设了 model_config["extra"]="forbid" 后必须 model_rebuild(force=True)，
pydantic v2 缓存 schema，不重建就不生效。
"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import tool


def report_tool(func: Callable[..., Any] | None = None, **tool_kwargs: Any):
    """业务工具的入口装饰器，支持 @report_tool 和 @report_tool(...) 两种写法。

    除强制 parse_docstring / error_on_invalid_docstring / extra=forbid 外，
    其余关键字原样透传给 langchain_core.tools.tool（如 name、response_format、
    return_direct 等），所以使用方式和 @tool 基本一致。
    """

    def decorate(fn: Callable[..., Any]):
        t = tool(fn, parse_docstring=True, error_on_invalid_docstring=True, **tool_kwargs)
        schema = t.args_schema
        if schema is not None:
            schema.model_config["extra"] = "forbid"
            schema.model_rebuild(force=True)
        return t

    if func is None:
        return decorate
    return decorate(func)
