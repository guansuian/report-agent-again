"""地区树：从公司接口取当前账号可见的地区，供 check_regions 定位使用。

单独成文件的原因：validate.py 里全是纯函数，可以脱离网络直接测试；
这个函数要发 HTTP，混进去会让校验逻辑依赖请求上下文。
"""

from __future__ import annotations

from core.clients import get_company_client
from core.tools.common.errors import ToolDataError


def get_region_tree() -> list[dict]:
    """拉取当前账号可见的地区树，返回 data.list。

    可见范围由公司接口按 Token 判断，这里不做过滤。
    每次调用都会发一次 HTTP 请求；暂不缓存，由调用方决定要不要复用。
    """
    response = get_company_client().get("/api/v1/agent/regions")

    try:
        regions = response["data"]["list"]
    except (KeyError, TypeError):
        raise ToolDataError("地区接口缺少 data.list。") from None

    if not isinstance(regions, list):
        raise ToolDataError("地区接口的 data.list 必须是列表。")

    return regions
