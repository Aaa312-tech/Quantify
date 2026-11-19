# 仓库维护

仓库：[Aaa312-tech/Quantify](https://github.com/Aaa312-tech/Quantify)，默认分支 `main`。代码、配置、研究数据、模型与验证证据一同版本化；环境、凭据、缓存和打包输出留在本地。

## 提交与同步

```powershell
scripts/sync.ps1 -Message 'fix: describe the concrete change'
```

脚本检查远端、格式、测试、冻结输入和LFS，提交后普通推送，并等待对应提交的`ci.yml`结果。可用 `-Python 'D:\conda\python.exe'`指定解释器。需要开发依赖和已登录的GitHub CLI。

远端有新提交时先检查并合并，不使用强制推送处理分歧。CI验证安装包和合成数据；数值与成交修改还需[历史验证](reproducibility.md)。

## 数据与产物

`.joblib`使用Git LFS。克隆后执行 `git lfs pull`，再运行 `python -m quantfolio verify`确认实际对象和冻结文件。`.gitattributes`保留研究证据的原始换行；不要批量格式化数据及冻结实验。

新训练放 `artifacts/experiments/<name>`，对应评估放 `artifacts/evaluations/<name>`。`artifacts/maintenance`只保留工程验证摘要。机器迁移探针、回收站路径和一次性清理脚本不属于发布产物，旧记录可从Git历史查阅。

软件版本在 `src/quantfolio/__init__.py`维护，构建元数据自动读取；策略版本由 `configs/project.toml`标明。软件升级不自动改变V6参数或其研究结论。
