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

相同包版本不保证相同数值实现。环境锁验收时，通用Windows和Linux环境的NumPy 2.3.4使用OpenBLAS，首个历史模型曾出现约4.47×10⁻⁸的预测差异；严格校验拒绝通过。后续推理修复见下节，原失败仍保留。研究锁固定原来的NumPy/MKL及其他数值依赖，不修改预测、模型或误差门槛；跨平台工程测试不能替代完整历史回归。

[环境验收](../artifacts/checks/environment_lock_2026-11-22/completion.json)记录安装、构建、测试及失败范围；[独立全量回归](../artifacts/checks/environment_lock_2026-11-22/research/result.json)覆盖48个模型、1,425个历史账户及75个纸面账户，与原环境完全一致。

离线安装同样从锁文件导出全部所需依赖，校验轮子哈希后使用 `uv pip sync --require-hashes --no-index --find-links <wheelhouse>`。构建依赖也须提供锁定的Setuptools和wheel。更新依赖后重新验证模型和完整账户，保留旧环境与失败记录。

依据：[uv锁定安装](https://docs.astral.sh/uv/concepts/projects/sync/)、[uv构建依赖](https://docs.astral.sh/uv/concepts/projects/build/)、[Conda显式环境](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html#building-identical-conda-environments)、[NumPy构建信息](https://numpy.org/doc/stable/reference/generated/numpy.show_config.html)。

## 离线示例

[offline_backtest.py](../examples/offline_backtest.py)直接调用`simulate`、`reconcile`和`execution_audit`，不读取项目配置或冻结研究输入。输入为7只虚构证券、80个虚构工作日的价格，20日动量信号在次日开盘执行，覆盖59个模拟账户日。它演示API契约，不创建新的策略候选或真实订单。

按首页安装后运行：

```sh
uv run --no-sync python examples/offline_backtest.py --output artifacts/examples/offline_demo
uv run --no-sync python -c "import json; from pathlib import Path; print(json.loads(Path('artifacts/examples/offline_demo/checks.json').read_text()))"
uv run --no-sync python -c "from quantfolio.storage import load_gzip; x = load_gzip('artifacts/examples/offline_demo/ledger.json.gz'); print(x['trades'][:2])"
```

`bars.csv`保存虚构行情，`inputs.json`说明来源与日历，`signals.json`列出信号及执行日期，`summary.json`为该示例的绩效，`ledger.json.gz`含逐日账户和成交。`checks.json`中的账户核算与执行检查均须为`PASS`。输出目录只写一次；换目录可重跑，不覆盖历史文件。CI还使用安装的wheel在检出目录外执行同一示例。

## 预测数值一致性

云端逐阶段对照确认：48个模型的输入、补值、标准化、参数及所记录的数学库文件哈希一致；24个树模型完全一致，24个Ridge模型在最后的矩阵向量乘法处出现差异。MKL日志显示本机使用AVX2分支，AMD托管机使用通用处理器分支。原始失败及[对照记录](../artifacts/checks/prediction_diagnostics_2026-11-22/cloud_comparison.json)保留；限制指令集不能代替跨处理器验证，见[Intel说明](https://www.intel.com/content/www/us/en/docs/onemkl/developer-guide-windows/2025-0/get-started-with-conditional-num-reproducibility.html)。

`models.predict_model`对float32、单输出Ridge固定八路乘加及最终求和顺序，保留历史计算的舍入结果。float32乘积在float64中精确表示；加法舍入落在float32中点时，用加法残差决定最终舍入方向。其余模型、float64及多输出格式沿用估计器原入口。预处理、模型构造、参数和冻结文件均不变；该实现不读取历史预测生成输出。

训练流程与模型重载验证共用此推理入口。诊断同时记录项目入口和估计器原生调用的结果，原生差异不会被删去。最终实现通过独立C运行库的297,989组乘加对照，其中1,446组专门覆盖舍入中点；float32溢出边界为`2**128 - 2**103`，也参与残差修正。首次溢出中点失败及中断的回归另存，未作为通过证据。全部24个冻结Ridge模型用于检查历史结果；精确预测仍须结合完整账户回归验收，不从模型检查推断收益或账本一致。

这项修复针对已有模型的推理。它不保证重新训练在任意数学库或处理器上产生相同系数，也不恢复研究；历史研究环境锁继续保留。

Ridge修复后的本地Intel和托管AMD完整历史回归均通过。两端48份预测及历史账户、纸面账户对照文件逐字节一致，全部校准和冻结窗口结果一致。该轮[跨环境对照](../artifacts/checks/portable_predictions_2026-11-23/cloud_comparison.json)另发现24组重算训练权重哈希不同，其他拟合输入字段一致；这些权重未用于重新拟合。权重差异的定位见下节，原失败记录保留。

通用OpenBLAS开发环境中的48个模型也通过了精确预测检查，但其完整回归在历史校准处被拒绝：该环境缺少Bottleneck，Pandas标准差采用了不同的归约实现。在原环境中关闭Bottleneck可复现相同差值，见[环境诊断](../artifacts/checks/portable_predictions_2026-11-23/environment_diagnosis.json)。正式历史验收继续使用已包含Bottleneck 1.4.2的研究锁，未改动校准公式或接受该通用环境的完整结果。

## 训练权重数值一致性

24组拟合输入的逐步对照将首个差异定位到时间衰减中的`np.exp2`。两台托管机均为AMD EPYC 7763，Windows 2022的衰减值与本地Windows 11最多相差2个浮点末位；Windows 2025与原值一致。样本、行业计数、指数输入和基础权重均相同，差异随归一化进入全部24组权重。原始数组、运行库哈希和逐组对照见[定位记录](../artifacts/checks/training_weights_2026-11-23/root_cause.json)。

NumPy 2.3.4的[`npy_exp2`映射到C运行库的`exp2`](https://github.com/numpy/numpy/blob/v2.3.4/numpy/_core/include/numpy/npy_math.h)。Windows UCRT随系统提供，其数学函数可能因操作系统版本产生末位差异，见[Microsoft说明](https://learn.microsoft.com/en-us/cpp/c-runtime-library/floating-point-support?view=msvc-170)。本次记录的三套UCRT文件哈希不同；切换本机FMA3开关没有复现云端差异。修复复核时Windows 2022分配到Intel Xeon 8370C，其UCRT和全部24组原生权重哈希仍与此前AMD实例一致；项目权重则全部匹配原历史值，见[复核记录](../artifacts/checks/training_weights_2026-11-23/cloud_windows_2022_receipt.json)。

`training_targets`仍计算`2**(-age/126)`，改用`math.pow(2.0, exponent)`构造float64衰减数组。行筛选、计数、半衰期、乘法与归一化顺序保持。所有被记录的指数输入上，三个环境中的`math.pow`均复现原本地`exp2`位模式；本地研究环境、独立OpenBLAS安装包及两种托管系统的24组实际权重均按原哈希通过。生产函数不读取冻结权重或诊断数组；不重新拟合模型。[本地完整验收](../artifacts/checks/training_weights_2026-11-23/local_acceptance.json)同时确认全部48个模型、1,425个历史账户、114份账本和75个纸面账户保持原结果。

[跨环境对照](../artifacts/checks/training_weights_2026-11-23/cloud_comparison.json)保存四套环境的24组原哈希和两台修复后托管机的48份预测数组。Windows 2025完整回归耗时59.5分钟；其`differential.json`、`history.json`和`paper.json`与本地逐字节一致，包括原先不同的训练权重。全部48个模型、1,425个账户、114份账本及75个纸面账户通过，新增研究拟合为0。

| 环境 | 24组当前权重 | 完整历史验收范围 |
| --- | --- | --- |
| Windows 11 / MKL | 原哈希全部一致 | 通过 |
| Windows 11 / 独立OpenBLAS wheel | 原哈希全部一致 | 本轮仅验证权重；缺少Bottleneck的完整校准限制保留 |
| Windows 2022 / Intel Xeon 8370C | 原哈希全部一致 | 冻结旧Git基线的权重比较失败 |
| Windows 2025 / AMD EPYC 7763 | 原哈希全部一致 | 通过；三份财务对照文件与本地逐字节一致 |

权重诊断始终与修复前已冻结的`portable_inference_full_2026-11-23/differential.json`比较，不更新参考哈希。`weights`为当前项目输出；`decay`和`native_weights`有意保留原`exp2`路径，作为环境差异对照。两者不同不会被抹去，正式通过条件仍是行号、目标和实际权重全部精确匹配。

完整回归还要求当前代码与冻结Git基线在同一环境内精确相等。旧基线在Windows 2022上仍调用有差异的`exp2`，因此修复后的权重不能同时等于旧环境值与原历史值。该环境的完整复核在`training_replay/fit_inputs/0/weights_sha256`按原门槛[失败](../artifacts/checks/training_weights_2026-11-23/cloud_windows_2022/ci-regression-45102297164/result.json)。原基线和这一检查均保留，托管完整验收明确使用`windows-2025`，`windows-2022`保留诊断和失败对照。环境标签不是数值保证，每次仍须执行全部验收；本次修复也不认证跨环境重新训练所得的模型系数。

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
