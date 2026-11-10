"""会话（thread）元信息的 HTTP 路由 —— 对应 deer-flow 的
app/gateway/routers/threads.py（最小版）。

deer-flow 这个文件有 1900+ 行、十几个接口（线程 CRUD、分支、搜索、状态、
goal、compact、history……）。这里只留「会话列表」真正需要的 5 个：

    POST   /api/threads              新建会话（登记一条 threads_meta）
    GET    /api/threads              会话列表（最近更新的在前）
    GET    /api/threads/{thread_id}  会话详情
    PATCH  /api/threads/{thread_id}  改标题 / 合并 metadata
    DELETE /api/threads/{thread_id}  删会话（连带删消息事件）

业务逻辑不写在这里：拿 store、调方法、把结果交回去。
路径怎么读：

    /api/threads/t1
    └──┬──┘ └──┬──┘
      前缀    会话 id（不传 = 集合）

和 thread_runs.py 的关系：
    thread_runs.py 管「会话里的 run / 消息」（/api/threads/{id}/runs、
    /messages）；本文件管「会话本身」。两个 router 前缀相同但不冲突，
    FastAPI 会按完整路径匹配。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway.deps import get_event_store, get_thread_store

router = APIRouter(prefix="/api/threads", tags=["threads"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class ThreadCreateRequest(BaseModel):
    """新建会话的请求体。

    thread_id 可以不传：前端已经有 id（localStorage 里的会话）时直接带上，
    没带就由后端生成一个 UUID。这样两种调用方式都能工作：
        POST /api/threads {"thread_id": "t1", "display_name": "销售额"}
        POST /api/threads {"display_name": "新对话"}
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = Field(default=None, max_length=64)
    user_id: str | None = Field(default=None, max_length=64)
    display_name: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] | None = None

    @field_validator("user_id")
    @classmethod
    def _user_id_not_blank(cls, value: str | None) -> str | None:
        """传了 user_id 就必须是非空字符串；前后空格去掉再存。"""
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("user_id 不能是空字符串")
        return value

    @field_validator("display_name")
    @classmethod
    def _display_name_not_blank(cls, value: str | None) -> str | None:
        """传了标题就必须是非空字符串；前后空格去掉再存。"""
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("display_name 不能是空字符串")
        return value


class ThreadPatchRequest(BaseModel):
    """修改会话的请求体。

    两个字段都可选：只传 display_name 就只改标题，只传 metadata 就只合并
    metadata。两个都不传 = 什么都不改，直接返回当前记录（幂等）。
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] | None = None

    @field_validator("display_name")
    @classmethod
    def _display_name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("display_name 不能是空字符串")
        return value


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.post("")
async def create_thread(body: ThreadCreateRequest, request: Request) -> dict:
    """新建（或登记）一个会话，返回 threads_meta 里的那条记录。

    幂等：thread_id 已存在时不报错、不覆盖，原样返回已有记录。
    前端「新对话」按钮可以先 POST 一次再发消息；就算没 POST，
    POST /runs/stream 的 start_run 也会自动 ensure 一行。
    """
    store = get_thread_store(request)
    thread_id = body.thread_id or str(uuid.uuid4())
    return await store.create(
        thread_id,
        user_id=body.user_id,
        display_name=body.display_name,
        metadata=body.metadata,
    )


@router.get("")
async def list_threads(
    request: Request,
    user_id: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """列出会话，最近更新的在前。

    对应 deer-flow 的 /api/threads/search（那边还叠了 metadata 过滤、
    置顶排序、归档过滤、归属过滤）。最小版只留 user_id / status 过滤和分页。
    """
    return await get_thread_store(request).search(
        user_id=user_id, status=status, limit=limit, offset=offset
    )


@router.get("/{thread_id}")
async def get_thread(thread_id: str, request: Request) -> dict:
    """查一个会话的元信息；不存在返回 404。"""
    record = await get_thread_store(request).get(thread_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"会话 {thread_id} 不存在")
    return record


@router.patch("/{thread_id}")
async def patch_thread(
    thread_id: str,
    body: ThreadPatchRequest,
    request: Request,
) -> dict:
    """改标题 / 合并 metadata，返回改完后的完整记录。

    先查一次是为了「不存在就 404」；存在时再按传了哪些字段分别更新。
    metadata 走 store 的合并语义，不会把别的键冲掉。
    """
    store = get_thread_store(request)
    if await store.get(thread_id) is None:
        raise HTTPException(status_code=404, detail=f"会话 {thread_id} 不存在")

    if body.display_name is not None:
        await store.update_display_name(thread_id, body.display_name)
    if body.metadata is not None:
        await store.update_metadata(thread_id, body.metadata)

    record = await store.get(thread_id)
    # 理论上不会为 None（上面刚查过），但静态类型需要收窄。
    if record is None:
        raise HTTPException(status_code=404, detail=f"会话 {thread_id} 不存在")
    return record


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, request: Request) -> Response:
    """删会话：threads_meta 一行 + 这个 thread 的全部消息事件。

    为什么连事件一起删：
        threads_meta 只是「会话列表的一行」，用户点「删除会话」时语义上是
        整个会话都不要了；留着 run_events 会让消息在重新打开同一个 id 时
        又冒出来，很反直觉。

    为什么不删 runs：
        runs 是「每次执行」的审计记录（状态、错误、耗时），前端任务记录
        还要看。deer-flow 的删除接口会连 runs 一起清，最小版先保留，
        等真要「彻底抹掉」时再补 RunStore.delete_by_thread。

    两个都删不到才 404：兼容「功能上线前建的线程」——只有事件、没有
    threads_meta 行，也应该能被删除。
    """
    store = get_thread_store(request)
    deleted_meta = await store.delete(thread_id)
    deleted_events = await get_event_store(request).delete_by_thread(thread_id)
    if not deleted_meta and deleted_events == 0:
        raise HTTPException(status_code=404, detail=f"会话 {thread_id} 不存在")
    return Response(status_code=204)
