# 2026-11-19 项目整理记录

## 保留范围

保留V6为唯一活动策略。V2–V5的独立源码、模型和重复数据退出活动目录，V4保留精简的冻结比较结果。V6的全部训练、验证、成本敏感性与诊断账本保留，不能只留下有利结果。

赛事白皮书、通知截图和22条已核实公司行为及其来源PDF保留，并统一到 `docs/references`。重复PDF、临时构建产物和验证时生成的重复模型不作为长期工作文件。

## 工程验证

- 46项合成数据核心测试通过，Ruff格式与静态检查通过。
- 48个已保存模型成功加载，其预测与冻结数组最大差异为0。
- 第1窗口的Ridge与树模型重新训练，各35080条预测与原结果最大差异为0。
- 19项策略×3档成本×25个账户，共1425个账户的完整账本与原V6完全一致。
- 重放重新执行337800项账务检查、236510项执行规则检查。
- 新路径下保留872项输入与参考文件的SHA-256校验。重新训练只验证工程复现，不代表新增独立收益证据。

机器记录位于 `artifacts/checks/migration_refit/verification.json` 与 `artifacts/replays/migration_*bps/verification.json`。

## 回收流程

**已完成。** 旧工作目录集中为 `_retired_2026-11-19` 后，通过Windows现代回收接口送入回收站；暂存目录 `.restructure_staging` 也已回收。两个实际回收站目标都已确认存在，没有清空回收站，没有使用永久删除降级路径。

旧目录含9810个文件、1,263,642,679字节（约1205.10 MiB）。进入回收站后，逐文件重新核对大小与SHA-256，全部与原文件一致。新工作目录约174.1 MiB，根目录17项；这表示工作目录更精简，回收站中的文件仍占用磁盘。

迁移只在指定工作目录的已核对路径内进行。`work/node_modules` 的外部共享目标保留，200个顶层条目和12份抽样依赖清单与整理前一致。首次回收暂存目录时，已加载的临时Python库阻止操作；退出该进程后改用不加载这些库的原生Windows接口，回收完成。

最终实际路径下的46项测试、格式和静态检查再次通过，48个模型重新加载验证通过；旧目录退出后，872项保留输入和参考文件的完整性仍通过。可安装wheel已构建，并在源码目录以外独立导入和运行命令入口。未执行远程GitHub CI。

## 如何查证或恢复

- `recycled_inventory.json`：旧目录每个文件的原相对位置、大小与SHA-256。
- `legacy_recycle_integrity.json`：回收站内9810个原文件的逐项核对结果。
- `recycled_legacy.json` / `recycled_staging.json`：两个回收站目标及所用接口记录。
- `preflight.json`：V6的836个原实验文件与旧位置逐字节一致，数据包也完全相同。
- `package_check.json`：独立wheel、命令入口和无旧模块导入的检查记录。
- `format_check.log` / `lint_check.log` / `unit_tests.log`：实际路径下的本地质量检查。
- `source_tree_sha256.json` / `final_summary.json`：整理后的源码清单和最终汇总。

需要旧文件时，可在Windows回收站找到 `_retired_2026-11-19` 并还原。它会恢复为工作目录内的独立旧资料文件夹，不会直接覆盖新的 `src`、`data` 或报告；再按清单提取所需材料即可。

回收接口的行为和标志参考 [Microsoft IFileOperation文档](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags)。
