"""可视化报告能力域:结构化输入 → 自包含 HTML(ECharts)、图表渲染、报告契约/QA/溯源。

委托式迁移:契约注册在本层,实现单向复用 v1 纯 stdlib 领域层 ``reporting/`` 与
``tools/chart_render``、``tools/html_report``;安全约束原样继承:输出强制限定产物目录
(fail-closed)、文本全转义、chart option ``</`` 逃逸、``echarts_src`` CDN/本地双模式。
"""

from .registry import register_all

__all__ = ["register_all"]
