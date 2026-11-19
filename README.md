# Quantfolio

[![Research code checks](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml/badge.svg)](https://github.com/Aaa312-tech/Quantify/actions/workflows/ci.yml)

维护仓库：[Aaa312-tech/Quantify](https://github.com/Aaa312-tech/Quantify)。源码、配置、数据、模型、报告与研究证据一并同步，数据包和模型文件通过Git LFS管理。

面向A股模拟投资研究的 Python 项目。当前只维护 **V6**：均衡进攻、8%回撤预警、非恢复仓位机制。源码、配置、数据、训练产物和报告各归其位，历史版本退出运行目录。

**研究状态：收益优势验证仍未通过。** V6是既有主策略中基础成本表现最好的保留版本；主策略与同风控动量相同，尚未证明选股模型有稳定增益。更高收益的事后单项试验没有被改称“最强策略”。

| 基础成本档：每边10bp滑点，另计交易费用 | V6 | 同风控动量 | 冻结V4不恢复版 |
| --- | ---: | ---: | ---: |
| 连续账户净收益 | 8.11% | 8.11% | 7.78% |
| 连续账户最大回撤 | -6.55% | -6.55% | -6.73% |
| 完整10日账户平均净收益 | 2.00% | 2.00% | 1.85% |

连续账户区间为2025-11-18至2026-11-02；数据截止2026-11-18。10日账户有23个完整样本和1个尾部样本。30bp成本档连续净收益为 **-4.85%**。8%是预警和退出目标，不是最大损失保证。

[查看本地报告](reports/latest/index.html) · [保留依据](docs/references/version_selection.json) · [复现指南](docs/reproducibility.md) · [整理记录](docs/maintenance/reorganization.md)

## 快速开始

已验证环境：Windows、Python 3.13.2。依赖的精确版本统一写在 `pyproject.toml`。

从GitHub获取完整项目（需要安装Git和Git LFS）：

```powershell
git lfs install
git clone https://github.com/Aaa312-tech/Quantify.git
Set-Location Quantify
git lfs pull
python -m pip install -e '.[dev]'
python -B -m quantfolio verify
```

如果直接下载GitHub的源码ZIP，数据和模型可能只有LFS指针；使用上述克隆流程取得实际文件。冻结证据禁止换行转换，完整性清单用于检查克隆内容。

```powershell
Set-Location -LiteralPath 'D:\投资组合量化'
& 'D:\conda\python.exe' -m pip install -e '.[dev]'
& 'D:\conda\python.exe' -B -m quantfolio status
& 'D:\conda\python.exe' -B -m quantfolio verify
& 'D:\conda\python.exe' -B -m quantfolio report
```

也可以用项目自己的虚拟环境安装。所有命令均离线读取本地研究材料，不连接交易终端。更换工作目录后使用 `--project 'D:\投资组合量化'`，或设置 `QUANTFOLIO_ROOT`。

当前电脑已有数值依赖时，无需安装本项目即可用 `scripts/run.ps1 -Python 'D:\conda\python.exe' status` 从源码启动。

```powershell
# 精确重放全部19项策略、3档成本；新名称避免覆盖已有结果。
python -B -m quantfolio backtest --output artifacts/replays/check_2026-11-20

# 按固定V6协议重新训练。默认覆盖11个历史窗口；可选择部分窗口研究。
python -B -m quantfolio train --first 1 --last 11 --include-latest `
  --output artifacts/experiments/refit_2026-11-20
```

`--include-latest` 指现有数据截止日，不会自动获取新行情。训练输出写入新目录；参考产物保持冻结。

## 目录

```text
投资组合量化/
├── README.md                  项目入口与真实验证状态
├── pyproject.toml             打包、依赖、pytest与Ruff配置
├── CONTRIBUTING.md            命名、代码、文档与提交规范
├── CHANGELOG.md               维护记录
├── NOTICE.md                  来源和分享边界
├── src/quantfolio/            唯一活动Python包
├── configs/                   冻结实验说明与SHA-256清单
├── tests/                     合成数据上的核心不变量测试
├── scripts/                   本地质量检查入口
├── docs/                      方法、架构、复现、维护记录
│   └── references/            比较摘要、原始材料、公告证据
├── data/processed/2026-11-18/  自包含数据快照
├── artifacts/
│   ├── v6_2026-11-19/         保留的V6完整训练和回测证据
│   ├── checks/               模型重载与重新训练验证
│   └── replays/              整理后的回测重放检查
└── reports/latest/            本地HTML报告和净值图
```

`artifacts/v6_2026-11-19` 中保留的试验、旧测试日志及历史路径字符串是冻结实验的证据，不是旧版可执行代码。运行时只使用新项目的相对路径和完整性清单。V4只有比较数据，没有活动的旧版代码或模型。

## 开发检查

```powershell
python -m ruff format --check src tests
python -m ruff check src tests
python -B -m pytest -q
```

或运行 `scripts/check.ps1`。每次推送会触发GitHub Actions的格式、静态检查和合成数据测试；实际结果以顶部状态标识和对应提交的运行记录为准。CI默认不下载LFS数据，完整模型和账本验证按复现指南在完整克隆上执行。

后续修改遵循“检查 → 提交 → 推送 → 核对CI”的流程，可使用 `scripts/sync.ps1`。具体同步规则见 [仓库维护指南](docs/repository.md)。

本次整理参考 [Qlib](https://github.com/microsoft/qlib)、[Backtesting.py](https://github.com/kernc/backtesting.py) 和 [pysystemtrade](https://github.com/pst-group/pysystemtrade) 的模块划分、项目元数据和维护方式。具体采用内容见 [参考记录](docs/references/github_conventions.md)。
