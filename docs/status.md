# 项目状态

截至2026-11-23。研究暂停，继续维护已有代码和成果；数据采集、新训练、策略试验及前向观察均未恢复。

## 结论

当前数据含594只证券、698个交易日和57列模型特征。11个外层窗口与截止日拟合的12次选型均采用动量及基准换手规则。三档成本下的策略连续净收益与同风控动量相等，尚无机器学习选股优势或前向收益证据。

采用条件为 `positive_score`，换手政策为 `legacy`，风险模式为 `latched`。均衡进攻、8%回撤预警、原始净值高水位和降仓后不恢复保持；成交后单股上限为总资金15%。已登记的六项单独改动和两项组合研究预算已用完。

## 有效产物

| 内容 | 入口 |
| --- | --- |
| 配置 | [project.toml](../configs/project.toml) |
| 数据 | [2026-11-20权益快照](../data/processed/2026-11-20-recipient-entitlements/) |
| 模型 | [完整训练记录](../artifacts/experiments/recipient_entitlements_2026-11-22/training_status.json) |
| 回测 | [评估摘要](../artifacts/evaluations/recipient_entitlements_2026-11-22/evaluation_summary.json) · [研究报告](../reports/experiments/recipient_entitlements_2026-11-22/index.html) |
| 数据接入 | [验收](../artifacts/checks/recipient_entitlements_2026-11-22/completion.json) · [归因](../artifacts/checks/recipient_entitlements_2026-11-22/attribution.json) |
| 来源核查 | [正文上下文审查](../artifacts/checks/distribution_residuals_2026-11-22/body_context_review.json) |
| 纸面计划 | [2026-11-23](../artifacts/plans/recipient_entitlements_2026-11-23/plan.md)：0笔参考订单，100万元现金，09:25上海时间到期 |
| 验证 | [验证说明](validation.md) · [完整性清单](../configs/integrity_manifest.json) |
| 工程维护 | [实施进度](engineering_plan.md#阶段和依赖) · [数值修复验收](../artifacts/checks/portable_predictions_2026-11-23/completion.json) · [本地完整回归](../artifacts/checks/portable_inference_full_2026-11-23/result.json) · [托管AMD完整回归](../artifacts/checks/portable_predictions_2026-11-23/cloud/ci-regression-45046246783/result.json) |

旧模型、旧计划及失败记录保留。配置决定实际使用的输入与实验，历史回测不会因文档或报告重排而重新选择。

## 数据核验范围

已接受1452条事件记录、1530个支持字段。最近一次权益接入涉及31项事件，更正3处现金金额、31项支付日、19项除权参考值和1项股份上市日，并完成对应训练与账户核对。

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
| 工程环境 | 固定Ridge推理顺序后，本地Intel与托管AMD均通过完整历史回归。24组重算训练权重的跨环境哈希仍不同，原因尚未定位；不认证跨环境重训。缺少Bottleneck的通用环境校准失败，完整验收仍使用研究锁 |

后续工作及验收条件见[研究待办](remediation.md)。恢复研究需要明确指令；过期计划不能改日期后继续使用。
