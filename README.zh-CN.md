# MegaBench

[English](README.md) | 简体中文

MegaBench 是一个面向外表 OLAP 场景 Mega-query 检测的 trace-derived
workload benchmark 工具集。

它更接近 ClickBench 风格的真实 workload 建模，而不是完整的
TPC-H/TPC-DS 可执行数据库 benchmark。当前版本聚焦发布隐私保护后的查询
workload：脱敏 SQL 形态、结构化特征、分桶后的 oracle 指标、标签、模板、查询生成器，
以及合成大宽表数据生成器。

## Artifacts

仓库内置预生成的公开 artifacts，位于 `data/public/`：

这些 artifacts 源自真实生产环境中面向消费内容/推荐业务的商务 BI、经营报表和
临时业务分析外表 OLAP 负载。整体语义上，它主要反映分析师和看板系统对大宽表/
事件事实表的查询，覆盖内容/对象与用户/设备维度、推荐策略和实验分组分析、流量
漏斗与互动指标、商业转化类指标、日期/小时切片，以及 JSON/数组属性解析、指标
聚合、排序和多表关联。源集群、环境、库名/表名/列名、用户、query ID 和具体
业务标识均不会公开。

- `workload.jsonl`：公开 workload 样本，包含脱敏 SQL、执行前特征、脱敏后的 plan 特征、标签和分桶后的 oracle 指标。
- `templates.json.gz`：从重复查询形态中挖掘出的模板目录。
- `stats.json`：私有 trace 与公开样本的直方图摘要和验证指标。
- `distribution_spec.json`：用于生成可执行大宽表数据集的粗粒度合成数据分布参数。
- `manifest.json`：artifact 元信息，包括扫描记录数、样本大小、模板数量和隐私边界。
- `validation_report.md`：简洁的 artifact 质量与隐私报告。

项目不会输出原始 SQL、原始 query plan、真实库名/表名/列名、用户、query ID、异常文本，以及精确的运行时或 IO 指标。

## 环境

```bash
source ./env.sh
```

该命令会通过 `uv sync` 创建 `.venv`，以 editable mode 安装 MegaBench，并激活环境。运行时不依赖 Python 标准库之外的包；开发环境包含 `pytest`。

## 生成合成数据集

MegaBench 可以基于公开的 `data/public/distribution_spec.json`，在本地生成
ClickBench-like 的大宽事件/事实表。官方目标规模包括 `1G`、`10G`、`100G`
和 `1000G`；这里的规模指生成后的数据文件大小。默认合成日期窗口从
`2024-01-01` 开始，持续 30 天。

```bash
megabench dataset generate --scale 1G
```

需要时可以覆盖生成的分区日期：

```bash
megabench dataset generate --scale 1G --start-date 2024-06-01 --days 14
```

默认会在 `data/generated/1G/` 下写出 CSV 文件：

```text
data/generated/1G/
  manifest.json
  schema.json
  files.json
  distribution_spec.snapshot.json
  events_wide/
    event_date=2024-01-01/
      part-00000.csv
```

合成表包含内容/对象、用户/设备、推荐策略、实验分组、地域/app、流量来源、
互动指标、商业指标、JSON-like 属性、数组列，以及额外的大宽表维度列和指标列。
固定 seed 下生成结果可复现。

如需输出 Parquet，需要先安装 `pyarrow`：

```bash
uv sync --extra parquet
megabench dataset generate --scale 1G --format parquet
```

检查已生成的数据集：

```bash
megabench dataset inspect data/generated/1G
```

## 生成合成 Workload 记录

```bash
megabench generate
```

该命令会从仓库内置的 `data/public/` 模板中采样，并写入
`artifacts/generated_workload.jsonl`。

支持的 profiles：

- `balanced`：按观测频率采样查询模式。
- `mega_heavy`：提高更容易产生 Mega-query 的模板权重。
- `external_table_stress`：提高大规模外表扫描模板权重。

## 记录格式

```json
{
  "query_id": "mq_00000001",
  "template_id": "T0001",
  "sql": "SELECT count() FROM events_wide_001 WHERE c_0001 = {{int}}",
  "pre_execution_features": {
    "num_tables": 1,
    "num_columns": 3,
    "query_length_bucket": "100_1k",
    "query_type": "2",
    "event_hour": "13"
  },
  "plan_features": {
    "read": 1,
    "filter": 1,
    "aggregation": 1
  },
  "label": "normal",
  "oracle_buckets": {
    "read_bytes": "1GB_100GB",
    "lake_read_files": "100_1k",
    "query_duration_ms": "1s_10s"
  }
}
```

`oracle_buckets` 是执行后的信号，不应作为执行前 Mega-query 检测模型的输入特征。

## 隐私边界

公开 artifact 通过抽象化生成，而不是可逆脱敏：

- 标识符会变成 `events_wide_001`、`c_0001` 这类角色化名称；
- 字符串、数值和日期 literal 会变成占位符；
- 未知函数会映射成 `fn_001`、`fn_002` 等名称；
- 运行时指标会进行分桶；
- 出现次数低于阈值的查询模式会被丢弃。

发布前建议复核 `validation_report.md`，并人工抽查 `workload.jsonl` 的随机样本。

## 范围

MegaBench v0.2 不是完整的数据库引擎 benchmark。它适用于：

- 训练和评估 Mega-query 分类器；
- 研究真实外表 OLAP workload 分布；
- 生成 `1G`、`10G`、`100G` 和 `1000G` 规模的合成大宽表数据；
- 基于观测模板生成更大的合成查询流。

当前生成的数据集是 synthetic 且可执行的，但还不应被视为完整的数据库引擎 benchmark。
面向不同引擎的 loader 和 query runner 属于后续工作。

## 维护者

`build` 命令用于从私有 trace 重新生成 `data/public/`，普通 benchmark 用户不需要使用。
