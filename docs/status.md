# 项目状态

截至2026-11-24。策略研究继续暂停。本次按明确授权补齐11月23日行情、沿用原流程训练并生成11月24日纸面计划；未修改策略、开启新候选实验或记入前向成交。工程E0–E7已完成[发布验收](../artifacts/checks/release_acceptance_2026-11-23/completion.json)，全库扫描的耗时告警按原记录保留。源码清单与命令入口精简已通过本地完整历史回归和质量检查，见[整理验收](../artifacts/checks/workflow_cleanup_2026-11-23/completion.json)。

## 结论

当前数据含594只证券、699个交易日和57列模型特征。11个外层窗口与截止日拟合的12次选型均采用动量及基准换手规则。三档成本下的策略连续净收益与同风控动量相等，尚无机器学习选股优势或前向收益证据。

采用条件为 `positive_score`，换手政策为 `legacy`，风险模式为 `latched`。均衡进攻、8%回撤预警、原始净值高水位和降仓后不恢复保持；成交后单股上限为总资金15%。已登记的六项单独改动和两项组合研究预算已用完。

## 有效产物

| 内容 | 入口 |
| --- | --- |
| 配置 | [project.toml](../configs/project.toml) |
| 数据 | [2026-11-23收盘快照](../data/processed/2026-11-23/) |
| 模型 | [完整训练记录](../artifacts/experiments/daily_close_2026-11-23/training_status.json) |
| 回测 | [评估摘要](../artifacts/evaluations/daily_close_2026-11-23/evaluation_summary.json) · [研究报告](../reports/experiments/daily_close_2026-11-23/index.html) |
| 数据接入 | [逐项验证](../artifacts/checks/daily_close_2026-11-23/validation.json) · [来源比较](../artifacts/checks/session_2026-11-23_reviewed/cross_source.json) · [换手率敏感性](../artifacts/checks/daily_close_2026-11-23/turnover_sensitivity/result.json) |
| 来源核查 | [正文上下文审查](../artifacts/checks/distribution_residuals_2026-11-22/body_context_review.json) |
| 纸面计划 | [11月24日操作说明](../reports/plans/2026-11-24.md) · [冻结计划](../artifacts/plans/daily_close_2026-11-24/plan.md)：开盘前冻结，0笔参考订单，保留100万元现金；仅适用于指定交易日 |
| 验证 | [验证说明](validation.md) · [完整性清单](../configs/integrity_manifest.json) |
| 工程维护 | [验收记录](engineering_plan.md#阶段和依赖) · [本地完整回归](../artifacts/checks/workflow_cleanup_full_2026-11-23/result.json) · [质量检查](../artifacts/checks/workflow_cleanup_2026-11-23/quality.json) · [0.30.0发布时的托管回归](../artifacts/checks/release_acceptance_2026-11-23/cloud_windows_2025/ci-regression-45122115960/result.json) |
| 仓库整理 | [目录与清理约定](repository.md) · [清理验收](../artifacts/checks/repository_cleanup_2026-11-23/completion.json) |

旧模型、旧计划及失败记录保留。配置决定实际使用的输入与实验，历史回测不会因文档或报告重排而重新选择。

## 数据核验范围

11月23日行情覆盖原股票池594只证券：568只沪深证券采用BaoStock，并与腾讯收盘报价核验；26只北交所证券核对腾讯日线与报价，其中5只另有东方财富日线佐证，其余21只仍依赖同一提供方。58只科创板证券的股数按既定规则换算为整手；18处换手率口径差异均保留，替换这些输入后采用策略的预测与计划完全一致。新增西南证券11月24日每股0.02元分红，见[公告审查](../artifacts/checks/session_2026-11-23_reviewed/action_review.json)。

当前568只证券具备选股资格，0只通过正收益边际门槛，见[完整信号](../artifacts/checks/daily_close_2026-11-23/signals.json)。市场仓位上限30%，实际目标仓位0%；有效空仓计划取代此前行情过期导致的阻断记录。

截至11月22日的来源补证已接受1452条事件记录、1530个支持字段。该次权益接入涉及31项事件，更正3处现金金额、31项支付日、19项除权参考值和1项股份上市日，并完成对应训练与账户核对。

最近来源检查涉及172份公告、509页、333处上下文及274组句子，实际查看14张原页。它没有覆盖全部1135份公告，也没有接受新财务事实。六处说明分类补正、六处空白范围差异、两处重复句子的页码差异及初次失败均留在原审查记录中。

## 未解决事项

| 范围 | 限制 |
| --- | --- |
| 公司行为 | 387项来源未通过；95项事件的115个候选字段及另外8项上市日线索尚未接受 |
| 权益日期 | 320项支付日、88项股份上市日缺失，366项事件至少缺少一类日期 |
| 行情与状态 | 73只证券有640个内部缺失交易日，111个缺口结束点未解决 |
| 股票池与时点 | 历史证券范围、公告真实发布时间尚未完整认证，仍有样本选择偏差 |
| ETF | 使用披露股票敞口代理，完整每日经济敞口未知 |
| 比赛 | 以白皮书为准；费用、分红税及撮合细节待确认 |
| 收益 | 没有优于基准或新增前向成交的证据；8%预警不保证实际损失上限 |
| 工程环境 | 24组当前权重已在四套环境中匹配原哈希；冻结旧Git基线在Windows 2022的完整对照仍因系统`exp2`差异失败，完整验收使用研究锁及Windows 11/2025。缺少Bottleneck的通用环境校准未通过；不认证跨环境重训 |

后续工作及验收条件见[研究待办](remediation.md)。恢复研究需要明确指令；过期计划不能改日期后继续使用。
