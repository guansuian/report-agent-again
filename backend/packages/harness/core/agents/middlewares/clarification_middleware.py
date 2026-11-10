"""澄清中间件 —— 把模型的「提问」翻译成「本轮结束 + 一条问题消息」。

对应真实 deer-flow 的 deerflow/agents/middlewares/clarification_middleware.py（最小版）。

它做两件事：

    ① after_model       —— 模型同一轮里既调了 ask_clarification、又调了别的工具？
                           把别的工具从 AIMessage 里删掉，免得它们在用户回答前就执行了。
    ② wrap_tool_call    —— 截住 ask_clarification，不让工具真的执行；
                           自己造一条 ToolMessage（内容是问题），塞进 state，
                           然后 goto=END 结束本轮。

为什么这样能跑通：

    LangChain / OpenAI 的协议要求「每个 tool_call 后面必须跟一条 tool 结果」。
    我们把问题伪装成工具结果写进历史，协议就满足了。用户下一轮发消息时，
    模型看到的是：

        Human: 帮我做个报表
        AI:    tool_calls=[ask_clarification]
        Tool:  "按哪个维度拆分？1. 地区 2. 产品"
        Human: 地区

    它自然就接着答了，知道自己问过什么、用户答了什么。

和 LangGraph 的 interrupt() 的区别：那不是「挂起进程」，只是「结束这一轮」。
好处是状态全在 checkpointer 里 —— 刷新页面、换浏览器、重启服务器都还在。
代价是 run 的状态是 success 而不是 paused，语义上要靠 tool 消息自己表达。

为什么必须排在中间件链的最后：
    它要贴着工具层截获调用。排前面的话，外面的中间件会先看到 tool_call，
    可能已经做了别的事（比如把工具真跑了）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

logger = logging.getLogger(__name__)

ASK_CLARIFICATION_TOOL_NAME = "ask_clarification"

# 表单字段类型白名单；模型传了别的类型就降级成 text，不会画出渲染不了的卡片。
# 最小版省掉 date（前端暂时没这个控件需求）。
FORM_FIELD_TYPES = frozenset({"text", "textarea", "number", "select", "multi_select", "checkbox"})
# 需要带 options 的字段类型：select/multi_select 没有选项就降级成 text。
_OPTION_FIELD_TYPES = frozenset({"select", "multi_select"})

# 字段名不能撞 JS Object.prototype 的成员（前端用普通对象按字段名存值，撞了会读到原型属性）。
_RESERVED_FIELD_NAMES = frozenset({
    "__proto__", "constructor", "prototype", "toString", "toLocaleString",
    "valueOf", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable",
    "__defineGetter__", "__defineSetter__", "__lookupGetter__", "__lookupSetter__",
})

# 上限：模型发疯也不能发一张无边无际的表单。超了整体降级成纯文本问题。
MAX_FORM_FIELDS = 16
MAX_FIELD_OPTIONS = 24
MAX_FIELD_TEXT_CHARS = 200

# options 最多留几个：再多前端按钮排不下，模型也很少给得这么细
MAX_OPTIONS = 10

# 模型偶尔会传空的 question。空 content 的 ToolMessage 会让下一轮请求很难看，
# 这里兜一句话。
_FALLBACK_QUESTION = "请补充更多信息后再继续。"


def _raw_tool_call_id(raw: Any) -> str | None:
    """从 OpenAI 原始 tool_call 结构里取 id（形如 {"id": "call_x", "type": "function", ...}）。"""
    if isinstance(raw, dict):
        raw_id = raw.get("id")
        if isinstance(raw_id, str) and raw_id:
            return raw_id
    return None


def _clone_with_tool_calls(message: AIMessage, tool_calls: list[dict[str, Any]]) -> AIMessage:
    """复制一条 AIMessage，只保留指定的 tool_calls。

    为什么不能只改 message.tool_calls：
        模型返回的原始 tool_call 同时存在于三处 ——
            tool_calls                         LangChain 归一化后的结构
            additional_kwargs["tool_calls"]    OpenAI 原始结构
            content 里的 tool_use 块           Anthropic 风格的块
        只改一处，下一轮把历史发回 provider 时，它会看到一个「没有对应结果的
        工具调用」，直接报 400。所以能删的地方必须一起删。

    DeepSeek / OpenAI 兼容接口的 content 是字符串，第 ③ 步不会生效；
    先把逻辑写全，以后换 Anthropic 不用再回来改。
    """
    kept_ids = {tc["id"] for tc in tool_calls if isinstance(tc.get("id"), str) and tc["id"]}

    update: dict[str, Any] = {"tool_calls": tool_calls}

    # ① content 里的工具块
    if isinstance(message.content, list):
        update["content"] = [
            block
            for block in message.content
            if not (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("id") not in kept_ids
            )
        ]

    # ② OpenAI 原始结构
    additional_kwargs = dict(message.additional_kwargs or {})
    raw_calls = additional_kwargs.get("tool_calls")
    if isinstance(raw_calls, list):
        kept_raw = [raw for raw in raw_calls if _raw_tool_call_id(raw) in kept_ids]
        if kept_raw:
            additional_kwargs["tool_calls"] = kept_raw
        else:
            additional_kwargs.pop("tool_calls", None)
    update["additional_kwargs"] = additional_kwargs

    return message.model_copy(update=update)


class ClarificationMiddleware(AgentMiddleware[AgentState]):
    """截获 ask_clarification，把问题变成一条 ToolMessage 并结束本轮。

    只碰一个钩子：wrap_tool_call / awrap_tool_call。别的工具原样放行。
    """

    # ------------------------------------------------------------------
    # 三个小工具：造 id、造给模型看的文本、造给前端看的 payload
    # ------------------------------------------------------------------

    def _request_id(self, tool_call_id: str, question: str) -> str:
        """给这次提问一个稳定的 id。

        它同时用作 ToolMessage 的 message id。同一个提问重试时 id 不变，
        add_messages 会替换而不是追加，不会冒出两张卡片。

        有 tool_call_id 就用它（模型每次调用都自带）；没有就退化成问题文本的哈希。
        """
        if tool_call_id:
            return f"clarification:{tool_call_id}"
        return f"clarification:{sha256(question.encode('utf-8')).hexdigest()[:16]}"

    def _normalize_options(self, raw_options: Any) -> list[str]:
        """把模型给的候选项归一成字符串列表：非 list 包一层、去空白、去空、去重。"""
        if raw_options is None:
            return []
        if not isinstance(raw_options, list):
            raw_options = [raw_options]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_options:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    @staticmethod
    def _normalize_bool(raw: Any) -> bool:
        """模型可能把布尔传成字符串 / 1/0，统一成 bool。"""
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str):
            return raw.strip().lower() == "true"
        return False

    def _normalize_fields(self, raw_fields: Any) -> list[dict[str, Any]]:
        """把模型给的字段归一成合法字段列表；任何一个字段结构坏了，整个表单作废。

        原子降级：name / label / type 校验不过、超上限、名字重复或撞 JS 原型名，
        都返回空列表 → 调用方降级成纯文本问题，绝不画一张「看着完整但缺字段」的表单。
        """
        if not isinstance(raw_fields, list):
            return []
        if len(raw_fields) > MAX_FORM_FIELDS:
            return []

        normalized: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for entry in raw_fields:
            if not isinstance(entry, dict):
                return []
            raw_name = entry.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                return []
            name = raw_name.strip()
            if name in _RESERVED_FIELD_NAMES or name in seen_names or len(name) > MAX_FIELD_TEXT_CHARS:
                return []
            seen_names.add(name)

            raw_label = entry.get("label")
            label = raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else name
            if len(label) > MAX_FIELD_TEXT_CHARS:
                return []

            field_type = entry.get("type")
            if not isinstance(field_type, str) or field_type not in FORM_FIELD_TYPES:
                field_type = "text"

            options = self._normalize_options(entry.get("options")) if field_type in _OPTION_FIELD_TYPES else []
            if len(options) > MAX_FIELD_OPTIONS or any(len(option) > MAX_FIELD_TEXT_CHARS for option in options):
                return []
            if field_type in _OPTION_FIELD_TYPES and not options:
                field_type = "text"

            field: dict[str, Any] = {
                "name": name,
                "label": label,
                "type": field_type,
                "required": self._normalize_bool(entry.get("required")),
            }
            if field_type in _OPTION_FIELD_TYPES:
                field["options"] = [
                    {"id": f"{name}-option-{index}", "label": option, "value": option}
                    for index, option in enumerate(options, 1)
                ]
            placeholder = entry.get("placeholder")
            if isinstance(placeholder, str) and placeholder.strip():
                if len(placeholder.strip()) > MAX_FIELD_TEXT_CHARS:
                    return []
                field["placeholder"] = placeholder.strip()
            normalized.append(field)
        return normalized

    def _render_text(self, args: dict[str, Any], options: list[str], fields: list[dict[str, Any]] | None = None) -> str:
        """拼出 ToolMessage.content —— 这段文字是**给模型看**的。

        下一轮模型就在历史里读到它，知道自己问过什么。前端读的是 artifact，
        两者服务不同读者，所以分开写。有 fields 时优先用字段列表（表单），
        没有字段才轮到 options（按钮）。
        """
        parts = [str(args.get("question") or "")]
        if args.get("context"):
            parts.append(f"（{args['context']}）")
        if fields:
            parts.append("")
            for index, field in enumerate(fields, 1):
                line = f"{index}. {field['label']}"
                if field["required"]:
                    line += "（必填）"
                field_options = field.get("options")
                if field_options:
                    line += "——可选项：" + " / ".join(option["label"] for option in field_options)
                    if field["type"] == "multi_select":
                        line += "（可多选）"
                parts.append(line)
            parts.append("")
            parts.append("请按每个字段填写后回复。")
        elif options:
            parts.append("")
            parts.extend(f"{index}. {option}" for index, option in enumerate(options, 1))
        return "\n".join(parts).strip()

    def _build_payload(
        self,
        args: dict[str, Any],
        *,
        request_id: str,
        options: list[str],
        fields: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """造给前端画卡片用的结构化数据，挂在 ToolMessage.artifact 里。

        key 的含义：
            version              协议版本，1 = 纯文本 / 选项，2 = 表单
            kind                 固定 "human_input_request"，前端据此认出这是一次提问
            source               谁问的，固定 "ask_clarification"
            request_id           这条请求的身份证，用户作答时回填同一个值
            clarification_type   问题类型，前端可据此换图标 / 标题
            question             问题原文
            input_mode           前端该画什么控件：form（表单）/ choice_with_other（按钮+输入框）/ free_text（只输入框）
            context/options/fields  可选字段，有才带；fields 优先于 options
        """
        if fields:
            version, input_mode = 2, "form"
        elif options:
            version, input_mode = 1, "choice_with_other"
        else:
            version, input_mode = 1, "free_text"

        payload: dict[str, Any] = {
            "version": version,
            "kind": "human_input_request",
            "source": ASK_CLARIFICATION_TOOL_NAME,
            "request_id": request_id,
            "clarification_type": str(args.get("clarification_type") or "missing_info"),
            "question": str(args.get("question") or ""),
            "input_mode": input_mode,
        }
        if args.get("context"):
            payload["context"] = str(args["context"])
        if input_mode == "form":
            payload["fields"] = fields
        elif options:
            payload["options"] = [
                {"id": f"option-{index}", "label": option, "value": option}
                for index, option in enumerate(options, 1)
            ]
        return payload

    # ------------------------------------------------------------------
    # 核心：截获 → 造 ToolMessage → 结束本轮
    # ------------------------------------------------------------------

    def _handle(self, request: ToolCallRequest) -> Command:
        """不经 handler，直接返回 Command。

        Command 的两个 key：
            update  —— dict，key "messages" 是 state 里的频道名，
                       value 是要并进去的消息列表（走 add_messages 规则）
            goto    —— 下一步去哪个节点，END 表示本轮到此为止、不回模型
        """
        args = request.tool_call.get("args") or {}
        # 模型可能把 args 传成非 dict（极少见），防一下，免得下面 .get 报错
        if not isinstance(args, dict):
            args = {}

        # options / fields 都防一下：模型可能传 "地区" 这种字符串或畸形的字段
        options = self._normalize_options(args.get("options"))[:MAX_OPTIONS]
        fields = self._normalize_fields(args.get("fields"))

        tool_call_id = str(request.tool_call.get("id") or "")
        question = str(args.get("question") or "").strip() or _FALLBACK_QUESTION
        args = {**args, "question": question}   # 让 content 和 payload 用同一个问题

        request_id = self._request_id(tool_call_id, question)
        logger.info("截获澄清请求: %s", question)

        message = ToolMessage(
            id=request_id,
            content=self._render_text(args, options, fields),
            tool_call_id=tool_call_id,
            name=ASK_CLARIFICATION_TOOL_NAME,
            artifact={"human_input": self._build_payload(args, request_id=request_id, options=options, fields=fields)},
        )
        # 在这里把自己造出来的ToolMessage写入到State中
        return Command(update={"messages": [message]}, goto=END)

    # ------------------------------------------------------------------
    # 同轮清理：模型一次输出里可能既有提问、又有别的工具调用
    # ------------------------------------------------------------------

    def _drop_sibling_tool_calls(self, state: AgentState) -> dict[str, Any] | None:
        """把和 ask_clarification 同轮的其它工具调用从 AIMessage 里删掉。

        为什么必须删：
            模型常常一次输出好几个 tool_call。如果 [ask_clarification, query_report]
            一起进 tools 节点，query_report 会在用户回答之前就执行 —— 用户还没说
            维度，查出来的东西是白查，而且模型这一轮还要结束等用户。
            更麻烦的是 LangChain 的 return_direct 路由：只有当最后一条 AIMessage
            的所有 tool_call 都是 return_direct 时才走向 END，混进一个普通工具，
            图会绕回模型，Command(goto=END) 的语义就乱了。

        为什么改 AIMessage 而不是拦工具：
            拦工具只能拦住执行，拦不住「这一轮继续跑」。删调用才能让这一轮
            干净地以提问收尾。
        """
        messages = state.get("messages") or []
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None

        tool_calls = list(last.tool_calls or [])
        clarification_calls = [tc for tc in tool_calls if tc.get("name") == ASK_CLARIFICATION_TOOL_NAME]
        if not clarification_calls:
            return None

        siblings = [tc for tc in tool_calls if tc.get("name") != ASK_CLARIFICATION_TOOL_NAME]
        if not siblings:
            return None

        logger.warning(
            "ask_clarification 和 %d 个其它工具出现在同一轮，丢弃 %s，只保留提问",
            len(siblings),
            [tc.get("name") for tc in siblings],
        )
        return {"messages": [_clone_with_tool_calls(last, clarification_calls)]}

    # ------------------------------------------------------------------
    # 钩子：只拦名字是 ask_clarification 的调用
    # ------------------------------------------------------------------

    def after_model(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """模型输出后、工具执行前。清理同轮的兄弟工具调用。"""
        return self._drop_sibling_tool_calls(state)

    async def aafter_model(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """异步版 —— astream 走的是这条。"""
        return self._drop_sibling_tool_calls(state)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步版。我们的链路走异步，但 LangChain 某些代码路径会走同步，两个都实现最稳。"""
        if request.tool_call.get("name") != ASK_CLARIFICATION_TOOL_NAME:
            return handler(request)      # 别的工具：原样放行
        return self._handle(request)     # 这个工具：按住，不让它执行

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步版 —— astream 走的是这条。"""
        if request.tool_call.get("name") != ASK_CLARIFICATION_TOOL_NAME:
            return await handler(request)
        return self._handle(request)
