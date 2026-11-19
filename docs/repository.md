# GitHub仓库维护

目标仓库：<https://github.com/Aaa312-tech/Quantify>，默认分支 `main`。用户授权将当前工作项目全部同步，并在后续项目工作完成后持续更新该仓库；无需对同一同步范围反复征求确认。这不创建定时任务。

## 同步范围

跟踪 `src`、`tests`、`configs`、`scripts`、`docs`、`data`、`artifacts`、`reports` 及根目录项目文件。先前已移入Windows回收站的旧版本不属于当前项目，不重新上传。

本地Python环境、缓存、打包输出、Git内部文件和凭据不进入版本控制。不要把令牌写入脚本、文档或远端URL，认证使用本机GitHub CLI或Git凭据管理器。

`*.joblib`通过Git LFS保存实际内容，覆盖数据快照和拟合模型。其他现有文件使用普通Git。新增超过100 MiB的文件必须先配置LFS；对于会频繁变化的大型二进制，也应优先考虑LFS。[GitHub大文件规则](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)

## 字节完整性

原实验中有历史CRLF文本文件。`.gitattributes`禁止对数据、研究产物、参考材料和维护证据进行自动换行转换；代码继续使用UTF-8与LF。`.joblib`在LFS下载后必须恢复为真实二进制，而非仅保留指针。

完整克隆后运行：

```powershell
git lfs pull
python -m pip install -e '.[dev]'
python -B -m quantfolio verify
```

`configs/integrity_manifest.json`验证冻结输入与参考证据。`docs/maintenance`中旧的清理清单和源文件哈希描述其记录时点，不会因后续合法维护而改写成新历史。

## 常规更新

```powershell
git fetch origin
git status --short --branch
scripts/check.ps1
scripts/sync.ps1 -Message 'fix: describe the concrete change'
```

同步脚本会确认目标仓库和分支、检查远端没有未合入更新、运行质量检查和冻结输入校验、检查LFS、提交并进行普通推送。它不会强制推送，不会覆盖远端历史。远端存在新提交时，先查看变更并合并，再继续同步。

如果当前Python不在PATH上，可传 `-Python 'D:\conda\python.exe'`。使用脚本前安装 `.[dev]`，并让GitHub CLI登录有写入权限的账号。脚本推送后会等待并检查对应的GitHub Actions结果。一次提交应该对应一个可解释的研究或工程变更，提交说明包含最终行为和相关验证。

推送后检查 [GitHub Actions](https://github.com/Aaa312-tech/Quantify/actions)。现有CI检查源码格式、静态问题与合成数据测试，默认不下载全部LFS数据；它不等价于收益优势验证。数值逻辑变化还须按 `docs/reproducibility.md` 做相关模型或账本验证。

## 当前研究状态

只维护V6活动代码；均衡进攻、8%回撤预警、非恢复机制和2026-11-18数据截止日保持不变。发布仓库不会使未通过的收益优势检验变为通过。
