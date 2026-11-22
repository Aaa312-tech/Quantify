# 复现指南

命令在项目根目录执行，`python`指安装了本项目的环境。安装步骤见[首页](../README.md#安装与验证)。当前研究暂停；验证已有结果可以执行，新采集、训练和研究需明确恢复后再进行。

## 运行环境

| 用途 | 环境文件 | 验收范围 |
| --- | --- | --- |
| 开发和合成测试 | `uv.lock`，支持Python 3.11–3.13 | Windows、Linux的安装、wheel、入口、模板和测试 |
| 现有历史模型与完整账户复核 | `environments/research-win-64.lock`加`uv.lock`中的Python依赖 | Windows x64、Python 3.13.2、原NumPy/MKL构建 |

开发环境使用uv 0.13.9：

```sh
python -m pip install uv==0.13.9
uv sync --locked --all-extras
uv run --no-sync python -B -m pytest -q
```

`.python-version`选择3.13.2；检查其他受支持版本时，安装和运行都显式传入 `--python <version>`。`--locked`要求项目配置与锁文件一致，常规安装不更新锁。构建后端单独固定为Setuptools 78.1.1和wheel 0.45.1，构建入口为 `uv build`。CI通过 `UV_PYTHON=python`使用矩阵指定的解释器，先安装锁定依赖，再安装生成的wheel，于源码目录外检查导入和模板。

历史研究环境按[首页命令](../README.md#安装与验证)创建在 `.research`。Conda显式锁固定Python、NumPy、Pandas、MKL、NumExpr、Bottleneck及其二进制依赖的构建和SHA-256；pip 26.1.1单独作为安装工具固定。其余Python依赖由 `uv export --locked`导出带哈希要求，再用 `uv pip install --require-hashes --no-deps`补齐。保留Conda提供的相同版本包，使用本地wheel或可编辑安装加载项目代码。不要对该混合环境运行通用的 `uv sync`，以免替换二进制构建或移除所需计算库。

相同包版本不保证相同数值实现。本次通用Windows和Linux环境的NumPy 2.3.4使用OpenBLAS，首个历史模型出现约4.47×10⁻⁸的预测差异；严格校验拒绝通过。研究锁保留原来的NumPy/MKL二进制，不修改预测、模型或误差门槛。跨平台测试通过只证明其列出的工程范围；现有模型的精确复现以研究环境的完整回归记录为准。

[环境验收](../artifacts/checks/environment_lock_2026-11-22/completion.json)记录安装、构建、测试及失败范围；[独立全量回归](../artifacts/checks/environment_lock_2026-11-22/research/result.json)覆盖48个模型、1,425个历史账户及75个纸面账户，与原环境完全一致。

离线安装同样从锁文件导出全部所需依赖，校验轮子哈希后使用 `uv pip sync --require-hashes --no-index --find-links <wheelhouse>`。构建依赖也须提供锁定的Setuptools和wheel。更新依赖后重新验证模型和完整账户，保留旧环境与失败记录。

依据：[uv锁定安装](https://docs.astral.sh/uv/concepts/projects/sync/)、[uv构建依赖](https://docs.astral.sh/uv/concepts/projects/build/)、[Conda显式环境](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html#building-identical-conda-environments)、[NumPy构建信息](https://numpy.org/doc/stable/reference/generated/numpy.show_config.html)。

## 配置

[project.toml](../configs/project.toml)是默认入口，指定行情快照、注册协议、评估目录、历史状态审查及完整性清单。项目路径可由全局参数或环境变量指定：

```powershell
python -m quantfolio --project 'D:\投资组合量化' status
```

`QUANTFOLIO_ROOT`具有相同用途。独立实验使用 `--config configs/experiments/<name>.toml`，配置必须位于项目的 `configs` 内。新输出不会自动替换活跃配置。

## 核对当前结果

```powershell
python -m quantfolio status
python -m quantfolio verify --run artifacts/experiments/recipient_entitlements_2026-11-22
```

`status`展示当前配置与研究状态。`verify --run`检查完整性、特征、训练身份、模型预测和标签时间边界；没有 `--refit` 时只重载已有模型。当前48个模型的验证及完整历史账户对照见[验证说明](validation.md)。

保存新的模型检查回执：

```powershell
python -m quantfolio verify --run artifacts/experiments/recipient_entitlements_2026-11-22 --output artifacts/checks/model_review
```

输出目录须未使用。不要复用示例目录覆盖已有检查。

工程维护使用统一的 `regression`命令，自动准备Git历史实现，比较已有模型、特征、训练输入、选择结果及全部账户，不执行新拟合。小范围与全量命令、所需文件及兼容登记方式见[标准工程回归](validation.md#标准工程回归)。

## 训练与评估

以下为恢复研究后的操作方式。先固定输入、候选范围和协议，再运行：

```powershell
python -m quantfolio train --include-latest --output artifacts/experiments/replication
python -m quantfolio evaluate --run artifacts/experiments/replication --all-policies --output artifacts/evaluations/replication
python -m quantfolio verify --run artifacts/experiments/replication --output artifacts/checks/replication
python -m quantfolio report --run artifacts/evaluations/replication
```

- 默认训练覆盖所有可用完整窗口，当前为11个。`--first`、`--last`可限制范围；部分窗口输出标为 `PARTIAL`。
- `--include-latest`额外拟合截止日模型，不将它加入历史外层测试，也不采集行情。
- 评估默认覆盖10、20、30bp每边滑点；`--cost 10`可限定单档，`--all-policies`加入19项换手规则对照。
- 评估先检查训练完成状态、数据及源码身份、预测行和时间覆盖。训练与评估目录一经存在即拒绝覆盖。

运行失败时保留诊断输出，修正后使用新目录。固定旧预测的 `execution-bridge`、`data-bridge`用于归因，不能替代修正数据后的完整训练；注册研究见[策略对照](strategy_study.md)和[数据专题](index.md#研究记录)。

## 输出

| 产物 | 内容 |
| --- | --- |
| `artifacts/experiments/<name>/` | 协议身份、训练状态、每窗口拟合、选型、模型与预测 |
| `artifacts/evaluations/<name>/` | `evaluation_summary.json`及逐笔、逐日压缩账本 |
| `artifacts/checks/<name>/` | 独立检查回执与执行证据 |
| `reports/experiments/<name>/` | HTML报告和净值图 |

`report --run`读取对应评估的成本、区间、样本及账本哈希，不重新回测。报告路径由评估目录名确定；生成前须确认目的目录尚未使用，已经冻结的报告不得重写。

[当前报告](../reports/experiments/recipient_entitlements_2026-11-22/index.html)使用现有评估重新排版；原 `reports/latest/`作为冻结记录保留。省略 `--run` 的 `report`指向 `reports/latest/`，`report --reference`指向 `reports/reference/`，不要覆盖这两个历史目录。

## 纸面账户

```powershell
python -m quantfolio verify-paper --output artifacts/checks/paper_review
```

这会重放当前评估对应的75个主策略账户。人工报价、次日计划和收盘结转的文件格式及命令见[纸面账户](paper_workflow.md)；历史回放不算前向观察。

## 历史实验

不带 `--run` 的 `verify`读取 `reference_bundle`和冻结参考，仍可用于旧模型核对。它与当前实验是两个输入范围。

旧成交器的1425个账户必须用提交 `e11bb36`的代码重放，已完成记录见[原引擎验证](../artifacts/replays/prior_engine_2026-11-20/verification.json)。当前代码拒绝跨成交模型重放旧实验；`backtest`只是 `replay`的兼容别名。

各次数据修正均有独立配置和协议，见[行业](industry_timing.md)、[ETF](etf_integration.md)及[权益日期](distribution_availability.md)。逐阶段命令保存在[历史复现指南](https://github.com/Aaa312-tech/Quantify/blob/8f3bca316bfca68fa36fb7b3506af78c0fcc146b/docs/reproducibility.md)，不要将其中的旧截止日期或旧输出路径用于新的实验。

### 日期化行业对照

`configs/protocols/industry_timing.json`固定 `coverage`、`static`和 `dated`三组输入，分别检查覆盖、分类体系与可用时间。历史训练配置为 `configs/experiments/industry_dated.toml`；原数据生成与训练步骤见[登记阶段的命令](https://github.com/Aaa312-tech/Quantify/blob/8f3bca316bfca68fa36fb7b3506af78c0fcc146b/docs/reproducibility.md#日期化行业对照前一阶段)。当前快照继续使用日期化分类，并叠加后续ETF与权益修正，不能用该阶段结果替代当前评估。
