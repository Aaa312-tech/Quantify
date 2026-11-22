import pytest
from scripts.check_coverage import compare_coverage, module_totals


def counts(lines=8, statements=10, branches=3, total_branches=4):
    return dict(
        covered_lines=lines,
        num_statements=statements,
        covered_branches=branches,
        num_branches=total_branches,
    )


def test_coverage_preserves_line_and_branch_floors_independently():
    baseline = {"a.py": counts()}
    assert not compare_coverage(baseline, baseline, {})
    assert compare_coverage({"a.py": counts(lines=7)}, baseline, {})
    assert compare_coverage({"a.py": counts(branches=2)}, baseline, {})


def test_split_module_compares_aggregate_counts():
    baseline = {"a.py": counts()}
    current = {"a.py": counts(4, 5, 1, 2), "b.py": counts(4, 5, 2, 2)}
    assert not compare_coverage(current, baseline, {"a.py": ["a.py", "b.py"]})
    assert compare_coverage(current, baseline, {})


@pytest.mark.parametrize(
    "current,groups",
    [({}, {}), ({"a.py": counts()}, {"a.py": []}), ({"a.py": counts()}, {"b.py": ["a.py"]})],
)
def test_missing_or_unregistered_coverage_is_rejected(current, groups):
    assert compare_coverage(current, {"a.py": counts()}, groups)


def test_branchless_module_still_requires_line_coverage():
    baseline = {"a.py": counts(2, 2, 0, 0)}
    assert not compare_coverage(baseline, baseline, {})
    assert compare_coverage({"a.py": counts(1, 2, 0, 0)}, baseline, {})


def test_installed_package_paths_are_normalized_without_dropping_modules():
    report = {
        "meta": {"branch_coverage": True},
        "files": {r"C:\env\Lib\site-packages\quantfolio\ledger.py": {"summary": counts()}},
    }
    assert module_totals(report) == {"src/quantfolio/ledger.py": counts()}
    report["meta"]["branch_coverage"] = False
    with pytest.raises(ValueError, match="Branch coverage"):
        module_totals(report)
