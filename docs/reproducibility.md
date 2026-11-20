# 运行与复现

项目需要Python 3.11–3.13和Git LFS。先按README克隆完整仓库，在虚拟环境中安装 `.[dev]`。数据与模型属于项目目录，不打进Python安装包；从其他目录运行时使用 `--project PATH` 或 `QUANTFOLIO_ROOT`。

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

当前研究使用`configs/protocols/inner_action_review.json`登记的剩余29项事实，登记提交为
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

日期修正按`configs/protocols/historical_status_repair.json`执行：

```powershell
python -m quantfolio repair-status --protocol configs/protocols/historical_status_repair.json --output data/processed/2026-11-19-reviewed-status
```

协议绑定父数据、公告文件、筛查结果和输出目录。已有输出不能覆盖；复现时另存协议副本，
仅将输出路径改为新的研究目录并记录新协议哈希。随后配置对应快照和协议，运行
`train --include-latest`、`evaluate --all-policies`及`verify-paper`。
重训固定48次拟合、19种原政策与三档滑点，不新增候选。固定旧预测的1425账户须单独比较：
旧预测先按旧快照验证行身份，再在修正快照上执行；它只用于定位修正影响，不能代替新训练。

## 安装包检查

```powershell
python -m build
python -m ruff format --check src tests
python -m ruff check src tests
python -m pytest -q
```

CI在Windows的Python 3.11、3.13及Ubuntu的Python 3.13上，从源码分发包构建wheel并安装，确认包和模板可在仓库外加载，再运行测试。CI使用合成数据；完整历史验证在含LFS数据的项目中执行。
