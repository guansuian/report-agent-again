"""导出状态与文件下载入口：每次请求都由公司后端检查当前用户权限。

参考 DeerFlow artifacts 路由的职责分工，网关负责鉴权和交付文件。
本项目文件保存在公司后端，不读取沙箱文件，也不调用模型或创建新任务。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Path, Request, Response

from app.gateway.deps import get_app_company_client, get_company_request_user
from core.clients import CompanyClient, CompanyClientError
from core.runtime.request_context import use_company_access
from core.tools.common.errors import ToolDataError
from core.tools.export.create_export import _read_export_result

router = APIRouter(prefix="/api/exports", tags=["exports"])

# 与 CompanyClient.download_export 的编号规则保持一致，避免把编号当作路径。
ExportId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,128}$")]


@contextmanager
def _company_access(request: Request) -> Iterator[CompanyClient]:
    """两个入口共用认证和错误处理；凭据只在本次请求期间有效。"""
    user = get_company_request_user(request, user_id=None)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="查询或下载导出文件需要用户 Token。",
            headers={"WWW-Authenticate": "Bearer"},
        )
    client = get_app_company_client(request)

    try:
        with use_company_access(client, user):
            yield client
    except CompanyClientError as exc:
        status_code = 502
        if exc.status_code in (401, 403, 404, 410):
            status_code = exc.status_code
        raise HTTPException(status_code=status_code, detail=str(exc)) from None
    except ToolDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


def _get_export_task(client: CompanyClient, export_id: str) -> dict:
    """查询一次状态，保留文件信息，下载路径只指向本项目网关。"""
    response = client.get(
        f"/api/v1/agent/exports/{export_id}",
        params={"contract_version": "agent-data-v1"},
    )
    # 复用创建工具对编号、状态、格式和文件名的检查，不核对查询条件回显。
    task = _read_export_result(response)
    if task["export_id"] != export_id:
        raise ToolDataError("导出接口返回了其他任务的信息，不能用于本次查询或下载。")

    data = response["data"]
    for field in ("row_count", "byte_size", "expires_at", "error"):
        task[field] = data.get(field)

    task["download_path"] = None
    if task["status"] == "ready":
        task["download_path"] = f"/api/exports/{export_id}/download"
    return task


@router.get("/{export_id}")
def get_export_status(export_id: ExportId, request: Request, response: Response) -> dict:
    """返回最新任务状态，供前端轮询；不保存状态，也不创建导出任务。"""
    with _company_access(request) as client:
        task = _get_export_task(client, export_id)

    response.headers["Cache-Control"] = "private, no-store"
    return task


@router.get("/{export_id}/download")
def download_export(export_id: ExportId, request: Request) -> Response:
    """同一用户身份先查状态、再下载，文件字节不经过模型或 run_events。"""
    with _company_access(request) as client:
        task = _get_export_task(client, export_id)
        if task["status"] != "ready":
            raise HTTPException(
                status_code=409,
                detail="文件尚未就绪、已失败或已过期，请先查询导出状态。",
            )

        # 复用现有客户端：读取公司文件响应，沿用其 50 MB 大小限制。
        content, content_type = client.download_export(export_id)

    # 中文文件名按 UTF-8 编码放进下载响应头，不信任公司返回的下载 URL。
    file_name = quote(task["file_name"], safe="")
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{file_name}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
