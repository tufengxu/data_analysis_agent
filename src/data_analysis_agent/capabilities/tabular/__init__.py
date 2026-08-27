"""表格分析能力域:Excel/CSV 读取、画像、质量、join 规划、口径契约、查询执行。

委托 v1 ``tools/`` + ``kernel/`` 实现(能力侧代码,非 harness 内部);持久内核语义
(变量/DataFrame 跨调用存活、崩溃/超时重启并显式报告状态丢失、启动失败降级受限
子进程)在能力层保留。契约经 ``contracts.CapabilitySpec`` 声明。
"""

from .registry import KernelHolder, register_all

__all__ = ["KernelHolder", "register_all"]
