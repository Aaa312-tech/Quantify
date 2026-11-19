# GitHub仓库参考记录

查看日期：2026-11-19。通过GitHub连接器读取官方仓库目录和实际文件。学习项目组织和开发习惯，没有复制第三方策略代码，也没有将本地项目发布到GitHub。

| 仓库及实际来源 | 观察到的做法 | 本项目的具体采用 |
| --- | --- | --- |
| [Microsoft Qlib](https://github.com/microsoft/qlib)；[pyproject.toml](https://github.com/microsoft/qlib/blob/main/pyproject.toml) | 单一Python包，分离文档、示例、脚本与测试；项目元数据与依赖集中管理 | `src/quantfolio`、`docs`、`scripts`、`tests`；集中式pyproject、命令入口 |
| [Qlib格式检查配置](https://github.com/microsoft/qlib/blob/main/.pre-commit-config.yaml) | 自动格式化与静态检查进入维护流程 | 采用Ruff统一格式和导入检查，提供本地脚本与CI配置；没有照搬其Black/flake8工具组合 |
| [Backtesting.py](https://github.com/kernc/backtesting.py)；[pyproject.toml](https://github.com/kernc/backtesting.py/blob/master/pyproject.toml)；[贡献说明](https://github.com/kernc/backtesting.py/blob/master/CONTRIBUTING.md) | 独立文档、变更记录、贡献指南；Ruff配置；可复现问题、测试和可审查修改 | README/CHANGELOG/CONTRIBUTING分工，100列格式、可复现检查和明确结果边界 |
| [pysystemtrade](https://github.com/pst-group/pysystemtrade)；[pyproject.toml](https://github.com/pst-group/pysystemtrade/blob/develop/pyproject.toml) | 按数据、量化计算、执行、系统等职责分层；pytest和开发依赖配置 | 按数据、模型、组合、执行、审计分模块；不按历史版本互相导入 |

这些仓库并非都使用 `src` 布局。这里选择 `src/quantfolio` 是为分离可安装包与项目数据，结合本项目需要作出的实现选择。

读取时的GitHub文件对象SHA（用于记录看到的文件内容，并非本地文件SHA-256）：

```text
microsoft/qlib:pyproject.toml
54a44ab5fc28043796da48fc58a8c808c5922aca
microsoft/qlib:.pre-commit-config.yaml
15f00414c93d1614cf0e3dc1fe45c62cc317de0d
kernc/backtesting.py:pyproject.toml
c48c1a1465777fe74c1099a034be040355c7ac9e
kernc/backtesting.py:CONTRIBUTING.md
7e05f3bc133a5dc571be1283fbfdd4b94e71751b
pst-group/pysystemtrade:pyproject.toml
825e01e625872bab486b99a27f45a34c8309ebaa
```
