"""运行时全链路演示 —— 把 worker 主线真正跑一次。

零件的单测都有了，但 run_agent 还没有调用方。这个脚本负责组装：

    登记 run → 起消费者订阅 → run_agent 跑图 → 打印终态 / 落库 / 事件流

用法（在 backend/packages/harness 目录下）：

    python -m core.runtime.demo_run          # 假 agent，不调模型、不花钱
    python -m core.runtime.demo_run --real   # 真调 make_lead_agent + DeepSeek

和 core/agents/lead_agent/demo.py 同一角色：只做演示，不属于运行时库本身。
"""

from __future__ import annotations

import argparse
import asyncio

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from core.runtime.events.store.memory import MemoryRunEventStore
from core.runtime.runs.manager import RunManager
from core.runtime.runs.worker import RunContext, run_agent
from core.runtime.stream_bridge.memory import MemoryStreamBridge

RUN_ID = "r1"
THREAD_ID = "t1"


class _FakeAgent:
    """不调模型的假 agent：吐 1 个 chunk，模拟 LangGraph 的 values 流。"""

    ANSWER = "本月销售额 123 万"

    def __init__(self) -> None:
        self.checkpointer = None

    async def astream(self, graph_input, config=None, stream_mode="values"):
        # 模拟 LangGraph 调用 config 里挂的 callback（journal 靠它抓消息落库）：
        # 1) 模型调用开始 → journal 记下用户消息
        # 2) 模型调用结束 → journal 记下 AI 回答
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
        yield {"messages": [AIMessage(content=self.ANSWER)]}


async def _consume(bridge: MemoryStreamBridge, run_id: str) -> None:
    """模拟前端 SSE：边跑边打印事件，收到结束哨兵就退出。"""
    async for event in bridge.subscribe(run_id):
        if event.event == "__end__":
            print(f"[event] {event.id:>2} {event.event}")
            break
        data = repr(event.data)
        # if len(data) > 100:
        #     data = data[:100] + "..."
        print(f"[event] {event.id:>2} {event.event} {data}")


def _make_agent_factory(real: bool):
    """返回 run_agent 需要的异步工厂。

    默认用假 agent，先确认主线接通；--real 才真建图、真调模型。
    """

    if real:
        from core.agents.lead_agent.agent import make_lead_agent

        async def factory(*, config):
            # make_lead_agent 是同步函数；run_agent 里要 await，所以包一层 async
            return make_lead_agent(config)

        return factory

    async def factory(*, config):
        return _FakeAgent()

    return factory


async def main(real: bool = False) -> None:
    # ① 三个进程级单例（真实项目里在 lifespan 建一次）
    bridge = MemoryStreamBridge()   # 生产者写、消费者读的事件出口
    manager = RunManager()          # run 登记处
    event_store = MemoryRunEventStore()  # 消息落库

    # ② 登记一个 run：此时状态是 pending
    record = await manager.create_run(RUN_ID, THREAD_ID)
    print("== 1) 登记 run ==")
    print(f"{record.run_id} status = {record.status.value}")

    # ③ 基础设施依赖包；最小版没有 checkpointer
    ctx = RunContext(checkpointer=None, event_store=event_store)

    # ④ 先起消费者，否则 run_agent 发事件时没人接
    print("\n== 2) 消费端订阅中 ==")
    consumer = asyncio.create_task(_consume(bridge, RUN_ID))

    # ⑤ 跑主线：pending → running → success
    await run_agent(
        bridge,
        manager,
        record,
        ctx=ctx,
        agent_factory=_make_agent_factory(real),
        graph_input={"messages": [("user", "查一下销售额")]},
        config={
            "configurable": {"model_name": "deepseek-flash"},
            "context": {},
        },
    )
    await consumer

    # ⑥ 验收：终态 + 落库消息
    print("\n== 3) 生产端结束 ==")
    print(f"final status = {record.status.value}")

    print("\n== 4) 落库校验 ==")
    messages = await event_store.list_messages(THREAD_ID)
    for message in messages:
        # content 存的是 message.model_dump()：AI 回答在 "content"，
        # 工具调用在 "tool_calls"，工具结果在 "content"。
        body = message["content"]
        if isinstance(body, dict):
            text = body.get("content") or body.get("tool_calls") or body
        else:
            text = body
        print(f"[{message['event_type']}] {text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行时全链路演示")
    parser.add_argument(
        "--real",
        action="store_true",
        help="真调 make_lead_agent + DeepSeek（需要 DEEPSEEK_API_KEY）",
    )
    args = parser.parse_args()
    asyncio.run(main(real=args.real))
