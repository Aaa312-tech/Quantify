# 使用与来源

本项目尚未授予开放源代码许可证。仓库公开可见不代表其中的代码、行情数据或第三方资料获得重新授权。

Python依赖、赛事材料、公告及数据分别遵循各自的使用条件。原始材料和数据来源保存在 `docs/references`，冻结研究输入由SHA-256清单标识。

wheel包含Python代码、类型标记、HTML模板和包说明；源码包另带离线示例及环境锁。不包含行情、模型或第三方PDF。Git仓库中的研究材料另按各自来源追溯，发布软件版本不为这些材料授予再分发权。`examples/offline_backtest.py`中的价格和日历由程序生成，不是交易所数据。

安装时应保留各依赖包自带的许可证及声明。直接运行依赖的版本、许可元数据和许可文件哈希记录在[发布检查](artifacts/checks/release_acceptance_2026-11-23/dependency_licenses.json)中；这份盘点不代替依赖的完整使用条件。

模块划分和命名参考Qlib、Backtesting.py、pysystemtrade，未复制这些仓库的策略实现。具体采用内容及源码链接见[开发约定](CONTRIBUTING.md)。
