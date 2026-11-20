# Quantify

[![Checks](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml/badge.svg)](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml)

A股模拟投资研究项目，Python包与命令名为`quantfolio`。当前采用**滚动选型动量**策略：动量候选池、行业残差模型、滚动验证选型、次日开盘模拟。使用均衡进攻和8%回撤预警；仓位降级后不恢复。

**选型稳健性修正已通过验收，收益仍与同风控动量持平。** 采用模型或换手规则须满足原门槛，且平均超额减标准误严格为正；第7窗口因此回到动量。该问题来自已研究的历史数据，不能视为独立的收益验证。进度见[修正记录](docs/remediation.md)。

下面是11月19日快照上完成48次重新拟合后的结果。相较修正前的选型，三档连续净收益分别提高2.43、2.74、2.24个百分点；原48个模型和1425个基线账户完整保留并逐项复现：

| 每边滑点，另计交易费 | 滚动选型动量净收益 | 同风控动量净收益 | 最大回撤 | 10日账户平均净收益 |
| --- | ---: | ---: | ---: | ---: |
| 10bp | 31.03% | 31.03% | -6.53% | 2.12% |
| 20bp | 7.88% | 7.88% | -6.40% | 1.05% |
| 30bp | 3.56% | 3.56% | -6.94% | 1.12% |

回测区间2025-11-18至2026-11-02，处理后数据截止2026-11-19；最新一天尚未增加完整外层测试窗口。23个完整10日账户每次重置本金，其均值不能等同于连续收益。成本会改变仓位与风险路径，低成本档结果不能外推。8%是预警和退出目标，不能保证实际回撤不超过8%。原冻结记录保留在`artifacts/reference`；本轮工程、收益、风险及数据质量分别列在[核验结果](artifacts/checks/strategy_study_2026-11-20/comparison.json)。

本轮登记的六项单独改动和两项有条件组合共完成2325个外层账户审计。额外成本惩罚降低三档收益；成本缓冲没有带来改善。恢复仓位的历史收益较高，但仍限研究用途；完整同风控对照、失败候选与归因见[策略实验](docs/strategy_study.md)。

随后单独检验了卖出预算冲突的最低必要修正：96次拟合、1725个账户评估完成，原规则1425个账户精确复现。修正后10bp连续净收益降至7.31%，最大回撤8.12%，三档10日均值均下降，因此保留原换手政策。完整失败结果见[卖出预算研究](docs/exit_budget_repair.md)。

594只证券的新行情已通过完整生成和历史前缀检查。全部外层账户未触发事件质量标记；内层候选仍涉及33项未核实事件，历史证券状态、行业时点与选样偏差也未解决。数据覆盖及字段修订见[采集核查](artifacts/checks/market_refresh_2026-11-20/collection_review.json)，这些限制继续约束模型采用和前向使用。

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

当前11月19日数据在11月20日开盘后已过期。下一次有效计划须完成11月20日行情核验和对应最新拟合；不会回填已经发生的开盘建议。输入格式、时点和已知换手限制见[纸面流程](docs/paper_workflow.md)。

11月20日日线已核验414只，仍缺180只。备用收盘报价虽覆盖594只，但交叉核对发现17只换手率不一致，另有2只成交额字段内部不一致，未接入训练。失败记录见[跨源核查](artifacts/checks/market_close_2026-11-20/closing_quote_comparison.json)。

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
