# 代码结构

```text
dataset / features → models / calibration → training / selection
                                             ↓ 冻结预测
rules / allocation / risk → simulation → ledger → validation
                                             ↓
                                    metrics / evaluation → reporting
```

| 模块 | 职责 |
| --- | --- |
| `cli`、`settings`、`storage` | 命令、根目录、输出隔离、原子写入和哈希 |
| `dataset` | 载入快照、完整性、时间窗口及标签结束日净化 |
| `snapshots` | 核验公司行为来源、生成独立快照、重建标签并记录差异 |
| `features` | 价格特征、同日横截面排序、行业相对特征 |
| `models`、`calibration` | 固定估计器、残差目标、预测混合、行业检验及历史收益校准 |
| `training`、`selection` | 在过去验证段选择模型和规则，再重训并冻结外层预测 |
| `rules`、`allocation` | 换手开关、选股和线性规划仓位 |
| `risk`、`market` | 风险降级、市场状态、仅依赖开盘可用信息的报价代理 |
| `simulation` | 信号和每日估值；内部 `_Execution`按先卖后买处理成交 |
| `ledger` | 整数股、T+1、分币现金、单独滑点成本、应收款和公司行为 |
| `validation` | 独立重建账务，核对信号时序、风险状态及成交后仓位 |
| `metrics`、`evaluation` | 单账户与10日账户统计、新实验评估与冻结账本精确重放 |
| `verification`、`reporting` | 模型重载与重训核验、从冻结结果或指定实验生成HTML报告 |

模型只读取信号日已知数据，订单在下一交易日开盘处理。`_Execution`按开盘报价代理估值，较早成交扣除的佣金和滑点降低现金及净值，后续买单重新检查上限。`validation`不复用`Ledger`的金额计算或`market`的报价函数，独立检查现金、开盘代理价格、方向涨跌停和成本。

风险控制仅支持 `latched`。运行时特征使用`rank_*`、`industry_*`等含义明确的名称；`dataset.load_bundle`在读取后按`configs/archive.toml`转换历史列名，保持值、类型及模型输入顺序。历史文件本身不改写。主策略使用`selected_strategy`，固定基准换手规则的对照使用`selected_baseline_rules`。

行情与实验在包外，报告模板在包内。新行情遵循[数据契约](data_contract.md)，新实验写入独立目录；[冻结参考文件](../configs/integrity_manifest.json)不随工程整理改写。路径调整只更新清单中的键，保留原SHA-256值；归档名称映射记录原路径。

`train → evaluate → report --run`按实验目录传递结果；评估检查训练时成交模型和执行源码。`execution-bridge`固定旧预测，比较新执行的账本差异；原冻结数值回归须用原提交的`replay`。项目路径和截止日期由`configs/project.toml`提供，运行时校验与输入一致。包导入不修改全局pandas设置。
