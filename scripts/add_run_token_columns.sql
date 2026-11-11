-- 给已有的 PostgreSQL runs 表补上用量合计字段。
-- SQLAlchemy 的 create_all 不会给已有表增加列，因此需要单独执行本文件。
-- IF NOT EXISTS：列已存在时跳过，可以重复执行。
-- 新增列后，旧记录的这四项值为 0；本脚本不从 run_events 回算历史用量。

BEGIN;

ALTER TABLE runs
    -- 本次 run 的累计输入 Token
    ADD COLUMN IF NOT EXISTS total_input_tokens INTEGER NOT NULL DEFAULT 0,
    -- 本次 run 的累计输出 Token
    ADD COLUMN IF NOT EXISTS total_output_tokens INTEGER NOT NULL DEFAULT 0,
    -- 本次 run 的累计总 Token
    ADD COLUMN IF NOT EXISTS total_tokens INTEGER NOT NULL DEFAULT 0,
    -- 本次 run 中取得有效用量的模型调用次数
    ADD COLUMN IF NOT EXISTS llm_call_count INTEGER NOT NULL DEFAULT 0;

COMMIT;
