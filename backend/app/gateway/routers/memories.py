"""长期记忆列表入口：参考 DeerFlow 的 memory 路由，由 Manager 负责读取。"""

from fastapi import APIRouter, HTTPException, Query, Request

from app.gateway.deps import get_memory_manager

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.get("")
async def list_memories(
    request: Request,
    user_id: str = Query(..., min_length=1, max_length=64),
) -> dict:
    """按传入的用户 ID 读取记忆；不创建 run，也不调用模型。"""
    user_id = user_id.strip()
    if not user_id:
        raise HTTPException(status_code=422, detail="user_id 不能是空字符串")

    # 复用启动时创建的管理器；未启用或未初始化时，由 getter 返回 503。
    manager = get_memory_manager(request)
    return await manager.list_memories(user_id=user_id)
