# Quantfolio

[![Checks](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml/badge.svg)](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml)

A股模拟投资研究。当前策略为V6：动量候选池、行业残差模型、滚动验证选型、次日开盘模拟。使用均衡进攻和8%回撤预警；仓位降级后不恢复。

**收益优势验证未通过。** V6与同风控动量的主策略结果相同，新增选股模型尚未证明有稳定增益。

| 每边10bp滑点，另计交易费 | V6 | 同风控动量 | 冻结V4不恢复版 |
| --- | ---: | ---: | ---: |
| 连续账户净收益 | 8.11% | 8.11% | 7.78% |
| 最大回撤 | -6.55% | -6.55% | -6.73% |
| 10日账户平均净收益 | 2.00% | 2.00% | 1.85% |

回测区间2025-11-18至2026-11-02，数据截止2026-11-18。共有23个完整10日账户；30bp成本档连续净收益为-4.85%。8%是预警和退出目标，不能保证实际回撤不超过8%。

## 运行

已验证环境：Windows、Python 3.13.2。先安装Git LFS，再获取数据和模型：

```powershell
git lfs install
git clone https://github.com/Aaa312-tech/Quantify.git
Set-Location Quantify
git lfs pull
python -m pip install -e '.[dev]'
python -B -m quantfolio status
python -B -m quantfolio verify
python -B -m quantfolio report
```

源码ZIP可能只包含LFS指针。命令离线读取本地数据；从其他目录运行时指定 `--project PATH`。完整训练、重放和环境限制见[复现指南](docs/reproducibility.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/quantfolio/` | 特征、训练、选股、模拟、独立核账及命令入口 |
| `tests/` | 合成数据上的交易规则与因果性测试 |
| `configs/` | 实验协议和冻结文件SHA-256清单 |
| `data/processed/2026-11-18/` | 行情、公司行为、特征与标签快照 |
| `artifacts/v6_2026-11-19/` | 冻结训练、预测和回测账本 |
| `artifacts/experiments/` | 新训练；每次使用新名称 |
| `artifacts/checks/`、`artifacts/replays/` | 模型验证和账本重放结果 |
| `artifacts/maintenance/` | 工程验证、迁移和回收记录 |
| `docs/` | 方法、架构、复现说明及来源材料 |
| `reports/latest/` | 可重建的HTML报告和净值图 |
| `scripts/` | PowerShell运行、检查和同步入口 |

## 开发

```powershell
scripts/check.ps1
scripts/sync.ps1 -Message 'refactor: describe the change'
```

同步脚本依次检查、提交、推送并等待CI。CI运行格式、静态检查及合成数据测试；完整历史验证需本地LFS数据。涉及数值或成交逻辑的修改必须重放冻结账本。

[代码约定与参考实现](CONTRIBUTING.md) · [架构](docs/architecture.md) · [方法](docs/methodology.md) · [仓库维护](docs/repository.md) · [本地报告](reports/latest/index.html) · [变更记录](CHANGELOG.md)
