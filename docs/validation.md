# 验证

## 产物依赖

新训练、评估和纸面计划同时生成`schema_version = 2`的`run.json`。运行前后均读取内容哈希；不使用路径、文件大小、修改时间或跨运行缓存代替验证。父产物递归校验，新记录缺字段、未知格式、漏登记、路径越界、循环引用或运行中输入变化均拒绝。自包含数据快照作为冻结输入；来源事实是否合格仍由独立研究核验决定。

项目TOML和可编辑配置以原文、有效配置和SHA-256写入新记录；冻结数据、模型、预测及父产物必须在原完整性清单登记。失败运行如已产生文件，会保留`FAILED`记录；不能作为后续成功运行的父输入。既有记录按原格式读取，不改写旧证据。

手工账户在计划入口登记，手工开盘及收盘报价在相应核算成功时登记；之后修改原文件会被拒绝。登记只绑定所提交的内容，不能证明报价真实或在当时可用。后续计划会沿账户父引用核对开盘记录和收盘报价。

完整回归通过后，标准注册入口同时保留旧`source_compatibility.json`并生成`dependency_compatibility.json`。新证明核对原验收范围、具体产物身份、完整回归及其两类证据，再比较金融入口的传递源码依赖。相关规则、模型或序列化实现变化仍需精确回归，不能以新增证书绕过旧门槛。

日常训练、评估和计划只扫描其依赖。发布前仍必须运行全库检查，包括已经失败或停用的历史档案：

```powershell
.research/python.exe -m quantfolio integrity
.research/python.exe -m quantfolio runs
```

`runs`从已登记记录重建索引，列出候选、成本、父输入、配置、状态和输出，不排序选择收益。`configured`仅标识配置指向，`adopted`还要求该记录完成；索引不会改动项目配置。旧记录未保存的有效配置保留为空，不用当前配置补造历史。

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

