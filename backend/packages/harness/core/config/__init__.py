"""配置子系统 —— 对应真实 deer-flow 的 deerflow/config/（约 60 个文件）。

真实实现把每一块配置都拆成独立的 dataclass 文件：
    memory_config.py / summarization_config.py / skills_config.py /
    subagents_config.py / database_config.py / authorization_config.py ...
再由一个 ``AppConfig`` 汇总，通过 ``load_app_config()`` 从 config.yaml 读入，
缓存成进程级单例（get_app_config()）。

这里为了教学只保留 app_config.py 一个文件，含三个最小结构。
"""
