"""持久化层 —— 对应 deer-flow 的 deerflow/persistence/（最小版）。

SQLAlchemy 访问业务表（run_events 等）都在这层：Base、ORM model、engine。
checkpointer 不在这层（它用 LangGraph 的 saver + psycopg，见 core/runtime/checkpointer）。
"""
