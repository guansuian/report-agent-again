"""长期记忆入口，参考 DeerFlow 的 Mem0Manager，改用 mem0 OSS Python 包。

中间件决定什么时候调用、提交哪些消息；这里只调用 mem0 并整理搜索结果。
用户 ID 每次调用时传入，不保存在共用的 Manager 对象中。
"""

from pathlib import Path

from mem0 import AsyncMemory

from core.config.memory_config import MemoryConfig


class MemoryManager:
    """提供保存对话、搜索记忆、列出记忆和关闭资源四个操作。"""

    def __init__(self, config: MemoryConfig) -> None:
        # 关闭时不创建 mem0，避免空配置触发它默认的模型和存储。
        if not config.enabled:
            raise ValueError("长期记忆未启用，不应创建 MemoryManager。")

        self._config = config

        # 路径相对 harness 目录，避免从不同工作目录启动时找不到提示词。
        harness_dir = Path(__file__).resolve().parents[3]
        instructions_path = harness_dir / config.custom_instructions_file
        instructions = instructions_path.read_text(encoding="utf-8").strip()
        if not instructions:
            raise ValueError(f"长期记忆提炼规则不能为空：{instructions_path}")

        # mem0 接收正文，不接收文件路径；复制配置，避免修改原配置对象。
        backend_config = dict(config.backend_config)
        backend_config["custom_instructions"] = instructions
        self._memory = AsyncMemory.from_config(backend_config)

    async def add(self, user_id: str, messages: list[dict[str, str]]) -> None:
        """保存该用户的对话；调用方先整理为 role/content 格式并排除内部消息。

        infer=True 表示让 mem0 按自定义提炼规则生成记忆，不原样存整段对话。
        调用失败时直接抛出异常，由调用方决定如何处理。
        """
        await self._memory.add(
            messages=messages,
            user_id=user_id,
            infer=True,
        )

    async def get_context(self, user_id: str, query: str) -> str:
        """搜索当前用户的相关记忆，返回可以注入模型的文字。

        只按用户限定范围，不限定 thread_id，因此能读取其他对话留下的记忆。
        """
        result = await self._memory.search(
            query=query,
            filters={"user_id": user_id},
            top_k=self._config.top_k,
        )

        # 每条记忆占一行；保留完整条目，放不下的条目跳过，不截断句子。
        lines: list[str] = []
        used_chars = 0
        for record in result["results"]:
            text = record["memory"].strip()
            if not text:
                continue

            line = f"- {text}"
            added_chars = len(line)
            if lines:
                added_chars += 1  # 两行之间的换行符也占一个字符。
            if used_chars + added_chars > self._config.max_injection_chars:
                continue

            lines.append(line)
            used_chars += added_chars

        context_text = "\n".join(lines)
        return context_text

    async def list_memories(self, user_id: str) -> dict:
        """供用户管理页面读取该用户的记忆，最多返回 100 条。

        与 get_context 的相关性搜索不同，这里直接列出记忆，
        不调用大模型或嵌入模型；读取失败时直接抛出异常。
        """
        result = await self._memory.get_all(
            filters={"user_id": user_id},
            top_k=100,
        )

        # 只返回页面展示需要的字段，不直接暴露 mem0 的其他元数据。
        memories: list[dict] = []
        for record in result["results"]:
            memory = {
                "id": record["id"],
                "memory": record["memory"],
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
            }
            memories.append(memory)

        return {"results": memories}

    def close(self) -> None:
        """服务关闭时，调用 mem0 提供的资源关闭方法。"""
        self._memory.close()
