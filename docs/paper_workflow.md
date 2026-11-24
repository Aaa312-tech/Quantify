# 纸面账户与次日计划

纸面账户使用收盘余额、已核验行情和最新拟合，生成下一交易日的条件式计划。
它沿用当前5日收益信号、每5个账户交易日调仓及原风险规则；“次日”是操作日期，
不表示已经训练出新的1日收益预测模型。全部结果属于研究用途，不连接APP、券商或微信。

## 输入与时间

`configs/project.toml`必须同时指向一致的行情、完整训练和评估；最新拟合必须覆盖行情截止日。
账户估值日期也必须是同一天。完整日线最早在当日16:00后使用，计划必须在指定下一交易日
09:25之前冻结。所有时刻明确使用`Asia/Shanghai`或`+08:00`。

下一交易日取自[已核验日历](../configs/trading_calendar_2026.json)，不把任意工作日当作开市日。
日历依据[上交所2026年休市通知](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)，
并与深交所、北交所通知交叉核对；临时休市或2027年日期需要另外核验。

当前活跃数据截止2026-11-23，已完成替代行情来源核验、原流程训练与评估，并保留ETF分组、披露股票敞口及分红/股份可用日期约束。基金模式在数据重建和载入时核对，不能丢失标识后跳过独立检查。11月24日条件式计划
已在开盘前冻结；当日09:25后不得重新生成该日的前向计划，也不能只修改日期沿用。

`historical_status_review`必须指向冻结、数据哈希一致的审查；确认历史资格错误时，
计划返回`HISTORICAL_ST_ELIGIBILITY_REPAIR_REQUIRED`并保留空订单。不同快照、
审查文件被改写或不一致的解除决定会被拒绝。
十只已核验证券的日期修正已完成完整重训与账户验收，相关暂停仅对当前快照解除。
[最新纸面计划](../artifacts/plans/daily_close_2026-11-24/plan.md)的行情、账户及训练日期一致；
旧的过期记录保留。未来新快照还须另做绑定审查，不能直接复用解除决定。

## 操作流程

策略研究暂停，本次仅执行已授权的行情更新、原流程训练及纸面计划。以下以11月24日为文件组织示例，须满足上述数据和时间条件；报价核算和收盘记账不会因生成计划自动执行。
命令中的`python`指已安装项目的虚拟环境。输出必须是相应类别下的新目录，已有文件不会覆盖。

首次建立100万元虚拟现金账户：

```powershell
python -m quantfolio account-init --account-id research_paper --as-of 2026-11-23 --output artifacts/accounts/paper_2026-11-23
python -m quantfolio plan --account artifacts/accounts/paper_2026-11-23/account.json --cost 10 --output artifacts/plans/paper_2026-11-24
```

继续观察时使用上次`account-close`生成的账户，不能再次初始化本金、净值高水位或风险等级。
计划生成`plan.json`与可阅读的`plan.md`，冻结输入、模型、代码哈希、账户、目标权重、
风险状态、价格约束和有效期。参考股数以除权调整后的上次收盘参考报价计算；实际开盘的
股数、费用与现金必须重新核算。`--cost`可选10、20、30bp每边滑点，另计原研究交易费。

09:25取得明确来源的竞价报价后，手工保存文件，再运行：

```powershell
python -m quantfolio resolve-plan --plan artifacts/plans/paper_2026-11-24/plan.json --quotes artifacts/plans/quotes_2026-11-24/opening.json --output artifacts/plans/opening_2026-11-24
```

报价文件结构如下，证券代码和价格仅为格式示例，不能用来生成真实观察结果。
`source`应填写实际来源或可定位的记录；每个已有持仓都须提供可核验的开盘估值。

```json
{
  "session": "2026-11-24",
  "source": "填写实际报价来源",
  "recorded_at": "2026-11-24T09:26:00+08:00",
  "quotes": {
    "sh600000": {
      "open": 10.0,
      "market_at": "2026-11-24T09:25:00+08:00",
      "suspended": false
    }
  }
}
```

每条报价须声明停牌状态。缺少候选股报价会跳过该股；已有持仓缺少估值会拒绝整次处理，
不能编造停牌证券的开盘价。当天最高、最低、收盘与全天成交量不会用于开盘决定。
09:30以后才录入或处理的结果标为`RETROSPECTIVE_PAPER_REPLAY`，不计为及时的开盘观察。
手工录入的时间戳与来源仍需人工核实，程序哈希不能证明它们来自交易所。

