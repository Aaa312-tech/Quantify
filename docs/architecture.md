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
| `dataset` | 载入快照、完整性、已登记的源码等价核验、时间窗口及标签结束日净化 |
| `snapshots` | 核验公司行为来源、生成独立快照、重建标签并记录差异 |
| `status_data`、`status_repair` | 独立状态筛查、公告区间与可用时间核验、历史资格及特征重建 |
| `funds` | 编译基金来源历史，分开基准同行组、已披露敞口约束与未认证资本 |
| `industry_sources`、`industries` | 保留行业原表和差异，按已登记的发布时间约定生成分类历史及独立数据对照 |
| `market_data`、`refresh` | 采集与解析未复权日线，按登记证据追加行情、生成独立快照 |
| `preprocessing` | 从行情生成完整面板、资格、评分、特征和标签，精确验证历史前缀 |
| `features` | 价格特征、同日横截面排序、行业相对特征 |
| `models`、`calibration` | 固定估计器、残差目标、预测混合、行业检验及历史收益校准 |
| `training`、`selection` | 在过去验证段选择模型和规则，再重训并冻结外层预测 |
| `study` | 执行登记的六项改动和两项组合，冻结内层选择，分别评估恢复与同风控基准 |
| `feasibility` | 单独比较原换手与最低卖出预算修正，核对模型、固定预测和完整基线账户 |
| `rules`、`allocation` | 换手开关、选股和线性规划仓位 |
| `risk`、`market` | 风险降级、市场状态、仅依赖开盘可用信息的报价代理 |
| `simulation` | 信号和每日估值；内部 `_Execution`按先卖后买处理成交 |
| `ledger` | 整数股、T+1、分币现金、单独滑点成本、应收款和公司行为 |
| `validation` | 独立重建账务，核对信号时序、风险状态及成交后仓位 |
| `accounts` | 手工及纸面账户格式、持仓批次与风险结转、独立开盘成交核算 |
| `planning` | 有限交易日历、最新预测与输入核验、冻结次日意向、报价处理及收盘归档 |
| `metrics`、`evaluation` | 单账户与10日账户统计、新实验评估与冻结账本精确重放 |
| `verification`、`reporting` | 模型重载与重训核验、纸面流程完整历史重放、研究HTML报告 |

模型只读取信号日已知数据，订单在下一交易日开盘处理。`_Execution`按开盘报价代理估值，较早成交扣除的佣金和滑点降低现金及净值，后续买单重新检查上限。`validation`不复用`Ledger`的金额计算或`market`的报价函数，独立检查现金、开盘代理价格、方向涨跌停和成本。

每日回测由 `_signal_decision` 读取前收盘信号并确定目标，主循环处理公司行为和先卖后买，`_closing_row` 记录收盘估值。`allocation._constraints` 按原顺序构建持仓、现金、行业及换手约束；同一标的的费用率只计算一次，求解后仍独立检查约束。`RiskState._transition` 只选择下一状态，`update` 统一维护高水位、计数和审计证据。三个阶段之间的时间顺序、浮点计算与账本字段保持。

纸面计划在 `_opening_contracts` 中冻结信号日可知的报价、流动性和整手约束；`_opening_market` 校验人工开盘报价并构造执行输入，`resolve_intent` 负责公司行为、先卖后买和账户快照。账户恢复分别校验持仓权益与结转风险；买卖成交使用同一账本记录格式。纸面核账仍自行计算分币费用、现金、可卖数量和成交后敞口，不调用执行器的金额计算。

历史协议与计划保留原始源码哈希。源码改变时，`dataset.source_compatible` 只接受清单中已冻结的 `source_compatibility.json`：它必须绑定原文件身份、全部当前 Python 运行源码，以及通过的差分验证和完整历史账本验证。没有登记、证据不完整或任一绑定变化均拒绝使用；模型输入、预测、日期和计划到期检查仍单独执行。

训练验证、正式评估和研究对照共用 `evaluation.simulate_accounts`，按同一信号区间运行分段账户与连续账户。`evaluate_variants`负责成本和策略矩阵、独立核账与账本写入；`evaluate_accounts`保留原有策略标识入口。训练窗口按校准、内层选型、外层预测推进，`training._select_validation`集中处理内层规则和模型比较，不读取外层结果。成交计算与独立核账仍由不同模块实现。

行业信息分别按信号日16:00与执行日09:25读取。特征表中的日期化分组贯穿残差目标、
中性化和行业检验；执行器使用开盘时的分组检查买入上限，不能反向修改已冻结的收盘意向。
独立核账自行解析分类历史并按持股数量、开盘价格重建行业权重。没有分类历史的旧输入
保留原标签语义；含历史的股票在首期前或被新表遗漏时没有分类，禁止回退到当前标签。
ETF的`fund_history`保存披露基准和来源金额，`fund_mode`绑定固定数据对照。载入拒绝丢失或冲突标识，重建与标签修补保留它。`funds`用分数计算执行约束，`validation`另用十进制金额独立重建；未知经济上界不写成零。

主策略默认使用`latched`。`staged_restore`和`probe_restore`只在注册研究中显式启用，独立核验恢复等待期、市场条件、仓位与永久退出。运行时特征使用`rank_*`、`industry_*`等含义明确的名称；`dataset.load_bundle`在读取后按`configs/archive.toml`转换历史列名，保持值、类型及模型输入顺序。历史文件本身不改写。主策略使用`selected_strategy`，固定基准换手规则的对照使用`selected_baseline_rules`。

行情与实验在包外，报告模板在包内。新行情遵循[数据契约](data_contract.md)，新实验写入独立目录；[冻结参考文件](../configs/integrity_manifest.json)不随工程整理改写。路径调整只更新清单中的键，保留原SHA-256值；归档名称映射记录原路径。

换手政策进入训练身份、内层验证、外层评估和纸面计划。活跃政策为`legacy`；
`exit_priority`只用于登记研究，因收益与回撤验收失败而未启用。

`train → evaluate → report --run`按实验目录传递结果；评估检查训练时成交模型和执行源码。`execution-bridge`固定旧预测，比较新执行的账本差异；原冻结数值回归须用原提交的`replay`。默认配置为`configs/project.toml`；登记对照可用全局参数`--config configs/experiments/<name>.toml`读取独立配置，不修改活跃输入。配置路径不得越出项目的`configs`目录。包导入不修改全局pandas设置。