类型检查采用[渐进迁移](https://mypy.readthedocs.io/en/stable/existing_code.html)，当前显式检查18个模块：`settings`、`storage`、`models`、`allocation`、`ledger`、`accounts`、`risk`、`simulation`、`planning`、`validation`、`features`、`industries`、`dataset`、`status_data`、`funds`、`evaluation`、`integrity`、`runs`。预测、报价、仓位、成交、账户和风险使用具体契约；运行器和账户调用方受检查，包内`py.typed`同时提供安装包的类型信息。测试确认六种非法金融字段赋值会被mypy拒绝。

这些模块中的原始快照、来源元数据、诊断结果和未迁移的函数参数仍保留动态字典；不声称已完整类型化。只有缺少类型元数据的`scipy.*`、`sklearn.*`、`joblib`、`threadpoolctl`、`baostock`设置精确的外部导入豁免，不忽略本项目模块错误。Decimal非空求和及已校验的字段使用不转换数值的`cast`；旧模型、预测和金额仍由完整回归检查。

尚未显式列入检查入口的业务模块为：`calibration`、`cli`、`daily_sources`、`disclosures`、`distributions`、`feasibility`、`industry_sources`、`market`、`market_data`、`metrics`、`preprocessing`、`refresh`、`regression`、`reporting`、`rules`、`selection`、`snapshots`、`status_repair`、`study`、`training`、`verification`；另有`__init__`和`__main__`两个包入口。存储层的`Any`限于未知JSON及NumPy标量的序列化边界。

初次契约CI在安装的wheel环境中发现20处传递导入诊断，编辑安装仅检查原9个指定模块时未报告这些问题。[失败记录](../artifacts/checks/contract_ci_failed_2026-11-22/run.json)保留了三个作业的原始输出。补齐声明并把相关模块明确列入检查后，编辑安装和独立wheel均通过16模块检查；没有设置项目模块的忽略规则。基金来源历史新增三个控制模式的可用时间与未知区间场景，语句和分支覆盖率门槛再次通过，见[修复记录](../artifacts/checks/responsibility_checks_2026-11-22/type_check_resolution.json)。

完整历史回归的[手动工作流](../.github/workflows/regression.yml)使用Windows研究锁，取得注册范围的LFS输入后执行`regression --scope full`，不拟合模型、不追加兼容登记。手动运行时选择待验收分支；报告的`run.json`绑定实际提交和源码哈希。常规CI及完整回归的结果均上传至对应Actions运行页，保留14天；正式验收另归档到`artifacts/checks`。没有定时运行。

该工作流在权重诊断后运行`scripts/diagnose_predictions.py`，逐一保存48个冻结模型的输入、补值、标准化、系数和预测指纹，以及项目入口的重算预测、实际数学库哈希和线程设置；估计器原生调用的指纹与差异另外保留。选择`scope=diagnostics`可只收集诊断；默认`full`仍执行完整回归。项目入口遇到数值不符后继续收集其他模型，最终返回失败，不覆盖模型或调整容差。诊断不替代完整验收。

本地对应命令如下，输出目录必须未使用；执行脚本按原字节随结果归档：

```powershell
.research/python.exe -B scripts/diagnose_predictions.py --output artifacts/checks/prediction_review
```

训练权重另用`scripts.diagnose_weights`检查24组冻结拟合输入，输出样本计数、指数输入、衰减因子、归一化前权重、均值及实际权重数组。参考文件必须已登记、通过完整回归且范围完整；每组行号、目标和权重按原哈希精确比较。一个浮点末位的变化也返回失败；诊断不调用估计器拟合。运行库记录包含实际加载的Windows C运行库哈希、Bottleneck和NumExpr版本。

```powershell
.research/python.exe -B -m scripts.diagnose_weights --reference artifacts/checks/portable_inference_full_2026-11-23/differential.json --output artifacts/checks/weight_review
```

手动工作流在模型检查前执行该权重诊断；发生失配仍收集模型及完整回归结果，工作流保留失败状态。各次脚本与原始数组随结果归档，旧记录不改写。

三个分层CI作业已[通过](../artifacts/checks/quality_ci_2026-11-22/completion.json)。云端AMD EPYC完整回归在冻结模型预测处失败，最大误差约5.96×10⁻⁸；限定`MKL_ENABLE_INSTRUCTIONS=AVX2`后仍失败。两次原始记录分别保存在`artifacts/checks/ci_research_2026-11-22/hosted_default`及`artifacts/checks/ci_research_avx2_2026-11-22`。本机Intel环境在相同AVX2设置下，48个冻结模型的误差均为0。指令集限制不保证跨厂商选择同一内核，见[Intel说明](https://www.intel.com/content/www/us/en/docs/onemkl/developer-guide-linux/2024-0/instruction-set-specific-dispatch-on-intel-archs.html)。

早期手动工作流的`windows-2022`与`windows-latest`镜像均出现上述预测差异，Windows 2022记录另保存在`artifacts/checks/ci_research_windows2022_2026-11-22`。新增的[逐阶段对照](../artifacts/checks/prediction_diagnostics_2026-11-22/cloud_comparison.json)将差异定位到Ridge原生归约。固定项目推理顺序后，本地Intel及托管AMD EPYC 7763的完整回归均通过；[托管结果](../artifacts/checks/portable_predictions_2026-11-23/cloud/ci-regression-45046246783/result.json)覆盖全部原验收范围，耗时60.3分钟。原失败保留，当前没有常驻研究运行器。

当前完整工作流默认使用明确的`windows-2025`，另保留`windows-2022`选项。权重修复保持原历史值，但冻结旧代码在Windows 2022上的`exp2`本身不同；该环境的旧、新代码完整比较已在权重哈希处按原门槛失败，原始结果保留。24组当前权重在两种系统上均与同一原参考精确匹配，见[权重数值一致性](reproducibility.md#训练权重数值一致性)。操作系统标签不承诺CPU或长期数值一致，每次完整验收仍检查全部原范围。

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

## 性能复核

在固定研究环境中执行以下命令，输出须为新目录。运行期间不要修改源码、依赖锁或完整性清单，不要并行运行其他本地重任务。

```powershell
.research/python.exe -B scripts/benchmark.py --project . --baseline 2443df760246600710c7b3f0bed3e2e9bf46e9e8 --repeats 3 --output artifacts/checks/performance_review
```

四项工作分别为全库完整性、48个模型、1,425个历史账户及75个纸面账户；旧、新实现使用相同冻结金融输入和当前归档清单。每项各运行三次，以独立进程交替执行旧、新实现，比较耗时和峰值内存的中位数。每个样本仍须通过对应金融检查；不执行拟合。

耗时不包括标准回归加载器的准备阶段；操作系统记录的进程峰值包含该公共加载器及两套已导入实现，不能解读为独立安装包的导入占用。原E0采用采样内存，本次采用操作系统峰值，因此以同批重测的E0为比较对象。超过10%的耗时或内存回退返回`REVIEW_REQUIRED`，保留原始样本并解释或修复；不改阈值，不以单次最快结果代替中位数。

2026-11-23的[主测](../artifacts/checks/performance_2026-11-23/result.json)共24个样本，全部金融和输入校验通过。下表为三次测量的中位数；主测仍保留`REVIEW_REQUIRED`状态。

| 工作 | E0耗时 | 当前耗时 | 耗时变化 | 进程峰值变化 |
| --- | ---: | ---: | ---: | ---: |
| 全库完整性 | 104.16秒 | 128.10秒 | +22.98% | -0.11% |
| 48个模型 | 229.10秒 | 146.69秒 | -35.97% | +0.27% |
| 历史账户 | 732.72秒 | 737.35秒 | +0.63% | -0.10% |
| 纸面账户 | 421.89秒 | 443.54秒 | +5.13% | -0.21% |

针对全库扫描，另按事先确定的四组交替顺序[复测](../artifacts/checks/performance_integrity_review_2026-11-23/result.json)。8个样本均通过完整性检查，耗时中位数为390.90秒和449.31秒，仍触发+14.94%的告警。两轮CPU时间中位数分别增加5.77%和8.62%；主要差距落在总耗时减去进程CPU时间的部分，且同一实现的耗时波动较大。

[复核记录](../artifacts/checks/release_acceptance_2026-11-23/performance_review.json)确认扫描、读取及哈希函数的可执行定义相同，路径配置文件逐字节相同；输入、清单与环境锁也未变化。复测期间系统记录了一次电源状态变化，但没有单独测量存储、缓存、调度或电源的影响，不能指定它为原因。全部样本及两次告警原样保留，包括1987.91秒的样本。按计划的“解释或修复”条件完成复核，保留未改变的扫描实现；这不是性能全面通过或提速结论。

## 已登记的等价证据

| 范围 | 结果 | 记录 |
| --- | --- | --- |
| 模型 | 48个模型重载，最大预测误差0 | [全量回归](../artifacts/checks/dependency_closure_full_2026-11-23/result.json) |
| 历史账户 | 1425个完整账户及114份压缩账本精确一致 | [历史对照](../artifacts/checks/dependency_closure_full_2026-11-23/history.json) |
| 纸面账户 | 75个账户、1386个交易日精确一致 | [纸面验证](../artifacts/checks/dependency_closure_full_2026-11-23/paper.json) |
| 特征与选型 | 全部594只证券特征、24组拟合输入及12个冻结窗口与同环境旧实现精确一致；无模型重训 | [计算对照](../artifacts/checks/dependency_closure_full_2026-11-23/differential.json) |
| 报告 | 默认、指定实验和历史参考入口的HTML及PNG逐字节一致 | [渲染对照](../artifacts/checks/responsibility_checks_2026-11-22/report_render/result.json) |
| 独立环境 | E7源码在Windows 2025托管完整回归通过，三份财务对照文件及48份重算预测与本地逐字节一致；Windows 2022旧Git基线失败保留 | [跨环境验收](../artifacts/checks/release_acceptance_2026-11-23/cloud_comparison.json) |
| 训练权重 | 本地MKL、独立OpenBLAS及托管Windows 2022/2025的24组权重均匹配修复前原哈希 | [权重验收](../artifacts/checks/training_weights_2026-11-23/completion.json) |
| 开盘与核账 | 192组场景、2424个纸面交易日、38组拒绝路径；三档成本完整文件链路一致 | [差分对照](../artifacts/checks/paper_refactor_2026-11-22/differential.json) |
| 仓位与风控 | 29007组状态、1024组优化器输入及完整模拟对照 | [执行验证](../artifacts/checks/execution_refactor_2026-11-22/completion.json) |
| 训练与评估 | 12个冻结窗口以缓存重放，选型与历史结果一致 | [流程验证](../artifacts/checks/runtime_refactor_2026-11-22/completion.json) |

当前代码通过908项单元测试与75项集成测试，1项因本机符号链接权限跳过；18个模块的类型检查、41个模块的覆盖率门槛与四项缺陷检测通过，详见[最终质量验收](../artifacts/checks/release_acceptance_2026-11-23/release_quality.json)。41个运行模块与上述完整回归的源码逐字节一致；E7增加示例、性能工具及打包检查，未修改金融运行代码。[源码包安装](../artifacts/checks/release_acceptance_2026-11-23/source_archive_install.json)实际运行版本、示例及账本命令，六份示例输出与独立wheel安装逐字节一致。

此前独立OpenBLAS wheel的24组权重与原哈希一致；该环境通过48个冻结模型的精确预测检查，但完整回归因缺少Bottleneck而在历史校准处失败，不能作为完整金融验收。舍入边界初次失败及中断记录保留；修复后使用新输出验证。各记录仅证明其绑定源码、数据与协议下的结果，新的代码改动不能直接继承旧结论。

此前职责拆分源码的三个CI作业及106个发布文件核对通过，见[历史发布核验](../artifacts/checks/responsibility_checks_2026-11-22/publication.json)。新增诊断入口的三个CI作业和130个发布文件核对也通过，见[诊断发布核验](../artifacts/checks/prediction_diagnostics_2026-11-22/initial_publication.json)。这些记录绑定各自提交，不替代本次数值修复的云端验收。

上一轮Ridge修复的三个CI作业及695个发布文件的独立远端字节核对通过，见[源码发布核验](../artifacts/checks/portable_predictions_2026-11-23/source_publication.json)。托管完整回归使用同一提交和39个源码哈希。下载产物的SHA-256与GitHub记录一致；48份重算预测、历史账户和纸面账户对照文件与本地逐字节相同。额外跨环境比较发现24组重算训练权重哈希不同，其他拟合输入字段、冻结窗口、校准与历史结果一致；该轮[完整对照](../artifacts/checks/portable_predictions_2026-11-23/cloud_comparison.json)保留全部差异。随后已将其定位到Windows 2022系统`exp2`，修复与边界见[权重数值一致性](reproducibility.md#训练权重数值一致性)。

本次权重修复的三个CI作业及546个发布文件的独立远端字节核对通过，见[源码发布核验](../artifacts/checks/training_weights_2026-11-23/source_publication.json)。Windows 2022和2025均通过24组原权重哈希及48个模型预测检查；2025的完整回归覆盖全部原范围，三份财务对照文件与本地逐字节一致。2022的旧Git基线仍在权重比较处严格失败，未修改基线或跳过检查。两份下载归档的SHA-256与GitHub记录一致，见[完整验收](../artifacts/checks/training_weights_2026-11-23/completion.json)。

E2锁定环境验收时，Linux安装包通过835项测试，Windows通过834项、跳过1项本机权限检查。通用OpenBLAS环境的模型误差约4.47×10⁻⁸，未通过历史数值验收；这些失败未被放宽门槛或重训覆盖。精确复现使用[历史研究环境](reproducibility.md#运行环境)。

首次环境锁发布的CI在工作流校验阶段失败，未启动作业；任务级环境变量使用了该层不支持的`runner`上下文。修复后改用`github.workspace`的同级环境目录，保留[失败记录](../artifacts/checks/environment_ci_2026-11-22/workflow_failure.json)。上下文作用域以[GitHub说明](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)为准。

## 源码与历史身份

训练协议和纸面计划保留生成时的源码哈希。等价重构通过单独冻结的 `source_compatibility.json` 关联原文件身份、当前全部Python源码、差分验证和完整历史账本验证，不修改原协议。

缺少登记、哈希不符、证据不完整或引用越出项目时拒绝使用。模型输入、日期、预测和计划到期仍单独检查。活跃登记见[全模块兼容记录](../artifacts/checks/dependency_closure_full_2026-11-23/source_compatibility.json)及[依赖兼容记录](../artifacts/checks/dependency_closure_full_2026-11-23/dependency_compatibility.json)。

更改数值、时序、成交或持仓行为时，须重新完成对应模型和完整账户验证，并保留失败记录。验证脚本一经执行即作为证据保存；重试使用新文件，不能覆盖原结果。
