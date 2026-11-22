# Quantify

[![Checks](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml/badge.svg)](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml)

A股模拟投资研究项目，Python包与命令名为 `quantfolio`。使用动量候选池、同行残差模型、滚动验证选型和次日开盘模拟，沿用均衡进攻、8%回撤预警及降仓后不恢复。

**研究目标已暂停，现阶段整理已有成果。** 当前配置、验证边界与暂存待办统一见[项目状态](docs/status.md)。尚未证明策略优于同风控动量，也没有新增前向收益。

## 当前回测

数据截止2026-11-20，回测区间为2025-11-18至2026-11-02。使用日期化行业、ETF披露敞口代理和已核验的股东权益数据。

| 每边滑点，另计交易费 | 滚动选型动量净收益 | 同风控动量净收益 | 最大回撤 |
| --- | ---: | ---: | ---: |
| 10bp | 35.93% | 35.93% | -7.59% |
| 20bp | 3.38% | 3.38% | -6.30% |
| 30bp | 11.79% | 11.79% | -7.94% |

各成本档重新计算订单和风险路径，收益不必随成本单调变化。8%是预警及退出目标，不保证实际回撤上限。完整账户结果见[评估摘要](artifacts/evaluations/recipient_entitlements_2026-11-22/evaluation_summary.json)及[数据接入归因](artifacts/checks/recipient_entitlements_2026-11-22/attribution.json)。

![滚动选型动量与基准的历史净值](reports/latest/equity_curve.png)

## 运行

先安装Git LFS，再获取研究数据和模型。以下为Windows命令，Python支持3.11至3.13：

```powershell
git lfs install
git clone https://github.com/Aaa312-tech/Quantify.git
Set-Location Quantify
git lfs pull
python -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[dev]'
.\.venv\Scripts\python -m quantfolio status
.\.venv\Scripts\python -m quantfolio verify
```

Linux使用 `.venv/bin/python`。源码ZIP可能只含LFS指针。查看当前报告可打开 [reports/latest/index.html](reports/latest/index.html)；训练、评估及纸面流程见[复现指南](docs/reproducibility.md)。示例命令不会自动切换主策略；暂停期间不启动新的研究批次。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/quantfolio/`、`tests/` | 运行代码和行为测试 |
| `configs/` | 活跃配置、登记协议、冻结清单和历史名称兼容映射 |
| `data/raw/`、`data/processed/` | 来源材料和经核验的数据快照 |
| `artifacts/experiments/`、`artifacts/evaluations/` | 训练及对应回测 |
| `artifacts/checks/`、`artifacts/replays/` | 来源、兼容性和完整账本核验，包括保留的失败 |
| `artifacts/reference/`、`artifacts/accounts/`、`artifacts/plans/` | 冻结参考、纸面账户和有时效的计划 |
| `reports/`、`docs/` | 阅读报告、使用说明与研究历史 |
| `scripts/` | 本地检查和GitHub同步入口 |

## 开发与文档

本地检查：`scripts/check.ps1 -Python '.\.venv\Scripts\python'`。数值或执行修改还须验证历史模型和完整账本；文档整理不重新训练。

[项目状态](docs/status.md) · [架构](docs/architecture.md) · [方法](docs/methodology.md) · [复现](docs/reproducibility.md) · [仓库维护](docs/repository.md) · [开发约定](CONTRIBUTING.md) · [变更记录](CHANGELOG.md)
