# 0007 — 本地文件优先,数据源连接(数据库)暂缓

- 状态: Accepted (2026-06-30)

## 背景

项目定位是数据分析 Agent。早期调研报告把「数据源连接(数据库/SQLAlchemy)」列为优先升级项之一。
但项目实际服务场景在未来一段时间内主要是**本地 Excel/CSV 文件分析**:单文件单 sheet、单文件多
sheet、多文件多 sheet 的联合分析。数据库查询不是占比大的场景。

## 决策

Phase 1 集中夯实**本地文件分析地基**,数据库/外部数据源连接**暂缓**,记为后续迭代 TODO。

本阶段已落地(地基):

- Excel 运行期依赖入 `data` extra(`openpyxl`、`xlrd`);
- 只读 `data_profile` 工具:文件→列/类型/采样行数(Excel 逐 sheet),目录→可分析文件清单 +
  列预览(发现 sheet 与跨文件连接键),路径白名单 fail-closed,输出绝对路径;
- CLI `--path`(可重复)把数据文件/目录授权给数据读取工具;
- 系统提示 + 内置 `joint_analysis` 技能:发现→画像→定连接键→merge→校验 的联合分析方法论。

暂缓项(后续 TODO):

- 数据库连接(SQLAlchemy 引擎、连接串管理、NL2SQL 落地执行)。`nl_query` 现有 SQL 代码生成保留为
  「起点代码」,但不接入真实执行/连接管理。
- `nl_query` 的 `source_type` 暂不加 `excel`(模型可直接用 `python_analysis` 读 Excel)。
- Parquet 大文件画像走 pyarrow 元数据(已实现降级路径),但 parquet 引擎不列为项目硬依赖。

## 理由

实事求是:按真实使用场景集中优势兵力,先让本地 Excel/CSV/多表联合分析端到端跑通,而不是过早投入
占比小的数据库通道。自进化(领域记忆/技能蒸馏)从分析轨迹蒸馏 —— 地基不牢则轨迹无价值,故先地基后记忆。

## 影响

新增 `tools/data_profile.py`;`runtime`/`config`/`__main__`/`skills/builtin` 接线;`data` extra 增
`openpyxl`/`xlrd`;mypy 覆盖增 `pyarrow.*`。数据库连接相关需求在后续阶段单独立项再评估。
