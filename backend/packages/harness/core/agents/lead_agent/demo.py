"""运行演示 —— 走通「配置 → 选模型 → 建模型 → 收集工具 → 组装图 → invoke」主链路。

用法（在 backend/packages/harness 目录下）：
    python -m core.agents.lead_agent.demo

注意：真正调用模型需要 DEEPSEEK_API_KEY（config.yaml 里写的是 $DEEPSEEK_API_KEY 引用）。
app_config 模块级已 load_dotenv 读取项目根目录 .env，所以不用再手动 export key。
"""

from __future__ import annotations

import logging

from core.agents.lead_agent.agent import make_lead_agent

# 让中间件里的 logger.info（如 token 用量）打印到控制台
logging.basicConfig(level=logging.INFO)


def run() -> None:
    # 模拟 LangGraph Server 传入的 RunnableConfig
    config = {
        "configurable": {"model_name": "deepseek-flash"},
        "context": {},
    }

    print("== 1) make_lead_agent 拿到编译好的图 ==")
    graph = make_lead_agent(config)
    print("返回类型:", type(graph).__name__)

    print("\n== 2) 模拟运行一次 ==")
    result = graph.invoke({"messages": [("user", "你好，帮我查一下销售额")]})
    print("回复:", result["messages"][-1].content)


if __name__ == "__main__":
    run()
