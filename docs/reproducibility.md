# 运行与复现

项目需要Python 3.11–3.13和Git LFS。先按README克隆完整仓库，在虚拟环境中安装 `.[dev]`。数据与模型属于项目目录，不打进Python安装包；从其他目录运行时使用 `--project PATH` 或 `QUANTFOLIO_ROOT`。

## 当前支付日接入

当前配置与`configs/experiments/companion_payments.toml`一致；协议为`configs/protocols/companion_payments.json`，精确算术补核另见`payment_label_precision.json`。完整证据在`artifacts/checks/companion_payments_2026-11-21`，最终入口为`completion.json`、`validation.json`、`activation.json`和`input_closure.json`。`input_scope.json`保留首次独立核算失败，不能替换为最后状态。

```powershell
python -m quantfolio --config configs/experiments/companion_payments.toml train --first 1 --last 11 --include-latest --output artifacts/experiments/payment_reproduction
python -m quantfolio --config configs/experiments/companion_payments.toml evaluate --run artifacts/experiments/payment_reproduction --all-policies --output artifacts/evaluations/payment_reproduction
python -m quantfolio --config configs/experiments/companion_payments.toml verify --run artifacts/experiments/payment_reproduction --output artifacts/checks/payment_reproduction
```

命令使用冻结快照，输出须尚不存在。重建需要另行登记新路径并保留全部来源绑定、独立日期事实和标签零变化要求。此轮全部旧模型、完整账户与纸面账户直接使用最终源码复核；没有绕过模型身份守卫或改写原回执。后续章节保留各历史阶段的重现边界。

## 公告角色来源核对

最新来源阶段的协议为`configs/protocols/distribution_roles.json`，证据位于
`artifacts/checks/distribution_roles_2026-11-21`。使用`.[disclosures]`中的固定pypdf版本运行
`review-distributions --protocol ...`；独立审查另需`.[industry-data]`中的PyMuPDF。
现有输出已冻结，重跑须先登记新的输出协议，不覆盖旧检查。

`source_review.json`保留1839个事件、全部原候选及角色判定；正式验收还需同时读取
`independent_closure.json`、`amendment_closure.json`、`visual_review.json`和`completion.json`。
首次独立读取和修订跨页断言失败保留，不能用原始提取结果代替最终验收。新增77项支付日
在该来源阶段只作为事实保留；其后73项已按上方独立接入协议完成验证。该来源阶段本身没有重新拟合或生成新计划。
该阶段的48个模型通过普通模型守卫重新加载，全部预测精确一致。来源验收不代替接入前的标签范围登记和完整验证。

## 前一阶段分红可用时间接入

前一阶段配置为`configs/experiments/distribution_availability.toml`，绑定新快照、训练、评估
及状态审查。协议为`configs/protocols/distribution_availability.json`；原始失败协议
`distribution_dates.json`、部分输出和实际源码均保留。验收在
`artifacts/checks/distribution_dates_2026-11-21`，来源及标签范围见[接入说明](distribution_availability.md)。

```powershell
python -m quantfolio --config configs/experiments/distribution_availability.toml train --first 1 --last 11 --include-latest --output artifacts/experiments/distribution_reproduction
python -m quantfolio --config configs/experiments/distribution_availability.toml evaluate --run artifacts/experiments/distribution_reproduction --all-policies --output artifacts/evaluations/distribution_reproduction
python -m quantfolio --config configs/experiments/distribution_availability.toml verify --run artifacts/experiments/distribution_reproduction --output artifacts/checks/distribution_reproduction
```

这些命令读取已冻结的新快照，输出目录必须尚不存在。重新构建快照使用`repair-data`及
独立登记的新输出路径，保留原来源、审查基础与106项独立标签的哈希绑定，不能改写现有协议
或冻结快照。缺少独立标签验收的协议仍要求全部标签精确不变。

原输入的48模型、1425完整账户及75纸面账户实际在首次日期补充源码下验证。后续仅改变快照
验收范围；`compatibility/source_review.json`保留AST证明和最终源码下额外48次普通模型重载。
原账户回执保留实际执行源码身份，不改写为最终哈希。新数据完整拟合、重载、账户与纸面
检查均使用最终源码；24阶段目标预期在新数据拟合前冻结。

