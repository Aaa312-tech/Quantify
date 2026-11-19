# 如何复现

## 安装与轻量检查

在项目目录执行 `python -m pip install -e '.[dev]'`，然后运行 `python -B -m pytest -q`。测试使用合成数据，不需要行情快照，覆盖交易时序、涨跌停、分红现金、独立核账、行业处理、换手拆解和输出隔离。`scripts/check.ps1`合并格式、静态与测试检查。

精确验证环境是Windows与Python 3.13.2，依赖版本固定在 `pyproject.toml`。包支持Python 3.11–3.13，但未声称其他解释器、操作系统或BLAS组合都通过了逐位复现。V6保留关闭pandas numexpr加速的设置，避免原环境出现的特征掩码不一致。

## 检查冻结输入和模型

```powershell
python -B -m quantfolio verify
python -B -m quantfolio verify --refit --output artifacts/checks/refit_2026-11-20
```

前者检查清单中的全部文件、行业特征历史前缀、48个模型和预测、训练标签的结束日期。后者额外在第1窗口的训练集重新拟合Ridge和树模型，并与冻结预测逐项比较；这不是重新搜索策略，也不增加独立市场样本。

## 精确重放

```powershell
python -B -m quantfolio backtest --output artifacts/replays/full_2026-11-20
```

默认重放19项策略、3档成本、每项25个账户，共1425个账户；比较完整摘要和账本，包括成交、拒单、公司行为、风控决策及日净值，并重新执行独立核账与执行审计。遇到差异即失败。只检查主策略可加 `--primary-only`；单档成本可加 `--cost 10`。

## 新训练与报告

```powershell
python -B -m quantfolio train --first 1 --last 11 --include-latest `
  --output artifacts/experiments/refit_2026-11-20
python -B -m quantfolio report
```

训练按相同V6协议重新拟合，结果进入新实验目录。部分窗口明确标为 `PARTIAL`。命令 `backtest` 当前用于冻结V6的回归验证；它不自动评估任意新实验或将新模型替换为主策略。研究新参数时需另建登记后的评估任务，不能把本轮回归通过当作新实验收益已通过。

报告固定从已保留的V6参考证据重建，不读取未验证的新实验结果。历史数据截止日期不因运行命令而更新。

## 路径与完整性

从其他目录运行：`python -m quantfolio --project 'D:\投资组合量化' status`。安装包仅包含代码；本地行情和大产物单独保存在项目根目录。可以整体迁移文件夹，运行时不依赖原盘符。

`configs/integrity_manifest.json`是新路径下的可信文件SHA-256清单。冻结产物中的旧版 `publication_manifest`、`artifact_index` 和绝对路径原样保存，仅作历史审计依据；新验证器不会沿它们查找已回收的旧文件。

不要在Python `-O` 优化模式下运行研究代码，因为继承的数值不变量使用了assert。训练输出必须使用新名称，不能手工删除失败结果再复用同名目录来掩盖错误。
