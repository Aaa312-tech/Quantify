# 数据契约

`configs/project.toml`声明数据快照、参考实验和完整性清单的相对路径及数据截止日。当前快照为 `data/processed/2026-11-18/bundle.joblib`。载入前检查SHA-256及截止日期；joblib仅用于本地受信任的快照和模型。

| 字段 | 内容 |
| --- | --- |
| `meta` | 证券代码、类别、名称、行业 |
| `raws` | 各证券按日期索引的未复权OHLC、成交量与成交额 |
| `actions` | 除权、登记、支付、上市日期与来源 |
| `calendar` | 有序交易日历 |
| `panels`、`scores` | 日期×证券的历史面板与基础评分 |
| `table` | 日期索引、证券、合格状态、特征、标签及标签结束日 |
| `features` | 模型使用的57列有序特征名，读取时转换历史名称但不改变顺序 |
| `cutoff` | 数据完成截止日 |
| `opening_status`（可选） | 日期→证券→状态；包含带时区的`known_at`及布尔`suspended`，必须在开盘竞价前可用 |

`y1/y3/y5`、`end1/end3/end5`、`status1/status3/status5`配套使用。`dataset.label_mask`按真实标签结束日净化训练样本；`features`包含价格、动量、横截面及行业变换。动量和行业排名只使用同日数据，历史前缀通过重建比较验证。历史字段映射集中在`configs/archive.toml`；列名冲突会拒绝载入。

`market.legal_label`处理合法可成交标签，`ledger.causal_price_index`处理因果价格调整。原始来源清单在 `docs/references/data_lineage`，历史方法比较和迁移记录在`docs/references/provenance`。公司行为PDF按证券代码和公告日期命名。原记录中的路径、标识和来源哈希保留原文，可通过`configs/archive.toml`查找当前位置。

新数据需要新日期目录，核查交易日、重复记录、OHLC、流动性、公司行为和历史可用性，再登记配置与完整性清单。修改路径不会自动采集行情。`configs/project.toml`的`protocol`指向当前注册的修正协议；原实验协议保存在`configs/experiment.json`，不得改写。

开盘报价函数不能读取当日收盘、最高、最低或全日量额来决定成交。缺失开盘状态不等于已证明可成交；现有快照没有经过认证的开盘状态历史。执行输出应注明代理模型和单独的滑点成本。