## ETF接入（前一阶段）

该阶段使用`configs/experiments/etf_reported.toml`对应的快照、训练和评估。事前协议为
`configs/protocols/etf_integration.json`，三个固定对照的完整验收在
`artifacts/checks/etf_integration_2026-11-21`。覆盖与身份组仅作归因，不能按收益另选基线。

```powershell
python -m quantfolio repair-funds --protocol configs/protocols/etf_integration.json --variant reported
python -m quantfolio --config configs/experiments/etf_reported.toml train --first 1 --last 11 --include-latest --output artifacts/experiments/etf_reproduction
python -m quantfolio --config configs/experiments/etf_reported.toml evaluate --run artifacts/experiments/etf_reproduction --all-policies --output artifacts/evaluations/etf_reproduction
python -m quantfolio --config configs/experiments/etf_reported.toml verify --run artifacts/experiments/etf_reproduction --output artifacts/checks/etf_reproduction
```

登记快照已存在，第一行会拒绝覆盖；重新构建须使用新协议、新输出，并生成协议绑定的父快照
重建回执。其余命令可读取已冻结快照，输出目录须尚不存在。固定预测对照使用独立的
`etf_<arm>_fixed_2026-11-21`目录，不提前占用普通评估器的输出目录。

实际144次拟合和账户评估保留当时源码身份。后续`fund_mode`保留修复只改数据标识路径，
`mode_preservation/closure.json`绑定AST证明、四份完整精确重建及144个普通守卫下的模型重载。
不得将训练回执的源码哈希改写成修复后的哈希，也不得跳过普通过期来源检查。
来源金额、未来信息边界、逐笔敞口、全部成本和原始失败均见[接入说明](etf_integration.md)。

## 日期化行业对照（前一阶段）

协议`configs/protocols/industry_timing.json`在提交`6b31ec98`登记，分别构建覆盖过滤、
最新静态分类和日期化分类。每组都有独立配置、快照、训练、评估与核验目录。

```powershell
python -m quantfolio repair-industries --protocol configs/protocols/industry_timing.json --variant dated
python -m quantfolio --config configs/experiments/industry_dated.toml train --first 1 --last 11 --include-latest --output artifacts/experiments/industry_reproduction
python -m quantfolio --config configs/experiments/industry_dated.toml evaluate --run artifacts/experiments/industry_reproduction --all-policies --output artifacts/evaluations/industry_reproduction
python -m quantfolio --config configs/experiments/industry_dated.toml verify --run artifacts/experiments/industry_reproduction --output artifacts/checks/industry_reproduction
```

第一行的登记目标已存在时会拒绝重建；重新构建须复制协议、使用新目录，并重新生成绑定
该协议的旧源码父快照回执。训练示例读取已冻结快照，输出目录也须尚不存在。
另外两组使用对应`industry_coverage.toml`与`industry_static.toml`；静态组只用于回溯归因。
普通`evaluate`要求目标目录不存在，因此应先完成普通评估，再向其子目录写固定预测对照。
本轮覆盖组曾因对照提前创建父目录而被正常保护拒绝；原失败日志保留，通过临时暂存已有
文件后在空目录重新评估解决，全部暂存文件逐字节归回。过程见
`coverage/evaluation_recovery_protocol.json`与`coverage/evaluation_recovery.json`。

旧输入兼容性、独立来源连接、全部行业排名、未来分类扰动、三组训练及固定预测回放的
证据集中在`artifacts/checks/industry_timing_2026-11-21`。固定预测诊断明确使用前一组
预测，在覆盖组只删除新不合格键；不重新校准，也不经过普通评估器伪装为匹配的数据模型。

旧输入完整回放后仅扩展了纸面计划的源码依赖常量，加入独立行业核账模块。保留修改前文件、
AST逐节点比较和最终源码下75个纸面账户的再次回放，见`implementation/source_guard_closure.json`。
这项有明确范围的兼容性证据不解除普通训练或纸面计划的过期源码校验。

