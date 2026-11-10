"""run 的记录 / 管理器 —— 对应 deer-flow 的
deerflow/runtime/runs/manager.py（最小版）。

deer-flow 把 RunRecord 和 RunManager 放在同一个文件里：manager 负责创建 record、
改状态、把状态落库。这里 RunRecord 最小字段已就位，RunManager 负责「内存注册表
+ 落库两件事」；准入控制（create_or_reject）、lease 等还属后面复杂化的范围。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from core.runtime.runs.schemas import RunStatus
from core.runtime.runs.store import MemoryRunStore, RunStore

logger = logging.getLogger(__name__)


class RunStartOutcome(StrEnum):
    """pending → running 启动闸门的结果。"""

    started = "started"      # 成功从 pending 切到 running，可以开始跑
    cancelled = "cancelled"  # 已经不是 pending（被取消/已开始），不该继续跑


class RunStartupError(RuntimeError):
    """启动阶段无法安全推进时抛出（例如 run_id 根本不存在）。"""


@dataclass
class RunRecord:
    """一次 run 的运行时记录。

    **同一个 run 有两份拷贝，分工不同：**

        内存（本类，存在 RunManager._runs）
            给「正在跑」的 run 用：读得快，能挂 asyncio.Task（取消 run 要用），
            但服务器重启就没了。
        数据库（RunRow，经 RunStore 存取）
            给「跑过的」run 用：重启后还能查到、多进程能共享，
            但存不了 asyncio.Task 这类进程内句柄。

    所以除了 task，status / error / metadata 这些字段两边都有 ——
    不是漏了去重，而是两边都得能回答「这个 run 现在怎么样了」。
    同步由 RunManager 负责：创建时先写库再写内存，改状态时先改内存再写库；
    写入方只有 RunManager 一个，业务代码不许直接碰 store。

    这条「两份拷贝」的设计照搬 deer-flow（那边的 _runs 还多挂 start_lock /
    abort_event 等句柄，并且 get() 在内存查不到时会从库里回填）。

    目前只放 run_agent 主线和 RunManager.set_status 真正用到的字段，其余
    等需要时再补（deer-flow 的完整版还有 on_disconnect、operation_kind、
    token 统计、start_lock、abort_event、ownership_lost 等，都属后面复杂化的范围）。
    """

    run_id: str
    thread_id: str
    user_id: str | None = None                     # 归属用户（可空；用于按人过滤）
    status: RunStatus = RunStatus.pending          # 生命周期状态，RunManager 会改它
    error: str | None = None                       # 出错时的描述，success 时为 None
    metadata: dict[str, Any] = field(default_factory=dict)  # 前端需要的附加信息

    # 后台正在跑这次 run 的 asyncio.Task：services.start_run 用 create_task 塞进来。
    # 挂在 record 上是为了「别被垃圾回收」，以后要取消 run 也能直接 record.task.cancel()。
    # repr=False：打印 record 时不显示任务对象，输出干净。
    # 这是**唯一**进不了数据库的字段（RunRow 没有对应的列），也是保留 _runs 的主要理由。
    task: asyncio.Task | None = field(default=None, repr=False)


class RunManager:
    """run 注册表 —— 对应 deer-flow 的 RunManager（最小版）。

    同一份数据存两份：
        self._runs      进程内存，业务代码快速读（重启就丢）
        self._store     持久化，重启后还能查到（postgres 或内存版）

    所有内存变更都过一把 asyncio.Lock（和 deer-flow 一样「所有 mutation 都受锁保护」）。
    最小版三件事：登记 run、查 run、改状态；每次改都同步写一份到 store。
    """

    def __init__(self, store: RunStore | None = None) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()
        # 落库用。不传就退化成内存版（demo_run.py、单元测试走这条）。
        self._store: RunStore = store or MemoryRunStore()

    async def recover_orphans(self, *, error: str = "服务重启前的 run 已中断") -> int:
        """启动清账：把库里还挂着 pending / running 的 run 标成 error。

        为什么安全：这个方法只在 lifespan 启动时调用一次。此刻 self._runs 一定是
        空的，库里所有「进行中」的记录都来自上次已经死掉的进程 —— 没人会再把它
        跑完，不清掉的话前端会永远显示「正在生成中」。

        对应 deer-flow 的 reconcile_orphaned_inflight_runs（那边靠租约过期判断，
        我们单进程，启动这一刻就够了）。

        失败只记日志：清账失败不该让服务器起不来。
        """
        try:
            count = await self._store.fail_inflight(error)
        except Exception:
            logger.warning("启动清账失败（不影响服务启动）", exc_info=True)
            return 0
        if count:
            logger.warning("启动清账：%d 个孤儿 run 已标记为 error", count)
        return count

    async def create_run(
        self,
        run_id: str,
        thread_id: str,
        *,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        """登记一个新的 run，返回 record。

        对应 deer-flow 的 create_or_reject（准入控制 + 落库），最小版只做落库和登记，
        不判「同一 thread 是否已有 run 在跑」。

        顺序很重要：**先落库，再登记内存**。
        反过来的话，落库失败时内存里会留下一个「重启后就凭空消失」的 run，
        调用方却以为创建成功了。对应 deer-flow manager.py:1042-1049。
        """
        record = RunRecord(
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            status=RunStatus.pending,
            metadata=metadata or {},
        )
        try:
            await self._store.put(
                record.run_id,
                thread_id=record.thread_id,
                user_id=record.user_id,
                status=record.status,
                error=record.error,
                metadata=record.metadata,
            )
        except Exception:
            # 落库失败 = 创建失败：不登记内存，把异常抛给调用方
            logger.warning("run %s 落库失败，未登记", run_id, exc_info=True)
            raise
        async with self._lock:
            self._runs[run_id] = record
        return record

    def get(self, run_id: str) -> RunRecord | None:
        """按 id 查**内存里活着的** record；查不到返回 None。

        只读，单线程事件循环下 dict.get 是原子的，不需要锁。
        注意：只查内存，重启后的历史 run 查不到 —— 那是 fetch() 的事。
        """
        return self._runs.get(run_id)

    async def fetch(self, run_id: str, *, user_id: str | None = None) -> dict | None:
        """给接口用的查询：按 id 查一条 run，返回记录 dict；查不到返回 None。

        数据来源各取所长：
            时间戳等字段      ← store（内存的 RunRecord 里没有这些）
            status/error/metadata ← 内存优先（内存是最新真相，库里可能慢一次写入）

        user_id 不为 None 时按归属过滤（对不上返回 None，HTTP 层据此报 404）。

        对应 deer-flow 的 async get()（先查内存、没有再回库），最小版统一
        返回 dict，接口直接就能 JSON 序列化。
        """
        row = await self._store.get(run_id)
        if row is None:
            return None
        live = self._runs.get(run_id)
        if live is not None:
            row["status"] = str(live.status)
            row["error"] = live.error
            row["metadata"] = dict(live.metadata)
            row["user_id"] = live.user_id
        if user_id is not None and row.get("user_id") != user_id:
            return None
        return row

    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """按会话查跑过的 run，最新的在前。

        user_id 不为 None 时只返回属于该用户的 run。
        """
        return await self._store.list_by_thread(
            thread_id, user_id=user_id, limit=limit
        )

    async def _persist_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str | None = None,
    ) -> None:
        """把状态写进 store。

        和 create_run 不同：这里失败**只记日志，不抛异常**。
        因为 run 已经跑起来了，数据库抖一下不该把正在跑的任务带崩；
        内存里状态是对的，业务继续走，库里那份等下次状态变更再补。
        """
        try:
            await self._store.update_status(run_id, status, error=error)
        except Exception:
            logger.warning("run %s 状态落库失败（内存已更新）", run_id, exc_info=True)

    async def try_start(self, run_id: str) -> RunStartOutcome:
        """跑图前把 pending 的 run 切成 running —— 对应 deer-flow 的 try_start。

        deer-flow 里还做 lease 校验；最小版只做：
        - run 不存在 → 抛 RunStartupError（调用方有 bug，不该静默）
        - 已经不是 pending（被取消 / 已经跑过）→ 返回 cancelled，调用方不要继续跑
        - 正常 → 改成 running 并落库，返回 started
        """
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise RunStartupError(f"Cannot start unknown run {run_id}")
            if record.status != RunStatus.pending:
                return RunStartOutcome.cancelled
            record.status = RunStatus.running
        await self._persist_status(run_id, RunStatus.running)
        logger.info("Run %s -> %s", run_id, RunStatus.running.value)
        return RunStartOutcome.started

    async def set_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str | None = None,
    ) -> None:
        """把 run 改成新状态。

        对应 deer-flow 的 set_status（那里还做 lease 校验、terminal 证明），
        最小版做「锁内改内存 status + 记错误」，然后同步写一份到 store。
        """
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("set_status called for unknown run %s", run_id)
                return
            record.status = status
            if error is not None:
                record.error = error
        await self._persist_status(run_id, status, error=error)
        logger.info("Run %s -> %s", run_id, status.value)
