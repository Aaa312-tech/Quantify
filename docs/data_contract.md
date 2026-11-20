# 数据契约

`configs/project.toml`声明活跃快照`bundle`、冻结核验快照`reference_bundle`、研究评估`evaluation`及完整性清单。当前活跃快照位于`data/processed/2026-11-19`；父快照`2026-11-18-reviewed-dividends`、前次修正`2026-11-18-reviewed-actions`和原快照`2026-11-18`均保留。载入前检查SHA-256及各自截止日期；joblib仅用于本地受信任的快照和模型。

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

交易规则需要生效区间。[上交所2026年修订说明](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml)明确主板风险警示股票自7月6日起改为10%涨跌幅。当前静态元数据不能据此补写此前的ST状态；历史限幅与上市初期例外仍待按日期核查，来源保存在`docs/references/exchange_rules_2026`。

修正公司行为保留`cash_pay_date`公告日期，另用`cash_available_date`表达开盘可用时间；缺失值保持未知。`ex_reference_cash_per_share`与`ex_reference_share_multiplier`只调整价格参考，不能替代实际权益。来源哈希、页码、标签变化与未解决限制随快照归档。

`fetch-bars`把未复权日线响应写到`data/raw/<date>`，核对证券身份、字段、时间范围、OHLC和历史重叠；成交量单位为手、成交额为元。缺失目标日期、历史修订和下载失败分别保留。下载完成不代表数据已通过研究验收，仍需核查公司行为、证券状态和跨源差异后另建处理快照。

`fetch-bars --retry`只补采指定批次中失败或未请求的证券，使用新目录并记录前次批次的哈希。不得覆盖成功响应或把其他快照的补采混入本次数据。11月19日采集已覆盖594只证券；历史重叠中的102个换手率修订、183个两位小数差异及93个成交额整元截断差异均保留，历史OHLC和成交量未发现修订。跨源抽查12只证券的288个价格值一致，12个科创板成交量差异符合整手舍入，但仍保留原严格比较的复核状态。

已经终止的`PARTIAL`采集可以与后续批次一起登记。生成器核验每个成功响应，拒绝重复证券、未结束批次、未知来源及未登记的价格修订；合并后仍须覆盖整个研究股票池。单个批次中断不等于整个输入已经完整，失败请求不会转换成行情或停牌证明。

`preprocessing.build_dataset`从原始行情和公司行为生成全部面板、资格、评分、57个模型输入及1/3/5日标签。资格门槛、评分权重和先转换float32再排名的顺序沿用原始契约；594只证券、696日及413424行的全部106列已精确复现，见`artifacts/checks/market_pipeline_2026-11-20/rebuild.json`。这项复现不代表历史证券状态或行业信息已获时点认证。

`refresh-data --protocol configs/protocols/market_extension.json --output data/processed/<new-name>`只接收登记的采集、补采和公告证据。历史重叠和缺失值保留原样，新行情只从父快照截止日之后追加；缺失历史金额的绿的谐波继续隔离。生成后要求历史原始行情、面板、评分及非标签列完全相同，标签新增可观察结果单独记录。新目录不能覆盖，也不会自动切换活跃快照；切换前须完成对应训练、评估和身份核验。