新分类组合的纸面核验进一步发现：缺失开盘报价被表示为空价格行，拒单原因因此从
`NO_QUOTE`变成`INVALID_OPENING_REFERENCE`。实际失败账户的资金、交易、净值、决策
和权重一致，但完整账本校验仍拒绝通过。修正只删除这条不存在报价时的合成行，历史成交器、
训练、模型与输入不变；原失败记录保留，并用AST差异证明修改范围。最终重新核对新旧
共300个纸面账户，证据与原训练源码身份分别保存在`paper_missing_quote`和各组`validation.json`。
读取本轮结果应以最终`validation.json`及`paper_quote_review`为准，不能把原`paper`中的
失败记录改写为通过。

## 跳空因子精度修正

登记协议为`configs/protocols/gap_precision.json`，登记提交为`298bdcc6`。
修改前以旧源码全量重建594只证券、414612行数据，精确核对结果保存在
`artifacts/checks/gap_precision_2026-11-21/parent_rebuild.json`。
新的构建命令是：

```powershell
python -m quantfolio repair-data --protocol configs/protocols/gap_precision.json --output data/processed/2026-11-20-local-gaps
```

构建必须绑定冻结的父快照重建回执，且只允许`gap`与`rank_gap`改变；它不会自动启用新数据。
已有目录不能覆盖。重复研究须复制协议并指定新的输出目录，在隔离检出的原提交上用
`preprocessing.build_dataset`和`compare_history`重建父快照，生成绑定新协议哈希的回执并冻结，
再用修正源码构建。精确重建旧算法的结果必须使用该登记协议指明的旧提交，不能用新算法
代替旧算法后声称复现一致。

独立全量检查涵盖400275条报价的有理数参照、全部并列排名，以及全部原调整价格和送转股
序列的逐值一致性。合成失败用例和修复后测试分别保存，数值误差上限只用于验收，
不进入因子或交易规则。同名检查目录已完成48次拟合、1425个重训账户、1425个固定旧预测
账户及75个纸面账户的验收；旧输入的兼容性验证单独保留。
实际使用的[独立核验脚本及哈希](../artifacts/checks/gap_precision_2026-11-21/verification_sources.json)
作为审计证据归档，不进入运行时策略包。

## 训练、评估、报告

```powershell
python -m quantfolio train --first 1 --last 1 --output artifacts/experiments/example_run
python -m quantfolio evaluate --run artifacts/experiments/example_run --output artifacts/evaluations/example_run
python -m quantfolio report --run artifacts/evaluations/example_run
```

这组命令运行第1个窗口，结果标为 `PARTIAL`，报告位于 `reports/experiments/example_run/index.html`。完整训练使用 `--first 1 --last 11`；`--include-latest`额外拟合数据截止日模型，它不会进入历史外层评估，也不会下载新行情。

`evaluate`检查训练状态、协议身份、数据哈希、实际预测行和时间覆盖，然后分别回测所选策略与同风控动量。默认包含10/20/30bp每边滑点，可用 `--cost 10`选择单档，用 `--all-policies`运行19项规则比较。部分窗口不会冒充完整回测，新实验也不会自动替换主策略。

评估目录包含 `evaluation_summary.json`及逐笔、逐日压缩账本。报告读取同一评估目录的成本、区间和样本数，校验所用账本哈希。部分窗口不展示跨区间的历史基准收益对比。

每次训练或评估使用新目录。目录已存在时命令会拒绝覆盖；缺少完成状态的训练不能进入评估。运行失败时保留已写出的诊断材料，修复原因后使用新名称。

有限候选研究使用`study`和`evaluate-study`，完整定义、命令及输出见
[选型、恢复与换手实验](strategy_study.md)。`study`先完成48次拟合，再冻结各窗口的内层
选择；`evaluate-study`核对源文件和选择记录后运行外层账户。`positive_score`选型模式
属于训练身份，不能把不同采用门槛的模型结果混用。登记协议和执行源文件须在实验前固定，
新增结果不会自动修改`configs/project.toml`。

## 验证冻结参考结果

```powershell
python -m quantfolio verify
python -m quantfolio verify --refit --output artifacts/checks/refit_check
python -m quantfolio report
```

`verify`校验完整性清单中的原872项证据及后来登记的快照、动量与行业特征的历史前缀、48个原模型的保存预测和标签可用日期。它始终读取`reference_bundle`，不受活跃快照切换影响。`--refit`重新拟合第1窗口的两个估计器，与冻结预测逐项比较。验证新训练使用`verify --run artifacts/experiments/<name>`，可同时加`--refit`和新的检查输出目录。

