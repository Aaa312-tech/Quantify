# Quantify

[![Checks](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml/badge.svg)](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml)

A股模拟投资研究项目，Python包与命令名为`quantfolio`。当前采用**滚动选型动量**策略：动量候选池、行业残差模型、滚动验证选型、次日开盘模拟。使用均衡进攻和8%回撤预警；仓位降级后不恢复。

**修正数据与执行后的完整重训已完成，收益优势仍未通过。** 开盘成交使用报价代理，滑点单独扣款；分红支付日期与现金可用日期分别记账。11个外层窗口及最新一期共48个模型重新拟合后，所有窗口仍选择动量。进度与验收见[修正记录](docs/remediation.md)。

下面是修正快照上重新训练、重新选型后的结果。主策略与同风控动量相同，没有验证出额外选股收益：

| 每边滑点，另计交易费 | 滚动选型动量净收益 | 同风控动量净收益 | 最大回撤 | 10日账户平均净收益 |
| --- | ---: | ---: | ---: | ---: |
| 10bp | 31.03% | 31.03% | -6.53% | 2.12% |
| 20bp | 7.88% | 7.88% | -6.40% | 1.05% |
| 30bp | 3.56% | 3.56% | -6.94% | 1.12% |

回测区间2025-11-18至2026-11-02，处理后数据仍截止2026-11-18。23个完整10日账户每次重置本金，其均值不能等同于连续收益。成本会改变仓位与风险路径，低成本档结果不能外推。8%是预警和退出目标，不能保证实际回撤不超过8%。原冻结记录保留在`artifacts/reference`；本轮差异归因见[核验结果](artifacts/checks/causal_retraining_2026-11-20/comparison.json)。

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
