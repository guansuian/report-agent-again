"""测试公共零件：假 agent、假 app、假请求。

放在这里而不是 conftest.py：这些都是**普通函数**，直接 import 就能用，
不用记 pytest 的 fixture 规则。
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.memory import MemorySaver

from core.clients import CompanyClient
from core.persistence.thread_meta.memory import MemoryThreadMetaStore
from core.runtime.events.store.memory import MemoryRunEventStore
from core.runtime.runs.manager import RunManager
from core.runtime.stream_bridge.memory import MemoryStreamBridge


class FakeAgent:
    """假 agent：不调模型，模拟 LangGraph 的多模式输出和 callback。

    stream_mode 传的是列表时，LangGraph 固定吐 (mode, chunk) 二元组：
        ("values",   完整状态)
        ("messages", (AIMessageChunk, metadata))
    """

    ANSWER = "本月销售额 123 万"
    PIECES = ("本月", "销售额", " 123 万")   # 模拟打字机的一段段输出

    async def astream(self, graph_input, config=None, stream_mode="values"):
        # 模拟 LangGraph 调用 config 里挂的 callback（journal 靠它抓消息落库）
        for cb in (config or {}).get("callbacks", []):
            if hasattr(cb, "on_chat_model_start"):
                cb.on_chat_model_start(
                    {"name": "fake"},
                    [[HumanMessage(content="查一下销售额")]],
                    run_id="llm-1",
                )
            if hasattr(cb, "on_llm_end"):
                cb.on_llm_end(
                    LLMResult(
                        generations=[
                            [ChatGeneration(message=AIMessage(content=self.ANSWER))]
                        ]
                    ),
                    run_id="llm-1",
                )

        modes = stream_mode if isinstance(stream_mode, list) else [stream_mode]
        if "values" in modes:
            yield ("values", {"messages": [AIMessage(content=self.ANSWER)]})
        if "messages" in modes:
            for piece in self.PIECES:
                yield (
                    "messages",
                    (AIMessageChunk(content=piece), {"langgraph_node": "agent"}),
                )


class BoomAgent:
    """假 agent：跑到一半就炸，用来验证错误路径也能正常收尾。"""

    async def astream(self, graph_input, config=None, stream_mode="values"):
        modes = stream_mode if isinstance(stream_mode, list) else [stream_mode]
        if "values" in modes:
            yield ("values", {"messages": [AIMessage(content="我先说一句")]})
        raise RuntimeError("模型超时了")


def make_app() -> FastAPI:
    """建一个「已经摆好货」的 app（相当于 lifespan 跑完了）。

    和真实 create_app() 的区别：这里不跑 lifespan，直接把单例摆到 state 上，
    再挂上路由 —— 这样既能测 getter，也能用 TestClient 打真实路由。
    """
    from app.gateway.routers import thread_runs, threads

    fastapi_app = FastAPI()
    fastapi_app.state.stream_bridge = MemoryStreamBridge()
    fastapi_app.state.run_manager = RunManager()
    fastapi_app.state.event_store = MemoryRunEventStore()
    fastapi_app.state.thread_store = MemoryThreadMetaStore()
    fastapi_app.state.checkpointer = MemorySaver()
    fastapi_app.state.company_client = CompanyClient(
        "https://company.test", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": 200, "data": {}})
        ),
    )
    fastapi_app.include_router(thread_runs.router)
    fastapi_app.include_router(thread_runs.runs_router)
    fastapi_app.include_router(threads.router)
    return fastapi_app


def make_request(fastapi_app: FastAPI) -> SimpleNamespace:
    """造一个假请求。deps.py 的 getter 只需要 `.app` 这一个属性。"""
    return SimpleNamespace(app=fastapi_app, headers={})


def make_body(text: str = "查一下销售额", stream_mode=None, user_id: str | None = None):
    """造一个合法的请求体。stream_mode 不传 = 用服务端默认值。"""
    from app.gateway.run_models import RunCreateRequest

    return RunCreateRequest(
        input={"messages": [{"role": "user", "content": text}]},
        config={"configurable": {"model_name": "deepseek-chat"}},
        stream_mode=stream_mode,
        user_id=user_id,
    )