原引擎的精确复现必须在提交`e11bb36`的隔离检出中运行`replay`，不能用修正后的引擎强求旧收益不变。
已完成的1425账户复现保存在`artifacts/replays/prior_engine_2026-11-20/verification.json`。
当前代码会拒绝用另一成交模型重放旧实验；旧命令`backtest`仍是`replay`的兼容别名。

修正引擎的固定预测诊断使用：

```powershell
python -m quantfolio execution-bridge --output artifacts/evaluations/opening_review
python -m quantfolio report --run artifacts/evaluations/opening_review
```

默认19项策略×3档成本×25账户；可加`--primary-only`或`--cost 10`缩小范围。
它使用冻结预测与旧验证选型，只用于归因；完整重训必须另外运行`train`。
新评估拒绝训练与执行模型不一致，或执行源文件在训练后变化的实验。

## 纸面账户验证

```powershell
python -m quantfolio verify-paper --output artifacts/checks/paper_replay
```

这将活跃主策略三档成本的全部75个历史账户逐日通过纸面流程推进，比较完整摘要与账本，
并独立核账。它不是新一轮训练，也不生成前向观察。手工输入与时效要求见[纸面流程](paper_workflow.md)。

`evaluate-study`严格校验登记时的研究源码。复现既有`strategy_study_2026-11-20`研究时，
须在`70ff3bc`的隔离检出中运行；新增加的CLI与验证代码不会反向改写该研究的源码身份。
当前包可继续核验其模型、运行`verify-paper`及读取已冻结的评估报告。

## 公司行为修正

`configs/protocols/action_repair.json`登记首次修正；`dividend_review.json`在其快照上登记后续5项分红补证。以下命令按协议中的源快照生成新目录，检查公告哈希和原权益值，并重建全部1/3/5日标签：

```powershell
python -m quantfolio repair-data --protocol configs/protocols/dividend_review.json --output data/processed/reviewed_dividends
```

检查`audit.json`与`label_changes.json.gz`后，将`configs/project.toml`的`bundle`指向新文件、`protocol`指向修正协议，保留`reference_bundle`原路径。生成器会向完整性清单追加新快照；不会自动切换活跃输入。

```powershell
python -m quantfolio data-bridge --baseline artifacts/evaluations/action_bridge_2026-11-20 --output artifacts/evaluations/dividend_review
python -m quantfolio train --include-latest --output artifacts/experiments/reviewed_training
python -m quantfolio evaluate --run artifacts/experiments/reviewed_training --output artifacts/evaluations/reviewed_training --all-policies
```

数据对照要求日历、资格、特征及预测行完全一致。比较对象可以是已完成的执行诊断或数据诊断，且必须使用同一冻结预测来源。它不使用重建标签重新选型。完整重训另外运行，不能把两个阶段的收益变化合并称为选股改进。

不带`--run`的`report`读取当前配置的`evaluation`，生成`reports/latest/index.html`。显式`report --reference`才会读取冻结参考并写到`reports/reference`，页面标记旧成交假设的问题。工程复现通过不增加独立市场样本，也不改变收益优势检验的结论。

## 采集后续日线

```powershell
python -m quantfolio fetch-bars --date 2026-11-20 --output data/raw/market_review
python -m quantfolio fetch-bars --date 2026-11-20 --retry data/raw/market_review --output data/raw/market_retry
```

采集包含与活跃快照重叠的五个交易日，逐只留存未复权响应、请求信息、SHA-256和历史修订。请求按顺序执行，连续失败或限流时停止。仅在首批存在失败或遗漏请求时运行补采命令；它保留首批全部文件，且要求数据来源与日期相同。采集不修改活跃快照，也不从缺失日线推断停牌；当天16时之前拒绝请求当天完整日线。

完成跨源抽查、公司行为核查和重叠审查后，以登记协议生成新快照：

```powershell
python -m quantfolio refresh-data --protocol configs/protocols/market_extension.json --output data/processed/market_rebuild
```

该协议固定父快照、采集与补采哈希、允许保留但不覆盖的历史修订字段及公告证据。输出包含
`bundle.joblib`、`provenance.json`、`audit.json`和逐条标签变化。原始行情、面板、评分、资格及
特征的历史前缀须精确相同；单纯增加未来行情允许末端标签由不可观察变为可观察。
日期日历以基准的已观察交易日为准，不推算支付后的未知开盘日期。

