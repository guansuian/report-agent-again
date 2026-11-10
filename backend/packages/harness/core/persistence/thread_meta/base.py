"""ThreadMetaStore 抽象接口 —— 对应 deer-flow 的
deerflow/persistence/thread_meta/base.py（最小版）。

它只负责一件事：把「一个会话（thread）的元信息」存起来，并能查 / 改 / 删。
不关心 run 怎么跑，也不关心消息内容（消息在 run_events 表）。

deer-flow 的 ThreadMetaStore 有 11 个方法（create / claim_unowned / set_project /
get / search / update_display_name / update_status / update_metadata /
update_owner / check_access / delete）。最小版没有登录和项目空间，只留
会话列表需要的 7 个：

    create              登记会话（已存在则原样返回，幂等）
    get                 按 id 查一个会话
    search              按最近更新时间倒序列出会话
    update_display_name 改标题
    update_status       改状态（idle / running / error ...）
    update_metadata     合并 metadata（不是整条覆盖）
    delete              删一个会话

**记录 dict 的字段**（所有方法的返回值都是这个形状）：

    thread_id     str            会话 id（主键）
    user_id       str | None     归属用户；None 表示未归属
    display_name  str | None     标题；None 表示还没起名
    status        str            会话状态，默认 idle
                                 idle / running / error / interrupted / timeout
    metadata      dict           附加信息（前端标记等），没传就是 {}
    created_at    str            创建时间，ISO 8601 字符串
    updated_at    str            最后修改时间，ISO 8601 字符串

三条约定（memory 版和 db 版必须一致）：
1. create 之后，get 必须能查到。
2. create 是幂等的：同一个 thread_id 再 create 一次不报错，原样返回已有记录
   （不覆盖标题 / metadata）。为什么：run 准入时会先 ensure 一次，重试、并发
   都要求重复 create 安全。deer-flow 在 HTTP 层做「先查再建」，最小版把这个
   判断收进 store，调用方更省心。
   例外：已有记录的 user_id 是 None、这次 create 带了 user_id 时，会把它补上
   （「认领」）。这样 run 先自动建行、前端随后再带 user_id 也能落上归属。
   已有记录属于别的 user_id 时**不覆盖**，原样返回。
3. update_* / delete 找不到目标时返回 False（不抛异常），HTTP 层据此报 404；
   和 RunStore.update_status「找不到就静默跳过」是同一个思路 —— 后台任务
   不该因为一行数据不在就把服务带崩。
"""

from __future__ import annotations

import abc
from typing import Any


class ThreadMetaStore(abc.ABC):
    """会话元信息的存储抽象接口。"""

    @abc.abstractmethod
    async def create(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        display_name: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """登记一个会话，返回存进去（或已存在）的那条记录。

        thread_id 已存在时不报错、不覆盖，原样返回已有记录 —— 幂等。
        唯一例外：已有记录的 user_id 为 None 且本次传了 user_id 时，补上归属。
        """

    @abc.abstractmethod
    async def get(self, thread_id: str) -> dict | None:
        """按 thread_id 查一个会话；查不到返回 None。"""

    @abc.abstractmethod
    async def search(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """列出会话，最近更新的在前。

        Args:
            user_id: 只看某个用户的会话（None = 不筛，单用户模式）。
            status:  只看某个状态（None = 不筛）。
            limit:   最多返回多少条。
            offset:  跳过前多少条（翻页用）。
        """

    @abc.abstractmethod
    async def update_display_name(self, thread_id: str, display_name: str) -> bool:
        """改标题，返回是否真的改到了（会话不存在 → False）。"""

    @abc.abstractmethod
    async def update_status(self, thread_id: str, status: str) -> bool:
        """改状态，返回是否真的改到了（会话不存在 → False）。"""

    @abc.abstractmethod
    async def update_metadata(
        self,
        thread_id: str,
        metadata: dict[str, Any],
        *,
        touch: bool = True,
    ) -> bool:
        """把 metadata 合并进已有 metadata，返回是否真的改到了。

        合并规则：同名的键被新值覆盖，没传的键保留。
        为什么不是整条覆盖：前端可能只改一个 ``pinned`` 标记，
        整条覆盖会把别的模块写的键冲掉。

        touch=False 时**不刷新 updated_at**：置顶/归档这类「不是聊天活动」
        的变更不该把会话顶到列表最前面。对应 deer-flow 的 ``touch`` 参数。
        """

    @abc.abstractmethod
    async def delete(self, thread_id: str) -> bool:
        """删一个会话，返回是否真的删到了（会话不存在 → False）。"""
