"""工具层自己的异常类型。

为什么不用 ToolException：
    老项目的工具统一抛 langchain_core.tools.ToolException，再靠
    handle_tool_error=True 转成错误 ToolMessage。新项目的
    ToolErrorHandlingMiddleware 已经会兜住工具抛出的任何异常，
    所以这里不需要继承 ToolException，只要把「参数错」和「数据错」
    两类语义分开，错误文案按「什么错了 + 怎么改」写清楚即可。

    CompanyClientError（上游 HTTP / 业务错误）已在 core.clients 定义，
    这里不再重复，工具直接从 core.clients import 即可。
"""

from __future__ import annotations


class ToolInputError(RuntimeError):
    """调用方（模型）给的参数不合法：缺、错、越界、重复等。

    抛这个错意味着「换一组正确的参数重试」可能成功，文案要能指导模型怎么改。
    """


class ToolDataError(RuntimeError):
    """公司接口返回的数据形状/语义不符合预期，不能安全地展示给用户。

    抛这个错意味着不是参数的锅，而是上游数据异常，模型不该无脑重试。
    """
