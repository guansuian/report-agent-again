-- 给已有数据库补 user_id 列（幂等，可重复执行）。
--
-- 为什么需要手动跑：建表用的是 SQLAlchemy 的 create_all —— 它只会
-- 「表不存在就建表」，不会给已存在的表 ALTER 加列。所以老库必须跑一遍
-- 这个脚本，否则 ORM 插入 user_id 时会报 column does not exist。
--
-- 用法：
--   psql "$DATABASE_URL" -f scripts/add_user_id_columns.sql
--
-- 三张表的情况：
--   threads_meta  本次新增
--   runs          本次新增
--   run_events    建表时就有（早期为了对齐表结构预留的），这里 IF NOT EXISTS
--                 再写一遍，跑在别的环境上也不会报错。
--
-- user_id 语义：归属标签 + 列表过滤器，可空；None = 未归属。
-- 不是权限边界（还没接登录，值由请求体传入）。

ALTER TABLE threads_meta ADD COLUMN IF NOT EXISTS user_id VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_threads_meta_user_id ON threads_meta (user_id);

ALTER TABLE runs ADD COLUMN IF NOT EXISTS user_id VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_runs_user_id ON runs (user_id);

ALTER TABLE run_events ADD COLUMN IF NOT EXISTS user_id VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_run_events_user_id ON run_events (user_id);
