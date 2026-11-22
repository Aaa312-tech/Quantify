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

CI在Windows / Python 3.11、Windows / Python 3.13和Ubuntu / Python 3.13构建并安装wheel，再于源码目录外检查导入和模板。测试使用合成输入，不在CI下载完整研究数据。

## 当前结果的复核

```powershell
python -m quantfolio verify --run artifacts/experiments/recipient_entitlements_2026-11-22
```

该命令校验冻结文件、特征和训练身份，重载48个已有模型并比较预测，不进行新拟合。若需保存回执，另加 `--output artifacts/checks/model_review`，目录必须尚不存在。不带 `--run` 的 `verify` 检查旧冻结参考；它不能替代当前实验验证。

```powershell
python -m quantfolio verify-paper --output artifacts/checks/paper_review
```

纸面验证把当前评估的75个主策略历史账户、1386个交易日通过计划与账户流程重放，比较完整账本。这些是历史集成检查，不是前向交易。

## 已登记的等价证据

| 范围 | 结果 | 记录 |
| --- | --- | --- |
| 模型 | 48个模型重载，最大预测误差0 | [模型验证](../artifacts/checks/paper_refactor_2026-11-22/models/verification.json) |
| 历史账户 | 1425个完整账户及114份压缩账本精确一致 | [历史对照](../artifacts/checks/paper_refactor_2026-11-22/history.json) |
| 纸面账户 | 75个账户、1386个交易日精确一致 | [纸面验证](../artifacts/checks/paper_refactor_2026-11-22/paper/verification.json) |
| 开盘与核账 | 192组场景、2424个纸面交易日、38组拒绝路径；三档成本完整文件链路一致 | [差分对照](../artifacts/checks/paper_refactor_2026-11-22/differential.json) |
| 仓位与风控 | 29007组状态、1024组优化器输入及完整模拟对照 | [执行验证](../artifacts/checks/execution_refactor_2026-11-22/completion.json) |
| 训练与评估 | 12个冻结窗口以缓存重放，选型与历史结果一致 | [流程验证](../artifacts/checks/runtime_refactor_2026-11-22/completion.json) |

最近一次核心代码检查为799项通过、1项本机符号链接权限跳过，详见[验收回执](../artifacts/checks/paper_refactor_2026-11-22/completion.json)。各记录仅证明其绑定源码、数据与协议下的结果；新的代码改动不能直接继承旧结论。

## 源码与历史身份

训练协议和纸面计划保留生成时的源码哈希。等价重构通过单独冻结的 `source_compatibility.json` 关联原文件身份、当前全部Python源码、差分验证和完整历史账本验证，不修改原协议。

缺少登记、哈希不符、证据不完整或引用越出项目时拒绝使用。模型输入、日期、预测和计划到期仍单独检查。活跃登记见[源码兼容记录](../artifacts/checks/paper_refactor_2026-11-22/source_compatibility.json)。

更改数值、时序、成交或持仓行为时，须重新完成对应模型和完整账户验证，并保留失败记录。验证脚本一经执行即作为证据保存；重试使用新文件，不能覆盖原结果。
