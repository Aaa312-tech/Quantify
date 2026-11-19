# 维护记录

## 0.7.1 — 2026-11-19

- 当前策略改用方法名称“滚动选型动量”，统一命令、日志、文档和报告；软件版本单独维护。
- 新评估使用`selected_strategy`，通过历史结果的`primary_policy`读取旧账本。冻结文件、模型特征名及研究参数不变。
- `status`分别输出软件版本、策略标识和名称。主策略及动量基准的账本重放记录见`artifacts/replays/policy_names_2026-11-19/verification.json`。

## 0.7.0 — 2026-11-19

- 接通`train → evaluate → report --run`，支持部分窗口、成本选择、实验来源及预测覆盖校验。`replay`负责冻结重放，兼容旧命令`backtest`。
- 报告使用实际区间、成本及样本数；部分实验不再展示不同区间的V4对比。
- 关键约束不再依赖assert；完善LFS、路径及参数错误提示，JSON和压缩账本使用原子写入。
- 包导入不再改变全局pandas配置；项目路径集中配置，软件版本只有一个来源。
- CI从源码分发包构建并测试wheel，覆盖Windows和Ubuntu。修正文档中失效的模块引用，运行示例统一使用虚拟环境，移出机器清理日志。
- V6参数与冻结数据不变；完整重放和真实训练流程的验证见`artifacts/maintenance/quality_2026-11-19`。

## 0.6.1 — 2026-11-19

- 合并特征、模型、时间窗口和验证模块，移除单函数文件、空包装和未使用的区间估计函数。
- 分离开盘订单执行与逐日模拟；复用整手规则，合并重复收益统计，写清持仓金额、数量及训练样本的变量名。
- 审计改为检查非恢复风险等级，补充恢复仓位和缺失每日决策的回归测试。
- HTML模板移入包资源；精简项目说明，机器维护记录归入 `artifacts/maintenance`。
- CI使用Node.js 24运行时的[checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1)和[setup-python v7.1.0](https://github.com/actions/setup-python/releases/tag/v7.1.0)，固定到对应发布提交。
- 策略参数不变。工程验证结果见 `artifacts/maintenance/refactor_2026-11-19`。

## 0.6.0 — 2026-11-19

- 将完整当前项目纳入 `Aaa312-tech/Quantify`，包括数据、模型、报告与验证证据；采用Git LFS和保留原始换行的属性规则。
- 增加后续仓库维护约定、同步脚本及克隆说明；保留原有研究结论与风险设置。
- 选定V6为唯一活动策略，合并运行所需公共函数，移除活动代码对V2–V5包的依赖。
- 采用 `src` 包布局与 `pyproject.toml`；统一命令入口、格式、导入、测试和本地检查脚本。
- 数据快照、冻结实验、可重建报告和参考材料分目录管理；增加可移植SHA-256清单。
- 重建中文项目说明、方法、架构、复现指南和本地结果报告。
- 策略参数、成本、风险规则与历史数据保持原实验语义；迁移验证与回收站记录见 [历史迁移记录](https://github.com/Aaa312-tech/Quantify/blob/21a32c06331ff8a78b733aa531d857f7bee55cf8/docs/maintenance/reorganization.md)。

这是工程整理，不是新增收益证据。版本保留依据见 `docs/references/version_selection.json`。
