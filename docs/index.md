# 文档

## 使用

| 文档 | 内容 |
| --- | --- |
| [安装](../README.md#安装与验证) | 环境、Git LFS和当前模型验证 |
| [复现](reproducibility.md) | 配置、训练、评估、报告和历史结果复核 |
| [纸面账户](paper_workflow.md) | 账户格式、次日计划、报价及收盘结转 |
| [当前状态](status.md) | 有效数据、模型、结果和剩余限制 |

## 方法与开发

| 文档 | 内容 |
| --- | --- |
| [策略方法](methodology.md) | 选股、仓位、风控、成本和检验范围 |
| [数据契约](data_contract.md) | 字段、单位、时间边界和来源要求 |
| [代码结构](architecture.md) | 模块职责与执行流程 |
| [验证](validation.md) | 测试、模型重载、账本对照和源码兼容 |
| [开发约定](../CONTRIBUTING.md) | 命名、代码、测试和提交 |
| [仓库维护](repository.md) | 文件归属、冻结记录、Git LFS和发布 |

## 研究记录

专题文档描述各自登记的研究范围，当前有效结论以[项目状态](status.md)和配置为准。

| 范围 | 记录 |
| --- | --- |
| 选股、恢复与换手 | [策略对照](strategy_study.md) · [卖出预算](exit_budget_repair.md) |
| 股票行业 | [日期化行业](industry_timing.md) |
| ETF | [来源](etf_sources.md) · [持仓表](etf_holdings.md) · [敞口](etf_exposures.md) · [接入](etf_integration.md) |
| 公司行为 | [条款核对](distribution_terms.md) · [权益与可用时间](distribution_availability.md) |
| 历史与待办 | [研究历史](research_history.md) · [研究待办](remediation.md) · [变更记录](../CHANGELOG.md) |

原始来源保存在 `data/raw/` 和 `docs/references/`，机器结果保存在 `artifacts/`。报告中的数字可沿相应评估摘要追溯到模型、输入和逐日账户。
