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
| `features` | 价格特征、同日横截面排序、行业相对特征 |
| `models`、`calibration` | 固定估计器、残差目标、预测混合、行业检验及历史收益校准 |
| `training`、`selection` | 在过去验证段选择模型和规则，再重训并冻结外层预测 |
| `rules`、`allocation` | 换手开关、选股和线性规划仓位 |
| `risk`、`market` | 风险降级、市场状态、可交易开盘价格 |
| `simulation` | 信号和每日估值；内部 `_Execution`按先卖后买处理成交 |
| `ledger` | 整数股、T+1、分币现金、应收款和公司行为 |
| `validation` | 独立重建账务，核对信号时序、风险状态及成交后仓位 |
| `metrics`、`evaluation` | 单账户与10日账户统计、冻结账本精确重放 |
| `verification`、`reporting` | 模型重载与重训核验、从冻结结果生成HTML报告 |

模型只读取信号日已知数据，订单在下一交易日开盘处理。`_Execution`的价格映射包含同一开盘时段较早的成交，后续买单按更新后的净值检查上限。`validation`不复用`Ledger`的金额计算，保留独立核对能力。

`recovery`、`v4_rank_*`、`selected_legacy`等名称属于冻结数据协议，不表示维护旧策略。风险控制仅支持 `latched`，历史记录中的这些字段继续保留以便逐项比对。

行情与实验在包外，报告模板在包内。新行情遵循[数据契约](data_contract.md)，新实验写入独立目录；[冻结参考文件](../configs/integrity_manifest.json)不随工程整理改写。
