# core —— deer-flow 后端的简化教学版

公司接口客户端的配置、Token 传递和工具调用方式，见 [clients 使用说明](core/clients/README.md)。

这个包是 [deer-flow](https://github.com/bytedance/deer-flow) 后端 harness 包
（`deerflow-harness`）的**简化教学镜像**，目的是帮你**看懂 deer-flow 是怎么组织的**。

- 真实包名：`deerflow`（位于 `deer-flow/backend/packages/harness/deerflow/`）
- 这里的包名：`core`（位于本目录 `core/`）
- 原则：**完整还原「lead agent 装配」主链路的结构；复杂组件用注释/空函数占位；不装任何第三方依赖。**

---

## 一、一句话理解 deer-flow

deer-flow 是一个基于 LangGraph 的多智能体（agent）系统。它最核心的一步是：
**把一次运行请求，组装成一个可运行的 LangGraph 图（graph）。**
这个「组装」就是 `lead_agent/agent.py` 干的事，也是本简化版还原的重点。

## 二、三层工厂结构（最重要的一条线）

deer-flow 用**三层工厂**来创建 agent，从底到顶：

| 层级 | 真实文件 | 简化版 | 说明 |
|---|---|---|---|
| 底层原语 | `langchain.agents.create_agent` | `core/agents/langgraph_compat.py` | LangChain 提供，编译出图 |
| SDK 层 | `deerflow/agents/factory.py:create_deerflow_agent` | `core/agents/factory.py` | 纯参数，不读配置 |
| 应用层 | `deerflow/agents/lead_agent/agent.py:make_lead_agent` | `core/agents/lead_agent/agent.py` | 读 config，完整装配 |

入口在 `backend/langgraph.json`：
```json
{ "graphs": { "lead_agent": "core.agents:make_lead_agent" } }
```
LangGraph Server 启动时会解析出 `core.agents` 模块里的 `make_lead_agent` 函数。

## 三、装配主流程（对着 `core/agents/lead_agent/agent.py` 看）

```
make_lead_agent(config)
  └─> assemble_lead_agent(config)          # 冻结 checkpoint 模式（真实实现有）
        └─> _assemble_lead_agent(config, app_config)
              1. 读运行时 config            —— model_name / is_plan_mode / subagent_enabled ...
              2. 解析模型名                  —— 请求 > agent 配置 > 全局默认
              3. 模型授权                    —— model:use，被拒则降级
              4. 创建模型实例                —— core/models/factory.py:create_chat_model
              5. 收集工具                    —— core/tools/tools.py:get_available_tools
              6. 工具授权                    —— core/authz/tool_filter.py:apply_tool_authorization
              7. 构建中间件链                —— agent.py:build_middlewares
              8. 渲染系统提示词              —— prompt.py:apply_prompt_template
              9. 编译成图                    —— create_agent(model, tools, middleware, prompt)
             10. 打包返回                    —— LeadAgentAssembly(graph, descriptor)
```

## 四、目录结构对照表

| 简化版路径 | 真实 deer-flow | 职责 |
|---|---|---|
| `core/agents/lead_agent/agent.py` | `deerflow/agents/lead_agent/agent.py` | **主装配工厂（核心）** |
| `core/agents/lead_agent/prompt.py` | `deerflow/agents/lead_agent/prompt.py` | 系统提示词渲染 |
| `core/agents/factory.py` | `deerflow/agents/factory.py` | SDK 层纯参数工厂 |
| `core/agents/features.py` | `deerflow/agents/features.py` | RuntimeFeatures 特性开关 |
| `core/agents/middlewares/` | `deerflow/agents/middlewares/` | 中间件链（约 50 个） |
| `core/agents/memory/` | `deerflow/agents/memory/` | 长期记忆 |
| `core/agents/thread_state.py` | `deerflow/agents/thread_state.py` | 图状态 schema |
| `core/models/` | `deerflow/models/` | 模型工厂 + provider 适配 |
| `core/tools/` | `deerflow/tools/` | 工具收集/注册 |
| `core/authz/` | `deerflow/authz/` | 模型/工具/技能三层授权 |
| `core/config/` | `deerflow/config/` | 配置（AppConfig 及几十个 dataclass） |
| `core/tracing/` | `deerflow/tracing/` | Langfuse/LangSmith 追踪 |
| `core/runtime/` | `deerflow/runtime/` | checkpoint / run / 事件流 |
| `core/persistence/` | `deerflow/persistence/` | 数据库层 |
| `core/skills/` | `deerflow/skills/` | 技能系统 |
| `core/subagents/` | `deerflow/subagents/` | 子 agent |
| `core/sandbox/` | `deerflow/sandbox/` | 沙箱执行 |
| `core/mcp/` | `deerflow/mcp/` | MCP 集成 |
| `core/extensions/` | `deerflow/extensions/` | 插件扩展机制 |
| `core/knowledge/` `projects/` `scheduler/` `guardrails/` `uploads/` `workspace_changes/` `integrations/` `community/` | 同名 | 见各 `__init__.py` 说明 |

## 五、跑一下 demo（纯标准库，无需装依赖）

```bash
cd backend/packages/harness
python -m core.agents.lead_agent.demo
```

会打印出整个装配的结果（模型、工具、中间件链、系统提示词），再模拟运行一次。

## 六、值得重点学习的几个设计（对应真实代码）

1. **INVARIANT 注释**（`agent.py` 文件头）——把「容易踩的坑 + 为什么 + 违反了会怎样」写清楚。
   真实 deer-flow 里最典型的是 tracing callback 必须挂在 graph 根节点，因此内部所有
   `create_chat_model` 都要传 `attach_tracing=False`。

2. **优先级解析**（`_resolve_runtime_option`）——`请求 > agent 配置 > 默认值`，
   并用 `key in cfg` 区分「没传」和「传了 false」。

3. **授权降级**（`_authorize_model_name` / `apply_tool_authorization`）——
   被拒时降级到可用项，而非直接报错打断运行（fail-open / fail-closed 可配）。

4. **中间件顺序即语义**（`build_middlewares`）——顺序错了行为就错，所以注释里写清
   每个中间件为什么排在这个位置。

5. **懒加载避免循环依赖**——`from ... import ...` 大量写在函数内部而非文件顶部。
