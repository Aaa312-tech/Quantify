# 仓库维护

仓库为 [Aaa312-tech/Quantify](https://github.com/Aaa312-tech/Quantify)，默认分支 `main`。代码、配置、研究数据、模型和验证记录共同版本化；环境、凭据、缓存和构建产物留在本地。

## 目录与命名

| 路径 | 内容与命名 |
| --- | --- |
| `src/quantfolio/` | 按职责命名的Python模块；报告模板放 `templates/` |
| `tests/` | 按数据、来源、组合、工作流分类，文件名为 `test_<behavior>.py` |
| `scripts/` | `check.ps1`作为本地检查入口；运行统一使用 `python -m quantfolio` |
| `configs/` | 活跃配置、注册协议、历史映射和完整性清单 |
| `data/raw/` | 按来源、用途、日期组织的原始响应与公告 |
| `data/processed/` | 经核验的数据快照，名称包含截止日期和处理口径 |
| `artifacts/experiments/`、`artifacts/evaluations/` | 同名训练及评估目录 |
| `artifacts/checks/`、`artifacts/replays/` | 独立验证和完整账本重放 |
| `artifacts/accounts/`、`artifacts/plans/` | 纸面账户、计划和开盘核算 |
| `reports/` | 根据评估结果生成的HTML和图表 |
| `docs/` | 使用、方法、当前状态和专题研究记录 |

文件和目录使用小写英文、下划线及ISO日期，已有来源文件保留原名。研究命名说明用途，不使用迭代编号、`final`、`new`等临时标记。策略名为 `walk_forward_momentum`；软件版本由 `src/quantfolio/__init__.py`提供，按语义版本维护。

README负责安装和结果摘要，[文档索引](index.md)负责导航，[状态页](status.md)负责有效产物；方法、契约和复现步骤放在对应文档。维护规则不记录逐批进度，变更记录只保留用户和开发者需要了解的变化。

## 冻结记录

完整性清单保存研究输入与输出的SHA-256。已冻结文件及已执行的检查脚本不得修改，包括失败结果；重试使用新路径。追加清单时保留原条目，不以重新计算哈希掩盖变化。

历史路径映射集中在 `configs/archive.toml`。确需迁移时保留原文件字节和哈希，验证全部消费者、模型输入顺序、预测和账户结果。文件多不等于冗余：来源、失败、父快照和复核记录承担追溯作用。

`.joblib`及批量公告、行业与基金PDF使用Git LFS。克隆后运行 `git lfs pull`，再按[验证说明](validation.md)检查实际对象。`.gitattributes`保留研究证据的原始换行；不要批量格式化 `data/`、`artifacts/`或冻结报告。

临时脚本、环境、缓存和重复构建文件不提交。手工报价保留来源与时刻，和输出一起存档；不得用生成时间或哈希代替历史可用性证明。

## 检查与发布

先检查远端及本地差异：

```powershell
git fetch origin
git status --short
git log --oneline --left-right HEAD...origin/main
git diff --check
git diff --stat
scripts/check.ps1 -Python '.\.venv\Scripts\python'
```

核对冻结文件完整性；数值、时序或执行变化还需完成[模型与账户验证](validation.md)。按文件或明确目录暂存，检查 `git diff --cached`后提交，再普通推送。不要自动暂存全部工作区，也不要强制推送解决分歧。

推送后按提交哈希查看 `ci.yml`的三个实际作业，全部完成通过后才记录发布成功。另从独立远端检出核对变更Git文件与LFS对象的字节，不能只以本地远端引用或上传进度作为验证。

本地检查不需要重新拟合。仓库发布也不恢复已暂停的研究任务。
