# 代码结构

```text
data → dataset / preprocessing / features → training / selection
                                                ↓ 冻结预测
rules / allocation / risk → simulation → ledger → validation
                                                ↓
                                      metrics / evaluation → reporting
```

## 模块

| 模块 | 职责 |
| --- | --- |
| `cli`、`settings`、`storage` | 参数解析、项目路径、输出隔离、原子写入和哈希 |
| `dataset`、`preprocessing`、`features` | 数据载入、历史前缀、特征、资格、标签及时间净化 |
| `market_data`、`daily_sources`、`refresh` | 日线来源、字段转换和独立行情快照 |
| `disclosures`、`distributions`、`snapshots` | 公告归档、权益字段核对和公司行为修正 |
| `status_data`、`status_repair` | 历史证券状态、可用时间和资格修正 |
| `industry_sources`、`industries`、`funds` | 行业来源、日期化分类和基金披露敞口 |
| `models`、`calibration`、`training`、`selection` | 拟合、行业检验、滚动选型和冻结预测 |
| `rules`、`allocation`、`risk` | 换手政策、仓位优化和风险状态 |
| `market`、`simulation`、`ledger` | 开盘代理、每日回测、整数股和分币账户 |
| `validation` | 独立复核价格、成交、资金、持仓及风险限制 |
| `accounts`、`planning` | 账户格式、风险结转、次日计划和报价处理 |
| `metrics`、`evaluation` | 账户统计、策略与成本对照、账本重放 |
| `study`、`feasibility` | 已登记的选股、恢复和卖出预算研究 |
| `verification`、`reporting` | 模型重载、纸面账户对照和阅读报告 |
| `regression` | 准备Git历史实现，精确对照冻结输入、特征、模型和账户，登记完整回归证据 |

## 信号到成交

信号只使用已完成收盘的信息。训练验证、正式评估和注册研究共用 `evaluation.simulate_accounts`运行分段账户与连续账户；内层选型不读取外层结果。

`simulation`按信号、公司行为、先卖后买、收盘估值的顺序推进。公开的`Execution`供回测与纸面计划共用，处理开盘报价、整数股、费用和每笔成交后的约束；`Ledger`记录现金、持仓批次、应收款及权益。仓位优化使用信号时点的约束，开盘成交按实际代理价格再次核对。

回测和纸面开盘共用`simulation.execution_targets`生成金额目标：按开盘净值换算权重，补齐未选持仓的退出目标，再应用个股止损与组合停机。持仓与目标的遍历顺序保持一致。

`validation`自行重建金额、价格参考和敞口，不调用执行器的金额与报价函数。这部分计算有意独立，防止执行器与核账器共享同一个错误。

独立核账按权益变动、逐笔成交和收盘恒等式推进；风险检查单独重建状态历史，再检查开盘时点和成交后约束。各检查保留原错误上下文和计数，金额与费用仍用独立公式计算。

## 纸面账户

`planning`把次日流程分为收盘意向、开盘报价解析和收盘结转。`_opening_contracts`冻结信号时点可知的价格与流动性约束；`_opening_market`校验报价并构造执行输入；`resolve_intent`调用相同的成交和账本规则。

`accounts`恢复持仓、分红权益和风险高水位，并独立核对开盘成交。账户通过父记录哈希串联；风险模式、交易日计数及原始本金随账户传递，不能在新一天重新初始化。

冻结计划先核对账户和信号时点，再准备参考报价预览；开盘结果与收盘报价分别验证后才能结转。父账户验证和收盘结转共用计划中的证券元数据，包括仍持有分红权益的证券。

## 数据与实验身份

行业和基金数据在信号日16:00与执行日09:25分别取当时可用记录。未知分类、未披露敞口及未知权益日期各自保留，不通过填充未来记录消除缺项。字段和时间约定见[数据契约](data_contract.md)。

`configs/project.toml`决定当前快照、协议和评估。登记对照通过 `--config`选择独立配置；模型、预测和评估目录保存在包外，报告模板随包分发。导入包不修改全局pandas配置。

历史名称由 `configs/archive.toml`在载入时映射，原文件不改写。训练协议和计划保持原源码哈希。旧兼容记录仍核对全部Python源码；新记录把完整回归证明与实际导入的模块集合绑定，无关报告模块的修改不改变金融产物的兼容性。共享规则、序列化代码和函数内部导入均在依赖范围内；无法确定的动态导入拒绝使用。检查规则见[验证](validation.md)。

`integrity`负责注册文件的内容核对及源码依赖；`runs`负责运行记录、父产物遍历和可重建索引。训练、评估和冻结计划在读取前核验依赖，结束时再次读取哈希，拒绝运行中变更。新目录中的`run.json`保存配置原文、有效参数、输入、父产物、源码和输出；原财务结果格式不变，旧目录不补写新文件。

活跃风险模式为 `latched`，换手为 `legacy`。恢复机制和 `exit_priority`保留在注册研究中，未被默认启用。

## 数据修补与报告

`snapshots.repair_distribution_snapshot`依次校验登记来源、选择已接受事实、重建数据、比较变化并写入新产物。来源绑定、独立标签审查和字段差异检查各自在模块内完成，不把来源解析成功视为财务事实已接受。

`reporting.build_report`先核对评估摘要和两份账本，再分别绘图、准备说明及渲染HTML。报告使用已冻结结果，默认、指定实验和历史参考三个入口的展示数值、链接与输出字节分别核对。
