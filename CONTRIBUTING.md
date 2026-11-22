# 开发约定

代码放在 `src/quantfolio`，按职责组织，不按迭代编号拆包。Python使用UTF-8、LF、4空格缩进和100列；格式及导入顺序交给Ruff。

## 代码

- 模块、函数和变量用 `snake_case`，类用 `PascalCase`，常量用 `UPPER_CASE`。
- 一个模块围绕一项职责。不要为一个小函数单建文件，也不要为未来可能用到的功能预先搭框架。
- 业务变量写出含义和单位，如 `position_values`、`quantity`、`after_fee_nav`；矩阵运算可用常见数学符号。
- 注释说明时间可用性、单位和交易限制。不复述显而易见的语句。
- 共享交易规则集中定义；独立核账应保留自己的计算，避免执行器和验证器共享同一错误。
- 持久化使用 `storage.save`，不写NaN/Infinity。新实验用新目录，不覆盖冻结参考文件。

## 测试位置

| 目录 | 覆盖范围 |
| --- | --- |
| `tests/data` | 行情、状态、特征、快照和数据修补 |
| `tests/sources` | 公告、分红权益和行业来源解析 |
| `tests/portfolio` | 成交、资金、风险、行业约束和换手 |
| `tests/workflows` | 命令、训练、评估、纸面计划和存储 |

共享的合成输入放在 `tests/factories.py`，测试模块之间不互相导入。构造函数用输入含义命名，如 `market_bundle`、`raw_history`；只有需要 pytest 生命周期管理时才在 `conftest.py` 定义 fixture。可按目录运行检查，例如 `python -m pytest tests/portfolio -q`。

## 检查与提交

运行 `scripts/check.ps1`。数值、时序或成交逻辑改变时，再按[验证说明](docs/validation.md)核对模型与完整账本。新增测试应覆盖实际失败情形或交易约束。

开发依赖按`uv.lock`安装，已有模型按[复现指南](docs/reproducibility.md#运行环境)使用固定的研究环境。更新依赖时同时检查锁文件和构建后端；通用环境的测试通过不能替代历史模型及账户的精确回归。

README保留入口和当前结论；方法、实现、运行说明分别放在对应文档。机器结果放 `artifacts`，不要复制到文档目录。报告注明样本、区间、成本与基准，区分连续收益和重置本金的10日均值。

有效产物和暂停状态统一维护在`docs/status.md`；待办放`docs/remediation.md`，历史过程放`docs/research_history.md`。不要向`AGENTS.md`或首页追加逐批实验流水账。

提交标题说明动作与对象，例如 `fix: reject recovery in latched risk audit`。同步方式见[仓库维护](docs/repository.md)。

## 参考代码

职责划分和命名参考以下实现，未复制第三方代码：

- [Backtesting.py的Broker](https://github.com/kernc/backtesting.py/blob/master/backtesting/backtesting.py)：订单处理与每日账户推进分开。本项目用 `_Execution` 集中处理开盘成交，`simulate`保留信号和估值流程。
- [Qlib的Account](https://github.com/microsoft/qlib/blob/main/qlib/backtest/account.py)：区分持仓核算和绩效统计。本项目由 `Ledger`记账，`metrics`统计账户与区间结果。
- [pysystemtrade的portfolioWeights](https://github.com/pst-group/pysystemtrade/blob/develop/sysquant/optimisation/weights.py)：区分权重、金额和数量，按资产键对齐。重命名仓位和训练变量时遵循这一点，没有引入其框架层级。

构建使用[setuptools动态版本](https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html)，CI按[pytest安装包测试建议](https://docs.pytest.org/en/stable/explanation/goodpractices.html)验证wheel。修改模板、入口或打包配置时，应确认安装包在源码目录外可用。
