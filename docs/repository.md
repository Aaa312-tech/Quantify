# 仓库维护

目标：[Aaa312-tech/Quantify](https://github.com/Aaa312-tech/Quantify)，分支 `main`。用户已授权后续项目修改完成后同步；无需重复确认，也不代表创建定时任务。

代码、配置、文档、数据、模型和研究证据均纳入版本控制。凭据、本地环境、缓存、打包输出和Git内部文件除外；已回收的旧版本不重新上传。

## 更新

```powershell
scripts/sync.ps1 -Message 'fix: describe the concrete change'
```

脚本检查远端、格式、测试、冻结输入和LFS，提交后普通推送并等待CI。可用 `-Python 'D:\conda\python.exe'` 指定解释器。需要已安装开发依赖，且GitHub CLI登录有写权限的账号。

远端有新提交时先检查并合并，不以强制推送解决分歧。CI只检查代码与合成数据；数值和成交逻辑的修改还须执行[完整验证](reproducibility.md)。

## 数据与历史

`.joblib`使用Git LFS。完整克隆后运行 `git lfs pull` 和 `python -B -m quantfolio verify`，确认下载的实际对象及872项冻结文件。新增大型文件需先配置LFS；凭据不得写入文件或远端URL。

`.gitattributes`保留研究证据的原始换行。`artifacts/maintenance/import_2026-11-19`中的源文件哈希、迁移与回收记录描述当时状态，不随本轮重构更新。当前源码历史以Git提交为准。

研究仍使用V6和2026-11-18数据；工程验证通过不改变收益优势检验未通过的结论。
