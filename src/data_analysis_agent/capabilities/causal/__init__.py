"""因果能力域:意图识别、因果图建模、识别策略、效应估计/实验 readout、QA 与报告适配。

委托式迁移:契约注册在本层,实现单向复用 v1 纯 stdlib 领域层 ``causal/``(drift 规则
守护其纯度)。「分析」(建模/图/识别)与「推断」(效应估计/实验结论)作为两个显式子
能力暴露,边界在契约中声明。
"""

from .registry import register_all

__all__ = ["register_all"]
