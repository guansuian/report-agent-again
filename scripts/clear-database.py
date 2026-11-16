"""清空项目的 PostgreSQL 业务和会话数据，保留表结构及迁移记录。

用法：在项目根目录执行 ./scripts/clear-database.sh。
运行前先停止后端，避免运行中的任务继续写入，或继续使用旧的内存状态。
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from core.config.app_config import get_app_config


# 只清理本项目的数据表；checkpoint_migrations 记录表结构版本，必须保留。
DATA_TABLES = (
    "run_events",
    "run_event_thread_seq",
    "runs",
    "threads_meta",
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
)


async def main() -> None:
    config = get_app_config()
    if config.database.backend != "postgres":
        raise ValueError("这个脚本只用于 PostgreSQL，请检查 database.backend 配置。")

    database_url = make_url(config.database.app_sqlalchemy_url)
    database_name = database_url.database

    # 只显示目标主机和数据库名，不打印连接密码。
    print(f"目标主机：{database_url.host}:{database_url.port or 5432}")
    print(f"目标数据库：{database_name}")
    print("将清空所有用户的会话、运行记录、Token 统计和检查点数据：")
    for table in DATA_TABLES:
        print(f"  - {table}")
    print("保留表结构和 checkpoint_migrations 迁移记录。")
    print("请先停止后端，再执行清理。清空的数据无法通过本脚本恢复。")

    confirmation = input(f"输入数据库名 {database_name} 确认清空，输入其他内容取消：")
    if confirmation != database_name:
        print("已取消，没有删除数据。")
        return

    engine = create_async_engine(database_url)
    try:
        # 表名来自上面的固定清单，不接受外部输入，也不级联清理其他表。
        table_names = ", ".join(DATA_TABLES)
        statement = text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY")

        # 在同一事务中清空；全部成功后提交，出错时整批回滚。
        async with engine.begin() as connection:
            await connection.execute(statement)
    finally:
        await engine.dispose()

    print(f"清理完成：已清空数据库 {database_name} 中的 {len(DATA_TABLES)} 张数据表。")


if __name__ == "__main__":
    asyncio.run(main())
