# Quantify

[![Checks](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml/badge.svg)](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml)

A股组合研究项目。使用滚动训练和验证选择动量排序及行业残差模型，在下一交易日开盘模拟调仓，独立核对成交、现金和持仓。Python包及命令名为 `quantfolio`。

当前策略为滚动选型动量，采用均衡进攻、8%回撤预警和降仓后不恢复。研究暂停，仓库继续维护；当前模型尚未显示优于同风控动量的收益，亦无前向收益结论。

## 回测

数据截止2026-11-20，连续账户区间为2025-11-18至2026-11-02，共231个交易日。初始资金100万元。

| 每边滑点 | 策略净收益 | 同风控动量 | 最大回撤 |
| --- | ---: | ---: | ---: |
| 10bp | 35.93% | 35.93% | -7.59% |
| 20bp | 3.38% | 3.38% | -6.30% |
| 30bp | 11.79% | 11.79% | -7.94% |

另计佣金、过户费和印花税。各档重新计算订单与风险路径，收益不必随成本单调变化。8%是预警和退出目标，不保证实际回撤上限。股票池、历史信息可用性及基金完整经济敞口仍有数据限制。

![策略与同风控动量的连续账户净值](reports/experiments/recipient_entitlements_2026-11-22/equity_curve.png)

[研究报告](reports/experiments/recipient_entitlements_2026-11-22/index.html) · [评估数据](artifacts/evaluations/recipient_entitlements_2026-11-22/evaluation_summary.json) · [方法与限制](docs/methodology.md)

## 安装与验证

复核现有冻结模型使用Windows x64、Conda及Git LFS。研究环境固定原NumPy/MKL构建；通用开发环境的Windows、Linux安装方式见[复现指南](docs/reproducibility.md#运行环境)。

```powershell
git lfs install
git clone https://github.com/Aaa312-tech/Quantify.git
Set-Location Quantify
git lfs pull
python -m pip install uv==0.13.9
conda create --prefix .research --file environments/research-win-64.lock --yes
uv export --locked --all-extras --no-emit-project --python .research/python.exe --output-file .research/requirements.txt
uv pip install --python .research/python.exe --require-hashes --no-deps -r .research/requirements.txt
uv pip install --python .research/python.exe --no-deps -e .
.\.research\python -m quantfolio status
.\.research\python -m quantfolio verify --run artifacts/experiments/recipient_entitlements_2026-11-22
```

源码ZIP可能只含LFS指针，模型与数据须完整下载后再验证。上述命令重载已有模型，不重新拟合。完整的特征、训练窗口和账户对照使用[标准回归命令](docs/validation.md#标准工程回归)。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `src/quantfolio/` | 数据处理、训练、组合、回测、纸面账户和报告 |
| `tests/` | 数据、来源、组合和工作流测试 |
| `configs/` | 活跃配置、研究协议和完整性清单 |
| `environments/` | 历史研究环境的二进制依赖锁 |
| `data/` | 原始来源与处理后的快照 |
| `artifacts/` | 模型、评估、账户及验证记录 |
| `reports/` | 由评估数据生成的阅读报告 |
| `docs/` | 使用、方法、数据契约及研究记录 |
| `scripts/` | 本地检查入口 |

运行参数以 [project.toml](configs/project.toml) 为准。数据和实验按用途、日期归档；历史记录不参与当前配置的自动选择。

[文档](docs/index.md) · [当前状态](docs/status.md) · [开发约定](CONTRIBUTING.md) · [变更记录](CHANGELOG.md) · [使用与来源说明](NOTICE.md)
