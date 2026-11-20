"""Seven independently switchable rules; 16 predeclared two-anchor contrasts."""

RULES = [
    "retain18",
    "replace1",
    "rank_gap3",
    "normal_budget25",
    "caution_budget25",
    "penalty10",
    "deadband2",
]
LABELS = {
    "retain18": "持仓保留名次12→18",
    "replace1": "主动替换最多1次",
    "rank_gap3": "替换排名差至少3个百分点",
    "normal_budget25": "正常仓位成交预算50%→25%",
    "caution_budget25": "警戒仓位新增25%成交预算",
    "penalty10": "每次买卖增加10bp优化惩罚",
    "deadband2": "2个百分点持仓缓冲",
}
CONFIGS = {"baseline": {r: False for r in RULES}}
for r in RULES:
    CONFIGS["add_" + r] = {k: k == r for k in RULES}
CONFIGS["all_on"] = {r: True for r in RULES}
for r in RULES:
    CONFIGS["remove_" + r] = {k: k != r for k in RULES}

TURNOVER_POLICIES = ("legacy", "cost_penalty", "cost_band")


def turnover_for(schedule, date_index):
    """Resolve a registered research turnover policy on the completed signal date."""
    name = schedule if isinstance(schedule, str) else schedule.get(int(date_index), "legacy")
    if name not in TURNOVER_POLICIES:
        raise ValueError("Unregistered turnover policy: " + str(name))
    return name


def config_for(schedule, date_index):
    """Resolve a fixed configuration or a signal-date rule schedule."""
    name = schedule if isinstance(schedule, str) else schedule.get(int(date_index), "baseline")
    if name not in CONFIGS:
        raise ValueError("Unregistered rule configuration: " + str(name))
    return name


def complexity(name):
    """Count enabled turnover controls for deterministic simpler-rule tie breaking."""
    return sum(CONFIGS[name].values())