当日16:00以后，用完整收盘报价结转账户：

```powershell
python -m quantfolio account-close --resolution artifacts/plans/opening_2026-11-24/resolution.json --quotes artifacts/plans/quotes_2026-11-24/closing.json --output artifacts/accounts/paper_2026-11-24
```

收盘报价沿用`session`、`source`、`recorded_at`和`quotes`结构；每条证券记录改为
`{"close": 10.0, "market_at": "2026-11-24T15:00:00+08:00"}`。记录时刻不得早于15:00。
新账户保存现金、逐批股数与可卖日、分红权益、应收款、风险高水位和父记录哈希；下次
计划还会检查这些收盘价格与新核验快照一致。收盘命令本身不刷新行情或重新拟合模型。

所有生成结果自动加入完整性清单。手工输入文件与输出一同保存在项目中，输入哈希随结果
记录；修改输入后不能继续使用原结果。旧记录保留，新一次运行使用新的用途名称。

## 手工账户

已有持仓可参照生成的`account.json`另存输入，声明`kind: "manual"`。
这表示使用者提供的余额与历史，不能因此视为独立认证的券商账单。
其他字段沿用完整账户格式，禁止省略风险状态而默认正常。

| 字段 | 要求 |
| --- | --- |
| `schema_version`、`account_id`、`created_at` | 格式版本1、固定账户名、明确时区的真实记录时刻 |
| `as_of`、`initial_date`、`initial_cash` | 收盘日期、账户起始日期、登记本金100万元 |
| `cash` | 实际可用现金，精确到分；应收款不计入可用现金 |
| `lots` | 每批的`id`、`symbol`、整数`quantity`、`bought`、`available`、调整后的`stop_cost` |
| `next_lot_id` | 大于所有现存持仓及历史权益批次编号，全部卖出后也不能重新从1计数 |
| `receivables` | `symbol`、整数`cents`、`pay_date`、`available_date`、`event_id` |
| `entitlements` | 公司行为编号对应的`[批次编号, 股数, 调整成本, 买入日期]`数组 |
| `risk` | 原`mode/phase/peak/age/healthy_streak/probe_anchor/observations`全部字段 |
| `sessions_completed`、`parent` | 已完成账户交易日数、原纸面父记录；手工起始输入可用`parent: null` |

`available: null`表示尚无已知可卖日期，不会自动视为可卖。纸面流程要求`mode: "latched"`，
只接受`NORMAL`、`CAUTION45`、`FROZEN20`、`HALTED0`；`observations`与已完成交易日数相同。
需要更早股权登记日权益但缺少持仓记录、分红到账日期不明、送股可卖日不明或存在未处理
税款及零碎股时，相关计划失败。初始纯现金账户不能携带历史分红权益。

## 验证与限制

```powershell
python -m quantfolio verify-paper --output artifacts/checks/paper_review
```

[当前验证](../artifacts/checks/paper_refactor_2026-11-22/paper/verification.json)覆盖75个主策略账户、1386个交易日，逐日账本与历史回测一致。费用、现金、可卖数量和成交后仓位另行独立核对，历史验证不增加前向样本。

白皮书限制单一标的不超过总资金20%；当前执行采用更严格的15%及30%行业上限。8%预警不保证损失上限，费用、分红税、撮合容量和比赛排名细节仍待确认。

原换手政策存在已登记的退出限制：持仓84%且收益边际全部转负时，50%的普通卖出预算可能导致优化无解，后备逻辑只按总仓位上限缩减，部分持仓因此保留。8%退出不走此路径。[最小复现](../artifacts/checks/paper_workflow_2026-11-20/known_limitations.json)及[卖出预算研究](exit_budget_repair.md)保留；修正未通过收益与回撤条件，未启用。

`FROZEN_CONDITIONAL_PLAN`表示时间及输入检查通过，`BLOCKED`表示没有有效操作建议，均不保证收益。缺少实际估值、流动性、权益日期或有效来源时，相关流程拒绝继续；已知停牌不会自动补齐这些输入。当前数据限制见[项目状态](status.md)。