切换研究输入时，成套更新`bundle`、`data_cutoff`、`protocol`，按完整训练与评估命令生成新结果，
再把`evaluation`指向已完成的对应实验；保留`reference_bundle`和原始冻结路径。
历史窗口的模型、预测与选择如果完全相同，则新评估同时是固定预测的数据对照；否则必须
另做固定旧预测的对照，分别归因，不能混称算法改善。

## 环境与错误

卖出预算研究的登记、双组完整训练、固定预测比较和验收见
[研究说明](exit_budget_repair.md)。已完成目录中的源码与数据哈希必须匹配才能重评；
复现实验应使用登记时提交`d7eabaa`、相同依赖与新输出目录，不覆盖完成记录。
本轮`legacy`组作为后续分红修正的基准保留，与此前1425个基线账户完全一致。

前一次研究使用`configs/protocols/inner_dividend_repair.json`登记的4项补证，登记提交为
`2f49fd243fee30cb021c39fb9996d9ecd8b8fb6e`。数据、训练和评估分别位于
`data/processed/2026-11-19-inner-dividends`、
`artifacts/experiments/inner_dividend_repair_2026-11-21`及
`artifacts/evaluations/inner_dividend_repair_2026-11-21`。沿用上述`repair-data`、
`train --include-latest`、`evaluate --all-policies`和`verify-paper`，复现时另取新输出目录。
48个模型文件、12组预测及全部选择精确一致，因此新评估也构成固定预测的数据对照，
1425个完整账户未变；验收和启用记录保存在同名`artifacts/checks`目录。

此前公司行为研究使用`configs/protocols/inner_action_review.json`登记的剩余29项事实，登记提交为
`7ccab04989aebcb6b9b1105af4fb3d75a564fdb5`。快照为
`data/processed/2026-11-19-inner-actions`，训练、评估和检查使用各自目录下的
`inner_action_review_2026-11-21`。同样先运行`repair-data --protocol`并指定新输出目录，
再配置对应数据与协议，运行`train --include-latest`、`evaluate --all-policies`和`verify-paper`。
本次14个模型、5组预测改变，不能省略固定旧预测的对照；
`artifacts/evaluations/inner_action_review_2026-11-21/fixed_forecasts`保存完整1425账户，
与前次基线精确一致。重训评估另有1425账户，差异及一个高成本主策略账户的归因保存在
`artifacts/checks/inner_action_review_2026-11-21/attribution.json`。已有目录仍不得覆盖。

冻结数值参考来自Windows、Python 3.13.2及`pyproject.toml`中的依赖版本。核心数值操作在局部pandas配置中关闭numexpr，退出后恢复调用者设置。其他平台的单元测试通过不等于所有历史浮点结果逐位相同。

输入、缓存及优化约束使用显式异常，Python的`-O`不会关闭这些检查。普通错误写入stderr并返回1，参数错误返回2；排查时在子命令前加`--debug`查看堆栈。LFS文件未下载时会提示 `git lfs pull`。

## 历史状态筛查

历史状态的外部采集使用独立可选依赖，训练和回测不需要它：

```powershell
python -m pip install -e '.[status-data]'
python -m quantfolio fetch-status --output data/raw/status_history_example
python -m quantfolio fetch-status --retry data/raw/status_history_example --output data/raw/status_history_retry
python -m quantfolio audit-status --collection data/raw/status_history_example --collection data/raw/status_history_retry --output artifacts/checks/status_history_example
```

