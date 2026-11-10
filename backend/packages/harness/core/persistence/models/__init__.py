"""业务表 ORM model 集合。

这个文件是「汇总登记处」：所有 ORM model 必须在这里 import 一次，
Base.metadata 才会认识它们。

为什么不能省：
    SQLAlchemy 的 create_all 只看 Base.metadata.tables 里登记了哪些表。
    model 类定义在别的文件里，但那个文件如果从来没被 import，类就不会
    执行，也就不会登记进 metadata —— create_all 自然跳过它，表不会建出来。
    所以启动时只要 import 了这个包，所有表就自动齐了（engine.py 里
    create_all 之前会 import 本包）。
"""

from core.persistence.models.run import RunRow
from core.persistence.models.run_event import RunEventRow, RunEventThreadSeqRow
from core.persistence.models.thread_meta import ThreadMetaRow

__all__ = ["RunEventRow", "RunEventThreadSeqRow", "RunRow", "ThreadMetaRow"]
