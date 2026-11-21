# Quantify

[![Checks](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml/badge.svg)](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml)

A股模拟投资研究项目，Python包与命令名为`quantfolio`。当前采用**滚动选型动量**：动量候选池、同行残差模型、滚动验证选型、次日开盘模拟。沿用均衡进攻、8%回撤预警和仓位降级后不恢复。

**当前仅作研究，尚未证明超越同风控动量。** 活跃快照截止11月20日，使用日期化股票行业、ETF基准分组和已披露股票敞口约束。最新补齐31项已核实的分红支付日，全部特征与收益标签精确不变。详见[接入说明](docs/distribution_availability.md)与[完整归因](artifacts/checks/implemented_payments_2026-11-22/attribution.json)。

| 每边滑点，另计交易费 | 滚动选型动量净收益 | 同风控动量净收益 | 最大回撤 | 10日账户平均净收益 |
| --- | ---: | ---: | ---: | ---: |
| 10bp | 35.93% | 35.93% | -7.59% | 1.75% |
| 20bp | 3.38% | 3.38% | -6.30% | 0.64% |
| 30bp | 11.79% | 11.79% | -7.94% | 0.87% |

回测区间为2025-11-18至2026-11-02。23个完整10日账户分别重置本金，其均值不等于连续收益；另有一个不足10日的账户。各成本档重新计算订单和风险路径，收益不必随成本单调变化。8%是预警和退出目标，不保证实际回撤上限。

当前数据接入完成48次拟合及重载、1425个重训账户、1425个固定旧预测账户和75个纸面账户核验。旧输入的48个模型、1425个完整账户和75个纸面账户精确复现；36312项登记前冻结文件保持原字节。687项本地测试通过，1项主机权限检查跳过。训练样本、目标与权重在拟合前冻结并逐项核对，来源事实的采纳不取决于收益方向。

[分红来源复核](docs/distribution_terms.md)覆盖1839项事件，1421项有字段证据，418项仍未通过。实施范围复核新增的34项支付日依据中，31项缺失值已通过独立接入，3项原已一致。[来源验收](artifacts/checks/payout_scope_2026-11-21/completion.json)保留136项独立字段检查和38个完整原页核对；本批另核对19827个标签和全部24个拟合阶段。

当前输入仍缺351项支付日、89项股份上市日，至少缺一类日期的事件有397项。内层保留22次行情质量标记、0次未解决事件；外层为0次质量标记、0次未解决事件。账户重复暴露不等于独立事件数。历史股票池、实际发布时间、111个行情缺口结束点和完整ETF经济敞口仍未完成核验。

11月23日[条件式纸面计划](artifacts/plans/implemented_payments_2026-11-23/plan.md)已按当前输入冻结：0笔参考订单，100万元现金，567个合格标的中有0个通过当前净收益建仓筛选。有效期止于11月23日09:25；尚无前向成交或收益，旧计划保持原样。

数据边界和后续工作见[数据契约](docs/data_contract.md)与[修正记录](docs/remediation.md)。[行业对照](docs/industry_timing.md)、[ETF接入](docs/etf_integration.md)及[分红来源](docs/distribution_terms.md)保留各阶段证据；未增加alpha候选或放宽风控。

![滚动选型动量与基准的历史净值](reports/latest/equity_curve.png)

## 运行

已验证环境：Windows、Python 3.13.2。先安装Git LFS，再获取数据和模型：

```powershell
git lfs install
git clone https://github.com/Aaa312-tech/Quantify.git
Set-Location Quantify
git lfs pull
python -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[dev]'
.\.venv\Scripts\python -m quantfolio status
.\.venv\Scripts\python -m quantfolio verify
.\.venv\Scripts\python -m quantfolio report
```

以上为Windows命令；Linux使用`.venv/bin/python`。源码ZIP可能只包含LFS指针。训练、评估与报告读取本地数据；`fetch-bars`另行采集日线，原始下载不会自动成为活跃快照。从其他目录运行时指定`--project PATH`。完整训练、重放和环境限制见[复现指南](docs/reproducibility.md)。

## 研究流程

沿用上面创建的虚拟环境，依次运行：

```powershell
.\.venv\Scripts\python -m quantfolio train --first 1 --last 1 --output artifacts/experiments/example_run
.\.venv\Scripts\python -m quantfolio evaluate --run artifacts/experiments/example_run --output artifacts/evaluations/example_run
.\.venv\Scripts\python -m quantfolio report --run artifacts/evaluations/example_run
```

示例仅运行一个窗口，结果会标为`PARTIAL`。完整训练、成本选择和冻结重放见[复现指南](docs/reproducibility.md)。新实验不会自动替换主策略；报告可从对应的`reports/experiments`目录打开。

## 纸面观察

`account-init → plan → resolve-plan → account-close`连接收盘账户、提前冻结的次日意向、手工开盘报价与收盘结转。它复用原策略并保留风险高水位，已通过75个历史账户、1386个交易日的完整账本对比；这不构成新增前向样本。

当前数据、最新拟合和100万元纯现金纸面账户均对应11月20日。11月23日计划在开盘前冻结，尚无开盘成交或前向收益；旧的过期就绪记录保留。输入格式、时点和已知换手限制见[纸面流程](docs/paper_workflow.md)。

11月20日新快照使用固定的三组来源覆盖594只证券：原主来源414只，BaoStock补齐178只，腾讯北交所日线覆盖另外2只。此前腾讯报价的17项换手率差异和2项金额校验失败保留；新核查另发现前一日18只ETF的换手率口径差异。原失败核验记录和来源限制均保留，详见[来源就绪审查](artifacts/checks/market_close_2026-11-20/source_readiness_review.json)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/quantfolio/` | 特征、训练、选股、模拟、独立核账及命令入口 |
| `tests/` | 合成数据上的交易规则与因果性测试 |
| `configs/` | 项目路径、实验协议、历史名称映射和冻结文件SHA-256清单 |
| `data/processed/` | 原快照及核验公司行为后的独立快照 |
| `data/raw/` | 按截止日归档的行情响应、覆盖和修订检查；不会自动用于研究 |
| `artifacts/reference/2026-11-19/` | 冻结训练、预测和回测账本 |
| `artifacts/experiments/`、`artifacts/evaluations/` | 新训练及对应评估；每次使用新名称 |
| `artifacts/accounts/`、`artifacts/plans/` | 纸面收盘账户、条件式计划、手工报价及开盘核算；按账户与日期归档 |
| `artifacts/checks/`、`artifacts/replays/` | 当前模型校验、迁移核对和完整账本重放结果 |
| `docs/` | 方法、架构、复现说明及来源材料 |
| `reports/latest/` | 当前配置的研究评估报告与净值图 |
| `scripts/` | PowerShell运行、检查和同步入口 |

## 开发

```powershell
scripts/check.ps1 -Python '.\.venv\Scripts\python'
scripts/sync.ps1 -Python '.\.venv\Scripts\python' -Message 'refactor: describe the change'
```

同步脚本依次检查、提交、推送并等待CI。CI构建并安装wheel，在Windows和Ubuntu上测试；完整历史验证需本地LFS数据。重构应保持账本相同；成交错误修正必须保留旧引擎复现证据，并单独记录新模型的差异。

[代码约定与参考实现](CONTRIBUTING.md) · [架构](docs/architecture.md) · [方法](docs/methodology.md) · [仓库维护](docs/repository.md) · [本地报告](reports/latest/index.html) · [变更记录](CHANGELOG.md)
