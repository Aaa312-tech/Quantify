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

## 验证冻结参考结果

```powershell
python -m quantfolio verify
python -m quantfolio verify --refit --output artifacts/checks/refit_check
python -m quantfolio report
```

`verify`校验872项冻结文件、动量与行业特征的历史前缀、48个模型的保存预测和标签可用日期。`--refit`重新拟合第1窗口的两个估计器，与冻结预测逐项比较。目录更名不改变模型、预测、账本及数据文件的内容哈希。

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

不带`--run`的`report`从冻结参考结果生成 `reports/latest/index.html`，页面明确标记旧成交假设的问题。工程复现通过不增加独立市场样本，也不改变收益优势检验的结论。

## 环境与错误

冻结数值参考来自Windows、Python 3.13.2及`pyproject.toml`中的依赖版本。核心数值操作在局部pandas配置中关闭numexpr，退出后恢复调用者设置。其他平台的单元测试通过不等于所有历史浮点结果逐位相同。

输入、缓存及优化约束使用显式异常，Python的`-O`不会关闭这些检查。普通错误写入stderr并返回1，参数错误返回2；排查时在子命令前加`--debug`查看堆栈。LFS文件未下载时会提示 `git lfs pull`。

## 安装包检查

```powershell
python -m build
python -m ruff format --check src tests
python -m ruff check src tests
python -m pytest -q
```

CI在Windows的Python 3.11、3.13及Ubuntu的Python 3.13上，从源码分发包构建wheel并安装，确认包和模板可在仓库外加载，再运行测试。CI使用合成数据；完整历史验证在含LFS数据的项目中执行。
