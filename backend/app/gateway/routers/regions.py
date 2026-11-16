"""地区查询入口：复用公司接口，返回当前用户可见的完整地区树。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.gateway.deps import get_app_company_client, get_company_request_user
from core.clients import CompanyClientError
from core.runtime.request_context import use_company_access
from core.tools.common.errors import ToolDataError
from core.tools.common.regions import get_region_tree

router = APIRouter(prefix="/api/regions", tags=["regions"])


@router.get("")
def list_regions(request: Request) -> dict:
    """只读取请求头中的身份，不接收 user_id，也不创建 run。"""
    user = get_company_request_user(request, user_id=None)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="查询地区需要用户 Token。",
            headers={"WWW-Authenticate": "Bearer"},
        )

    client = get_app_company_client(request)
    try:
        # 沿用工具查询时的身份传递方式；退出 with 后恢复，避免串用凭据。
        with use_company_access(client, user):
            regions = get_region_tree()
    except CompanyClientError as exc:
        # 身份和权限错误保留原状态；公司接口的其他失败统一表示为上游错误。
        status_code = 502
        if exc.status_code in (401, 403):
            status_code = exc.status_code
        raise HTTPException(status_code=status_code, detail=str(exc)) from None
    except ToolDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return {"list": regions}
