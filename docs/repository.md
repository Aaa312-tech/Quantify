# 仓库维护

仓库：[Aaa312-tech/Quantify](https://github.com/Aaa312-tech/Quantify)，默认分支 `main`。代码、配置、研究数据、模型与验证证据一同版本化；环境、凭据、缓存和打包输出留在本地。

研究推进已暂停。仓库维护负责整理和验证已有成果；原研究计划不会因同步仓库而自动恢复。当前入口见[项目状态](status.md)。

## 文档位置

| 文件 | 维护内容 |
| --- | --- |
| `README.md` | 项目用途、回测摘要、安装和导航 |
| `docs/status.md` | 当前有效产物、已完成边界、暂停状态及待办摘要 |
| `docs/remediation.md` | 尚未执行的计划和验收条件 |
| `docs/research_history.md` | 历次修正过程与阶段性维护约束 |
| `AGENTS.md`、`CONTRIBUTING.md` | 当前维护要求和代码约定，不追加研究流水账 |
| 各专题文档、`artifacts/` | 方法解释与原始机器证据，彼此通过链接对应 |

当前结论发生变化时先更新状态页，再修改受影响的摘要。阶段数据须带上阶段边界，避免把历史缺项数量写成当前数量。

## 提交与同步

```powershell
scripts/sync.ps1 -Python '.\.venv\Scripts\python' -Message 'fix: describe the concrete change'
```

脚本检查远端、格式、测试、冻结输入和LFS，提交后普通推送，并等待对应提交的`ci.yml`结果。需要开发依赖和已登录的GitHub CLI。

远端有新提交时先检查并合并，不使用强制推送处理分歧。CI验证安装包和合成数据；数值与成交修改还需[历史验证](reproducibility.md)。

## 数据与产物

`.joblib`以及批量公告、行业分类采集中的PDF使用Git LFS；来源索引、哈希与核查结论直接保存在Git中。克隆后执行 `git lfs pull`，再运行 `python -m quantfolio verify`确认实际对象和冻结文件。`.gitattributes`保留研究证据的原始换行；不要批量格式化数据及冻结实验。

冻结JSON回执与已执行的审查脚本可能保留Windows换行，Git空白检查将行尾CR识别为换行的一部分，仍检查多余尾随空格、空行及制表符前的空格。不得为了消除格式提示而改写已绑定哈希的证据。

冻结参考放`artifacts/reference/<date>`。新训练放 `artifacts/experiments/<name>`，对应评估放 `artifacts/evaluations/<name>`；这些输出目录由命令创建，无需空占位文件。名称说明用途，避免迭代编号、`final`、`new`等易失效标记。

纸面收盘账户放`artifacts/accounts/<purpose_date>`，计划及开盘核算放`artifacts/plans/<purpose_date>`。
手工报价另存具名目录，来源与时刻随输入保留；生成文件自动加入完整性清单，后续只能新建记录。

工程校验保留在`artifacts/checks`，完整重放保留在`artifacts/replays`。可重建缓存和未登记临时产物可以清理；已冻结证据即使被新检查补充，也不能当作冗余文件删除。机器探针、回收站清单、重复流程演示和一次性清理脚本不属于发布产物。

已登记数据对照的失败、原始训练身份及修复闭环属于验收证据，不能当作重复缓存清理。追加冻结目录时排除`__pycache__`、`.pytest_cache`和`.pyc`；原证据字节及哈希保持不变。

软件发布使用语义版本号，在 `src/quantfolio/__init__.py`维护，构建元数据自动读取。策略按方法命名：`walk_forward_momentum`（滚动选型动量），由`configs/project.toml`标明。软件版本和研究策略是两个不同字段。

历史文件按用途命名，内容保留原字节。`configs/archive.toml`集中记录路径、策略标识和特征列的兼容映射；完整性清单按新路径核对原哈希。重命名必须核对文件内容、模型输入顺序、预测和账本，不能用重新生成的哈希掩盖差异。
