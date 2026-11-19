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
| `features` | 模型使用的57列有序特征名 |
| `cutoff` | 数据完成截止日 |

`y1/y3/y5`、`end1/end3/end5`、`status1/status3/status5`配套使用。`dataset.label_mask`按真实标签结束日净化训练样本；`features`包含价格、动量、横截面及行业变换。行业特征只使用同日数据，历史前缀通过重建比较验证。

`market.legal_label`处理合法可成交标签，`ledger.causal_price_index`处理因果价格调整。原始来源清单在 `docs/references/data_lineage`；公司行为公告、赛事材料及PDF保留在 `docs/references`。这些记录中的历史路径不参与当前文件定位。

新数据需要新日期目录，核查交易日、重复记录、OHLC、流动性、公司行为和历史可用性，再登记配置与完整性清单。修改路径不会自动采集行情。`configs/experiment_v6.json`是冻结实验协议，不是可任意修改的调参配置。
