# 验证

验证分为代码检查、冻结输入核对、模型重载和账户对照。测试通过说明实现符合已检查的行为，不代表策略有超额收益。

## 日常检查

Windows：

```powershell
scripts/check.ps1 -Python '.\.venv\Scripts\python'
```

Linux或已激活环境：

```sh
python -m ruff format --check src tests
python -m ruff check src tests
python -B -m pytest -q
```

CI在Windows / Python 3.11、Windows / Python 3.13和Ubuntu / Python 3.13按`uv.lock`安装依赖，用固定构建后端生成并安装wheel，再于源码目录外检查导入和模板。测试使用合成输入，不在CI下载完整研究数据。现有历史模型的精确数值复现另使用[研究环境](reproducibility.md#运行环境)，不能由这些合成测试推断。

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

两种范围均检查完整输入清单和加载后的数据。`core`缩小计算范围，不能替代发布验收。`full`要求全部范围一致，模型预测误差为0；比较字段顺序、数组类型、标签、权重、历史校准、选择结果及压缩预测和账本字节。历史校准按原窗口重算，模型拟合次数必须为0。

输出目录必须未使用。`run.json`记录命令、环境、Git提交和实际源码哈希；`result.json`记录完成状态、范围和失败位置；`differential.json`、`history.json`、`paper.json`保存分项证据，`run.log`保存进度。运行失败返回非零状态，已有文件不覆盖；旧、新实现产生的重复临时账本在比较后清理。

默认只诊断，不改变正常加载守卫。需要登记等价源码时，在上述全量命令中显式加 `--register`：全部检查通过且输入和源码再次核对后，追加 `source_compatibility.json`及完整性清单。小范围、失败、缺项或源码变化均不能登记。原训练协议、计划身份及39,071项历史冻结文件保持。

干净克隆按[安装说明](../README.md#安装与验证)下载LFS对象即可运行。浅克隆还需 `git fetch origin 2443df760246600710c7b3f0bed3e2e9bf46e9e8`。缺少输入或LFS对象时先恢复指定文件，再使用新输出目录重试。跨平台精确复现须另有该环境的模型和完整回归证据，不能从单机通过结果推断。

## 已登记的等价证据

| 范围 | 结果 | 记录 |
| --- | --- | --- |
| 模型 | 48个模型重载，最大预测误差0 | [全量回归](../artifacts/checks/regression_full_2026-11-22/result.json) |
| 历史账户 | 1425个完整账户及114份压缩账本精确一致 | [历史对照](../artifacts/checks/regression_full_2026-11-22/history.json) |
| 纸面账户 | 75个账户、1386个交易日精确一致 | [纸面验证](../artifacts/checks/regression_full_2026-11-22/paper.json) |
| 特征与选型 | 全部594只证券特征、24组拟合输入及12个冻结窗口精确一致；无模型重训 | [计算对照](../artifacts/checks/regression_full_2026-11-22/differential.json) |
| 独立环境 | 锁定Windows研究环境和隔离新克隆的全量回归与原环境一致；跨平台安装及测试另行验收 | [环境验收](../artifacts/checks/environment_lock_2026-11-22/completion.json) |
| 开盘与核账 | 192组场景、2424个纸面交易日、38组拒绝路径；三档成本完整文件链路一致 | [差分对照](../artifacts/checks/paper_refactor_2026-11-22/differential.json) |
| 仓位与风控 | 29007组状态、1024组优化器输入及完整模拟对照 | [执行验证](../artifacts/checks/execution_refactor_2026-11-22/completion.json) |
| 训练与评估 | 12个冻结窗口以缓存重放，选型与历史结果一致 | [流程验证](../artifacts/checks/runtime_refactor_2026-11-22/completion.json) |

最近一次代码检查为834项通过、1项本机符号链接权限跳过，详见[检查日志](../artifacts/checks/regression_release_2026-11-22/checks_verified.log)。首次小范围回放的模块导入与日期索引比较失败、旧测试导入失败均保留；修复后重新使用新输出验证。各记录仅证明其绑定源码、数据与协议下的结果，新的代码改动不能直接继承旧结论。

锁定环境中的Linux安装包另通过835项测试，Windows通过834项、跳过1项本机权限检查。通用OpenBLAS环境的模型误差约4.47×10⁻⁸，未通过历史数值验收；这些失败未被放宽门槛或重训覆盖。精确复现使用[历史研究环境](reproducibility.md#运行环境)。

首次环境锁发布的CI在工作流校验阶段失败，未启动作业；任务级环境变量使用了该层不支持的`runner`上下文。修复后改用`github.workspace`的同级环境目录，保留[失败记录](../artifacts/checks/environment_ci_2026-11-22/workflow_failure.json)。上下文作用域以[GitHub说明](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)为准。

## 源码与历史身份

训练协议和纸面计划保留生成时的源码哈希。等价重构通过单独冻结的 `source_compatibility.json` 关联原文件身份、当前全部Python源码、差分验证和完整历史账本验证，不修改原协议。

缺少登记、哈希不符、证据不完整或引用越出项目时拒绝使用。模型输入、日期、预测和计划到期仍单独检查。活跃登记见[源码兼容记录](../artifacts/checks/regression_full_2026-11-22/source_compatibility.json)。

更改数值、时序、成交或持仓行为时，须重新完成对应模型和完整账户验证，并保留失败记录。验证脚本一经执行即作为证据保存；重试使用新文件，不能覆盖原结果。
