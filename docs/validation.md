# 验证

验证分为代码检查、冻结输入核对、模型重载和账户对照。测试通过说明实现符合已检查的行为，不代表策略有超额收益。

## 日常检查

Windows：

```powershell
scripts/check.ps1 -Python '.\.research\python.exe'
```

Linux或已激活环境：

```sh
python -m ruff format --check src tests scripts
python -m ruff check src tests scripts
python -m mypy
mkdir -p quality-results
python -m coverage run -m pytest -q
python -m coverage json -o quality-results/coverage.json
python scripts/check_coverage.py quality-results/coverage.json
```

CI在Windows / Python 3.11、Windows / Python 3.13和Ubuntu / Python 3.13按`uv.lock`安装依赖，用固定构建后端生成并安装wheel，再于源码目录外检查导入和模板。测试使用合成输入，不在CI下载完整研究数据。现有历史模型的精确数值复现另使用[研究环境](reproducibility.md#运行环境)，不能由这些合成测试推断。

## 分层工程检查

`scripts/check.ps1`依次执行格式、lint、类型、单元与集成测试、覆盖率比较和缺陷注入。结果写到新的`quality-results`子目录，可用`-Output`指定未使用的本地目录；检查失败即返回非零。Linux命令将输出写到`quality-results`，缺陷检测另运行`python scripts/check_faults.py --output <unused-directory>`。

`integration`标记覆盖固定样本的信号、开盘、账户结转和拒绝路径。CI分开执行`-m 'not integration'`与`-m integration`，合并覆盖结果后检查[覆盖率基线](../artifacts/checks/quality_baseline_2026-11-22/baseline.json)。`configs/coverage.toml`绑定基线哈希，逐模块检查语句和分支比例；缺少模块、未登记的新模块或降低任一比例均失败。模块拆分时只能明确列出共同承接原职责的文件，不能直接下调基线。

`check_faults.py`先运行未修改代码的控制测试，再分别注入未来价格读取、元/分换算错误、漏记成交和省略输入哈希检查。只有实际断言失败才算检出，导入或收集错误不能算成功。副本放在临时目录，原源码和历史产物不改写。

类型检查采用[渐进迁移](https://mypy.readthedocs.io/en/stable/existing_code.html)，当前显式检查16个模块：`settings`、`storage`、`models`、`allocation`、`ledger`、`accounts`、`risk`、`simulation`、`planning`、`validation`、`features`、`industries`、`dataset`、`status_data`、`funds`、`evaluation`。预测、报价、仓位、成交、账户和风险使用具体契约；运行器和账户调用方受检查，包内`py.typed`同时提供安装包的类型信息。测试确认六种非法金融字段赋值会被mypy拒绝。

这些模块中的原始快照、来源元数据、诊断结果和未迁移的函数参数仍保留动态字典；不声称已完整类型化。只有缺少类型元数据的`scipy.*`、`sklearn.*`、`joblib`、`threadpoolctl`、`baostock`设置精确的外部导入豁免，不忽略本项目模块错误。Decimal非空求和及已校验的字段使用不转换数值的`cast`；旧模型、预测和金额仍由完整回归检查。

尚未显式列入检查入口的业务模块为：`calibration`、`cli`、`daily_sources`、`disclosures`、`distributions`、`feasibility`、`industry_sources`、`market`、`market_data`、`metrics`、`preprocessing`、`refresh`、`regression`、`reporting`、`rules`、`selection`、`snapshots`、`status_repair`、`study`、`training`、`verification`；另有`__init__`和`__main__`两个包入口。存储层的`Any`限于未知JSON及NumPy标量的序列化边界。

初次契约CI在安装的wheel环境中发现20处传递导入诊断，编辑安装仅检查原9个指定模块时未报告这些问题。[失败记录](../artifacts/checks/contract_ci_failed_2026-11-22/run.json)保留了三个作业的原始输出。补齐声明并把相关模块明确列入检查后，编辑安装和独立wheel均通过16模块检查；没有设置项目模块的忽略规则。基金来源历史新增三个控制模式的可用时间与未知区间场景，语句和分支覆盖率门槛再次通过，见[修复记录](../artifacts/checks/responsibility_checks_2026-11-22/type_check_resolution.json)。

完整历史回归的[手动工作流](../.github/workflows/regression.yml)使用Windows研究锁，取得注册范围的LFS输入后执行`regression --scope full`，不拟合模型、不追加兼容登记。手动运行时选择待验收分支；报告的`run.json`绑定实际提交和源码哈希。常规CI及完整回归的结果均上传至对应Actions运行页，保留14天；正式验收另归档到`artifacts/checks`。没有定时运行。

该工作流先运行`scripts/diagnose_predictions.py`，逐一保存48个冻结模型的输入、补值、标准化、系数和预测指纹，以及项目入口的重算预测、实际数学库哈希和线程设置；估计器原生调用的指纹与差异另外保留。选择`scope=diagnostics`可只收集诊断；默认`full`仍执行完整回归。项目入口遇到数值不符后继续收集其他模型，最终返回失败，不覆盖模型或调整容差。诊断不替代完整验收。

本地对应命令如下，输出目录必须未使用；执行脚本按原字节随结果归档：

```powershell
.research/python.exe -B scripts/diagnose_predictions.py --output artifacts/checks/prediction_review
```

三个分层CI作业已[通过](../artifacts/checks/quality_ci_2026-11-22/completion.json)。云端AMD EPYC完整回归在冻结模型预测处失败，最大误差约5.96×10⁻⁸；限定`MKL_ENABLE_INSTRUCTIONS=AVX2`后仍失败。两次原始记录分别保存在`artifacts/checks/ci_research_2026-11-22/hosted_default`及`artifacts/checks/ci_research_avx2_2026-11-22`。本机Intel环境在相同AVX2设置下，48个冻结模型的误差均为0。指令集限制不保证跨厂商选择同一内核，见[Intel说明](https://www.intel.com/content/www/us/en/docs/onemkl/developer-guide-linux/2024-0/instruction-set-specific-dispatch-on-intel-archs.html)。

手动工作流可选择`windows-2022`或`windows-latest`托管镜像，均保持冻结预测的严格比较；操作系统标签不承诺CPU型号。两种镜像均出现上述差异，Windows 2022记录另保存在`artifacts/checks/ci_research_windows2022_2026-11-22`。新增的[逐阶段对照](../artifacts/checks/prediction_diagnostics_2026-11-22/cloud_comparison.json)将差异定位到Ridge原生归约。固定项目推理顺序后，本地Intel及托管AMD EPYC 7763的完整回归均通过；[托管结果](../artifacts/checks/portable_predictions_2026-11-23/cloud/ci-regression-45046246783/result.json)覆盖全部原验收范围，耗时60.3分钟。原失败保留，当前没有常驻研究运行器。

## 当前结果的复核

```powershell
python -m quantfolio verify --run artifacts/experiments/recipient_entitlements_2026-11-22
```

该命令校验冻结文件、特征和训练身份，重载48个已有模型并比较预测，不进行新拟合。若需保存回执，另加 `--output artifacts/checks/model_review`，目录必须尚不存在。不带 `--run` 的 `verify` 检查旧冻结参考；它不能替代当前实验验证。

```powershell
python -m quantfolio verify-paper --output artifacts/checks/paper_review
```

纸面验证把当前评估的75个主策略历史账户、1386个交易日通过计划与账户流程重放，比较完整账本。这些是历史集成检查，不是前向交易。

## 标准工程回归

工程基线由[regression.toml](../configs/regression.toml)绑定到Git提交、原环境验收记录、输入清单及归档名称配置。命令自动从Git准备隔离的旧实现，检查本地LFS对象，再用已有模型和预测分别运行旧、新代码。无需保留开发机的临时基线目录。

```powershell
python -m quantfolio regression --scope core --baseline 2443df760246600710c7b3f0bed3e2e9bf46e9e8 --output artifacts/checks/core_review
python -m quantfolio regression --scope full --baseline 2443df760246600710c7b3f0bed3e2e9bf46e9e8 --output artifacts/checks/full_review
```

| 检查范围 | `core` | `full` |
| --- | --- | --- |
| 冻结模型、训练窗口、拟合输入 | 4 / 1 / 2 | 48 / 12 / 24 |
| 原始价格特征 | 前10只证券 | 全部594只证券 |
| 衍生特征、行业计算和时间前缀 | 全量 | 全量 |
| 历史账户、压缩账本 | 主策略10bp：25 / 2 | 19项政策、三档成本：1,425 / 114 |
| 纸面账户、交易日 | 前2个账户：2 / 20 | 三档成本：75 / 1,386 |

两种范围均检查完整输入清单和加载后的数据。`core`缩小计算范围，不能替代发布验收。`full`要求全部范围一致，模型预测误差为0；比较字段顺序、数组类型、标签、权重、历史校准、选择结果及压缩预测和账本字节。拟合输入权重与旧实现的比较在同一运行环境内进行，不代表跨环境重新训练相等。历史校准按原窗口重算，模型拟合次数必须为0。

输出目录必须未使用。`run.json`记录命令、环境、Git提交和实际源码哈希；`result.json`记录完成状态、范围和失败位置；`differential.json`、`history.json`、`paper.json`保存分项证据，`run.log`保存进度。运行失败返回非零状态，已有文件不覆盖；旧、新实现产生的重复临时账本在比较后清理。

默认只诊断，不改变正常加载守卫。需要登记等价源码时，在上述全量命令中显式加 `--register`：全部检查通过且输入和源码再次核对后，追加 `source_compatibility.json`及完整性清单。小范围、失败、缺项或源码变化均不能登记。原训练协议、计划身份及39,071项历史冻结文件保持。

干净克隆按[安装说明](../README.md#安装与验证)下载LFS对象即可运行。浅克隆还需 `git fetch origin 2443df760246600710c7b3f0bed3e2e9bf46e9e8`。缺少输入或LFS对象时先恢复指定文件，再使用新输出目录重试。跨平台精确复现须另有该环境的模型和完整回归证据，不能从单机通过结果推断。

## 已登记的等价证据

| 范围 | 结果 | 记录 |
| --- | --- | --- |
| 模型 | 48个模型重载，最大预测误差0 | [全量回归](../artifacts/checks/portable_inference_full_2026-11-23/result.json) |
| 历史账户 | 1425个完整账户及114份压缩账本精确一致 | [历史对照](../artifacts/checks/portable_inference_full_2026-11-23/history.json) |
| 纸面账户 | 75个账户、1386个交易日精确一致 | [纸面验证](../artifacts/checks/portable_inference_full_2026-11-23/paper.json) |
| 特征与选型 | 全部594只证券特征、24组拟合输入及12个冻结窗口与同环境旧实现精确一致；无模型重训 | [计算对照](../artifacts/checks/portable_inference_full_2026-11-23/differential.json) |
| 报告 | 默认、指定实验和历史参考入口的HTML及PNG逐字节一致 | [渲染对照](../artifacts/checks/responsibility_checks_2026-11-22/report_render/result.json) |
| 独立环境 | 锁定Windows研究环境和隔离新克隆的全量回归与原环境一致；跨平台安装及测试另行验收 | [环境验收](../artifacts/checks/environment_lock_2026-11-22/completion.json) |
| 开盘与核账 | 192组场景、2424个纸面交易日、38组拒绝路径；三档成本完整文件链路一致 | [差分对照](../artifacts/checks/paper_refactor_2026-11-22/differential.json) |
| 仓位与风控 | 29007组状态、1024组优化器输入及完整模拟对照 | [执行验证](../artifacts/checks/execution_refactor_2026-11-22/completion.json) |
| 训练与评估 | 12个冻结窗口以缓存重放，选型与历史结果一致 | [流程验证](../artifacts/checks/runtime_refactor_2026-11-22/completion.json) |

当前代码通过819项单元测试与71项集成测试，1项因本机符号链接权限跳过；16个模块的类型检查、39个模块的覆盖率门槛与四项缺陷检测通过，详见[本地验收](../artifacts/checks/portable_predictions_2026-11-23/local_acceptance.json)。独立安装的OpenBLAS wheel通过48个冻结模型的精确预测检查；轮包中的39个Python模块与完整回归源码逐字节一致。其完整回归因缺少Bottleneck而在历史校准处失败，不能作为完整金融验收。舍入边界初次失败及中断记录保留；修复后使用新输出验证。各记录仅证明其绑定源码、数据与协议下的结果，新的代码改动不能直接继承旧结论。

此前职责拆分源码的三个CI作业及106个发布文件核对通过，见[历史发布核验](../artifacts/checks/responsibility_checks_2026-11-22/publication.json)。新增诊断入口的三个CI作业和130个发布文件核对也通过，见[诊断发布核验](../artifacts/checks/prediction_diagnostics_2026-11-22/initial_publication.json)。这些记录绑定各自提交，不替代本次数值修复的云端验收。

本次数值修复的三个CI作业及695个发布文件的独立远端字节核对通过，见[源码发布核验](../artifacts/checks/portable_predictions_2026-11-23/source_publication.json)。托管完整回归使用同一提交和39个源码哈希。下载产物的SHA-256与GitHub记录一致；48份重算预测、历史账户和纸面账户对照文件与本地逐字节相同。额外跨环境比较发现24组重算训练权重哈希不同，其他拟合输入字段、冻结窗口、校准与历史结果一致；该差异未被转成“全部计算跨平台一致”，见[完整对照](../artifacts/checks/portable_predictions_2026-11-23/cloud_comparison.json)。

E2锁定环境验收时，Linux安装包通过835项测试，Windows通过834项、跳过1项本机权限检查。通用OpenBLAS环境的模型误差约4.47×10⁻⁸，未通过历史数值验收；这些失败未被放宽门槛或重训覆盖。精确复现使用[历史研究环境](reproducibility.md#运行环境)。

首次环境锁发布的CI在工作流校验阶段失败，未启动作业；任务级环境变量使用了该层不支持的`runner`上下文。修复后改用`github.workspace`的同级环境目录，保留[失败记录](../artifacts/checks/environment_ci_2026-11-22/workflow_failure.json)。上下文作用域以[GitHub说明](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)为准。

## 源码与历史身份

训练协议和纸面计划保留生成时的源码哈希。等价重构通过单独冻结的 `source_compatibility.json` 关联原文件身份、当前全部Python源码、差分验证和完整历史账本验证，不修改原协议。

缺少登记、哈希不符、证据不完整或引用越出项目时拒绝使用。模型输入、日期、预测和计划到期仍单独检查。活跃登记见[源码兼容记录](../artifacts/checks/portable_inference_full_2026-11-23/source_compatibility.json)。

更改数值、时序、成交或持仓行为时，须重新完成对应模型和完整账户验证，并保留失败记录。验证脚本一经执行即作为证据保存；重试使用新文件，不能覆盖原结果。
