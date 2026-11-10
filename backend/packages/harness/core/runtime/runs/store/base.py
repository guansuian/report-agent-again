"""RunStore 抽象接口 —— 对应 deer-flow 的
deerflow/runtime/runs/store/base.py（最小版）。

它只负责一件事：把「一次 run 的记录」存起来，并能按 id 读回来、改状态。
不关心 run 怎么跑，也不关心谁调用它。

deer-flow 的 RunStore 有十几个方法（put / get / list / claim / release / lease…），
最小版只留 3 个，刚好覆盖 RunManager 现在的全部动作：

    put            新建一个 run（已存在则覆盖，幂等）
    get            按 run_id 查一条
    update_status  改状态 / 记错误

两条约定（memory 版和 db 版必须一致）：
1. put() 之后，get() 必须能查到；
2. put() 是 upsert —— 同一个 run_id 再 put 一次不报错，直接覆盖。
   为什么需要：上层失败重试时会重复 put，若撞主键报错，重试就没意义了。

**记录 dict 的字段**（get 的返回值、put 的返回值都是这个形状）：

    run_id      str           哪一次 run（主键）
    thread_id   str           属于哪个会话
    user_id     str | None    归属用户；None 表示未归属
    status      str           生命周期状态，取值见 RunStatus
                              pending / running / success / error / timeout / interrupted
    error       str | None    出错时的描述；成功、运行中都是 None
    metadata    dict          前端附加信息，没传就是 {}
    created_at  str           创建时间，ISO 8601 字符串
    updated_at  str           最后修改时间，ISO 8601 字符串

注意 status 用字符串而不是 RunStatus 枚举：跨进程/跨存储要能直接写进
数据库的 VARCHAR 列，枚举在边界上再转（RunStatus 是 StrEnum，本身就是 str）。
"""

from __future__ import annotations

import abc

from core.runtime.runs.schemas import RunStatus


class RunStore(abc.ABC):
    """run 记录的存储抽象接口。"""

    @abc.abstractmethod
    async def put(
        self,
        run_id: str,
        *,
        thread_id: str,
        user_id: str | None = None,
        status: RunStatus | str = RunStatus.pending,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """写入（或覆盖）一条 run 记录，返回存进去的那条记录 dict。

        run_id 已存在时不报错，整条覆盖 —— 上层重试 put 是安全的。
        user_id 为 None 时保留库里已有的归属（不把老归属冲掉）。
        """

    @abc.abstractmethod
    async def get(self, run_id: str) -> dict | None:
        """按 run_id 查一条记录；查不到返回 None。"""

    @abc.abstractmethod
    async def list_by_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """按会话查这个 thread 跑过的 run，最新的在前，最多 limit 条。

        user_id 不为 None 时只返回该用户的 run。
        """

    @abc.abstractmethod
    async def update_status(
        self,
        run_id: str,
        status: RunStatus | str,
        *,
        error: str | None = None,
    ) -> None:
        """改状态，同时刷新 updated_at。

        - error 为 None 时不动原来的 error 列（只改状态）
        - run_id 不存在时静默返回（打一条 warning），不抛异常
          —— 对应 deer-flow「找不到就跳过」的处理，避免后台任务把服务器带崩
        """

    @abc.abstractmethod
    async def fail_inflight(self, error: str) -> int:
        """把所有「还挂着进行中」的 run 标成 error，返回影响行数。

        什么时候用：**服务启动时调用一次**。此刻内存里没有任何活着的 run，
        库里凡是 status 还是 pending / running 的，都是上次进程崩掉
        （kill -9、断电、代码崩）留下的「孤儿」——没人会再把它跑完，
        不清理的话前端会永远显示「正在生成中」。

        对应 deer-flow 的 reconcile_orphaned_inflight_runs（那边靠租约过期判断，
        我们单进程，启动这一刻就足以判断）。
        memory 版没有持久化，启动时本来就是空的，直接返回 0。
        """