仅当前批未成功时运行补采；没有缺项时命令拒绝创建目录。采集保存原始字段、响应哈希、
数据身份与请求范围；离线核查可重复传入不重叠的成功批次。数据提供方的日线标记只能
帮助定位历史状态问题；当前两只证券的公告核验与1425账户排查、三只证券的训练输入
核查及546只沪深股票的全范围来源比较保存在`artifacts/checks/historical_status_2026-11-21`。
其中10只带历史ST标记，现已补齐全部生效和撤销公告；这些材料不会自动覆盖历史资格、
模型或回测。前3个采集批次记录了开发期间的源码哈希，但未保存各中间版本的完整源码；
最后一批另存`collector_source.py`并核对其哈希。最终离线核查重新解析了全部原始响应。
接口用法和依赖来源见[BaoStock维护者发布页](https://pypi.org/project/baostock/0.9.4/)。

前一轮历史ST修正按`configs/protocols/historical_status_repair.json`执行，登记提交为
`e9fa763b102a199c68adb33b6d5197d0d3951d4a`：

```powershell
python -m quantfolio repair-status --protocol configs/protocols/historical_status_repair.json --output data/processed/2026-11-19-reviewed-status
```

协议绑定父数据、公告文件、筛查结果和输出目录。已有输出不能覆盖；复现时另存协议副本，
仅将输出路径改为新的研究目录并记录新协议哈希。随后配置对应快照和协议，运行
`train --include-latest`、`evaluate --all-policies`及`verify-paper`。
重训固定48次拟合、19种原政策与三档滑点，不新增候选。固定旧预测的1425账户须单独比较：
旧预测先按旧快照验证行身份，再在修正快照上执行；它只用于定位修正影响，不能代替新训练。
本轮已完成两组各1425账户及75个纸面账户核对，检查记录在
`artifacts/checks/historical_status_repair_2026-11-21`。48个模型和12组预测均改变，
必须保留固定旧预测对照；`training_status_exposure.json`与`warning_buy_comparison.json`
单独验证十只证券的训练及买入剔除。原两只、三只证券的较早诊断保持原统计范围。
新发现的六项公司行为补证只归档于`status_followup_2026-11-21`，没有混入本轮输入。


## 六项公司行为修正

当前研究按`configs/protocols/inner_event_repair.json`执行，登记提交为
`f53744dddaeffd48c09933349fc0f0d201eae80d`。保留前一轮日期化ST区间及全部非标签输入：

```powershell
python -m quantfolio repair-data --protocol configs/protocols/inner_event_repair.json --output data/processed/2026-11-19-reviewed-inner-events
```

已有目录不可覆盖；复现时在协议副本中指定新输出，并记录新哈希。随后按上节顺序进行48次拟合、
全政策评估与纸面验证。14个模型、5组预测发生变化，选型不变，因此实际另跑1425个固定旧预测
账户；不能用选型相同代替预测数组及账本比较。原始行情、日期化ST状态、非标签输入均精确一致。
检查和归因位于`artifacts/checks/inner_event_repair_2026-11-21`。

11月20日新增备用行情原始响应及采集源码归档于`data/raw/baostock_close_2026-11-19`、
`data/raw/baostock_close_2026-11-20`和`data/raw/tencent_beijing_daily_2026-11-20`。
来源差异审查位于`artifacts/checks/market_close_2026-11-20`。适配器按登记来源读取原始响应，
复核所有哈希、字段和来源分工；构建新快照使用：

```powershell
python -m quantfolio refresh-data --protocol configs/protocols/daily_source_extension.json --output data/processed/2026-11-20-reviewed-sources
```

已有目录不可覆盖；复现时在协议副本中使用新输出并记录哈希。该命令只构建独立快照，
不会切换活跃配置，仍需完整训练、账户核验、新数据绑定的ST审查及计划有效期检查。

## 安装包检查

```powershell
python -m build
python -m ruff format --check src tests
python -m ruff check src tests
python -m pytest -q
```

CI在Windows的Python 3.11、3.13及Ubuntu的Python 3.13上，从源码分发包构建wheel并安装，确认包和模板可在仓库外加载，再运行测试。CI使用合成数据；完整历史验证在含LFS数据的项目中执行。


## 已验收的11月20日扩展

行情扩展阶段的完整训练、评估与状态复核保存于`daily_source_extension_2026-11-21`；
它是历史验收阶段，当前配置使用本页开头的`2026-11-20-companion-payments`快照。
新数据来源、单位转换和历史标签变化见同名`artifacts/checks`目录；实现兼容性、训练影响
及固定旧预测对照分别存档，旧腾讯全池核验失败记录不变。

复现训练及评估时使用新输出目录，避免覆盖冻结结果：

```powershell
python -m quantfolio train --first 1 --last 11 --include-latest --output artifacts/experiments/source_extension_reproduction
python -m quantfolio evaluate --run artifacts/experiments/source_extension_reproduction --all-policies --output artifacts/evaluations/source_extension_reproduction
python -m quantfolio verify --run artifacts/experiments/source_extension_reproduction --output artifacts/checks/source_extension_reproduction
```

最新拟合只用于尚未发生的开盘计划，不进入已结束的外层回测。历史收益相同并不等于新一日
信号相同，也不代表前向验证通过。当前计划及有效期见[纸面流程](paper_workflow.md)。
## 已核验复牌标签修正

```powershell
python -m quantfolio repair-resumption --protocol configs/protocols/resumption_reference.json --output data/processed/2026-11-20-reviewed-resumption
```

该命令保留所有行情、公司行为、元数据、特征和非标签历史，仅附加审查中的开盘状态并重建
对应证券的合法退出标签。输出不可覆盖；复现时须在协议副本登记新目录。构建不会启用新数据，
还须完成48个模型、1425个新账户、1425个固定旧预测对照和75个纸面账户核对，并绑定ST专项复核。

复牌修正已完成上述验收，该阶段的配置成套绑定其独立快照、训练、评估和ST复核。
旧输入的源码兼容性诊断保存在`artifacts/checks/resumption_reference_2026-11-21/implementation`；
完整差异、固定旧预测、门槛敏感性和未核验缺口均在同名检查目录。

## 整池公告采集

```powershell
python -m quantfolio fetch-disclosures --output data/raw/issuer_notices
python -m quantfolio fetch-disclosures --retry data/raw/issuer_notices --output data/raw/issuer_notices_retry
```

输出必须是不存在的新目录。命令先保存绑定输入哈希的完整待核验队列，再逐组分页采集
公告和原始PDF；网络失败保留原记录。`collection.json`汇总完成、失败和未处理范围，
只有已结束批次才加入冻结清单。补采先核验原批次及输入哈希，复用已有成功文件并按年份
重查失败范围。采集不切换训练数据，也不生成已核验的历史事实。
当前关键词范围及核验要求见[数据契约](data_contract.md#整池公告补证)。

## 登记的整池分红修正

```powershell
python -m pip install -e ".[disclosures]"
python -m quantfolio review-distributions --protocol configs/protocols/distribution_repair.json
python -m quantfolio repair-data --protocol configs/protocols/distribution_repair.json --output data/processed/2026-11-20-reviewed-distributions
```

先将完整来源核验结果加入冻结清单，再构建独立快照。已有输出不能覆盖；复现须复制协议、
登记新的输出路径及哈希。修正命令核对解析器身份、全部原事件身份和逐字段来源，先精确
重建父快照，再重建受现金权益影响的特征与标签。命令不会自动切换研究配置。

数值验收包括旧输入的48个模型、1425个完整账户及75个纸面账户；新输入完整拟合48次，
另评估1425个重训账户与1425个固定旧预测账户。普通评估继续拒绝执行源码不同的旧训练。
固定旧预测仅作为独立归因诊断，不伪装成当前执行代码训练出的模型。

## 行业原表审查

```powershell
python -m pip install -e ".[industry-data]"
python -m quantfolio review-industries --protocol configs/protocols/industry_cell_review.json
```

该协议的输出已经冻结；重复运行须先复制协议，登记新的`output`和`checks_output`，且保留
来源、输入哈希及核对规则。命令拒绝已有数据目录，不下载来源、不切换训练配置。
`configs/protocols/industry_sources.json`记录首次严格审查，结果为`REVIEW_REQUIRED`；
`industry_cell_review.json`保留该失败结果并登记后续规则，不能把后续成功覆盖到旧目录。

后续全部来源已提取，状态为`SOURCE_CELLS_CHECKED_WITH_TAXONOMY_FINDINGS`，原始名称差异
和首次失败仍在。完成回执、完整代码对应、逐日覆盖及精确审计源码位于
`artifacts/checks/industry_cells_2026-11-21`；原始单元格位于
`data/processed/industry_source_cells_2026-11-21`。这两个目录尚不是可直接训练的数据快照。

该来源审查阶段只增加模块、命令及可选依赖，未改变模型输入。344项测试通过，1项主机符号链接检查跳过；
原冻结文件、交易核心源码、活跃配置和纸面计划输入逐项核对。没有数值或执行改动，
该阶段没有重新拟合或增加回测收益。后续日期化接入已按本页开头的独立协议完成全量重训、
同风控动量对照和账户核验；不能沿用本轮来源审查代替这些验证。

## ETF来源复核

本阶段不改变运行包与模型输入，因此不新增拟合或回测收益。`configs/protocols/etf_sources.json`登记范围，`artifacts/checks/etf_sources_2026-11-21/completion.json`保存最终哈希与验收。读取`field_closure.json`和`structural_review.json`判断已核实字段；`initial`、首次`review.json`及`listing_unresolved.json`保留失败和中间状态，不能替换为最后状态。

原始查询、下载回执与采集程序在`data/raw/etf_*`，逐份字段和来源坐标在`data/processed/etf_sources_2026-11-21`。公开网页查询只用于研究来源，不接触券商账户。审阅用PDF副本单列在检查目录，原件和额外提示页均保留；副本不是原始披露。原历史文件完整性使用`python -m quantfolio verify`核对，实际复现不得覆盖冻结目录。

定期报告按`configs/protocols/etf_holdings.json`另行登记。原件分置于`data/raw/etf_reports_2026-11-21`及两次补采目录；首次页面和表格、后续页面、最终单元格分别保存在`etf_holdings_2026-11-21`、`etf_holdings_followup_2026-11-21`和`etf_holdings_cells_2026-11-21`，未覆盖失败输出。

本阶段验收读取`artifacts/checks/etf_holdings_2026-11-21/cell_closure.json`及`completion.json`。528份报告的身份及8349个选定页面采用两种PDF解析器核对，全部股票行另核对有序代码清单；141处跨页边界有原图目视回执。已执行脚本保留其记录的哈希，复现必须登记新目录并明确继承哪些裁定；不能直接重跑到冻结路径。原PDF用LFS，PDF索引恢复警告和拒绝的截断副本均保留。该来源审查没有触发重训，后续接入必须另做完整模型与账户验证。

后续时点与敞口构建按`configs/protocols/etf_exposures.json`登记。数据位于`data/processed/etf_exposures_2026-11-21`，检查位于同名`artifacts/checks`目录。先以`notes_reviewed.json`确定附注来源，再经财务字段初查、版式复核与清单补核，最终读取`financial_observations.json.gz`；不能用早期提取替代最终字段。`financial_audit.json`核对528份报告的有序代码、金额、原横线及合计。

`equity_profiles.json.gz`保存752份来源组合，`session_profiles.json.gz`为22只基金与698个交易日的完整索引。`history_audit.json`独立重建日期选择、代码映射及精确金额分拆，`information_boundaries.json`检查未来报告、概要、分类和身份对应的截断与扰动。复现不需要下载新PDF，也不会隐式重训；使用新注册路径，并记录原脚本、原图裁定和输入哈希。最终使用范围见[敞口数据说明](etf_exposures.md)。

## 分红条款复核

本轮来源输入保持原1839项事件身份。协议依次为`distribution_terms.json`、`distribution_term_scope.json`和`distribution_approved_wording.json`；实际执行程序、提取器副本、失败记录和验收均保存在`artifacts/checks/distribution_terms_2026-11-21`。运行包仅改公告提取与版本，数值模块、活跃快照和纸面计划的哈希保持。

```powershell
python -m pip install -e ".[disclosures]"
python -m quantfolio review-distributions --protocol configs/protocols/distribution_approved_wording.json
```

以上协议输出已经冻结，命令会拒绝覆盖。重新执行前必须登记新的`review`和`checks_output`，并保留相同事件清单、来源哈希及门槛。独立读取使用`pymupdf==1.27.2.3`，脚本`independent_fields.py`的`--output`也必须指向不存在的新文件；它不调用生产提取器。首次执行的读取器另存`independent_initial.py`，对应原11项失败，不能用修复后源码解释其旧结果。

该来源阶段的最终验收见[来源说明](distribution_terms.md)链接的三个结论文件。435项本地测试通过，1项环境检查跳过；48个模型普通重载的预测完全一致。该阶段没有接入新增字段，也没有新增拟合和回测结果；后续接入及其单独登记的重训与账户对照见[分红与股份可用时间](distribution_availability.md)。
