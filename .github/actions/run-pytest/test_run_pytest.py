"""Tests for run_pytest.py. Run: uv run --no-project --with pytest pytest .github/actions/run-pytest"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import run_pytest as rp  # noqa: E402


@pytest.mark.parametrize("value, expected", [
    ("tests", ["tests"]),
    ("tests --ignore=tests/integration", ["tests", "--ignore=tests/integration"]),
    ("tests/*", ["tests/*"]),  # no globbing: pytest gets it as written
    ("tests\n-m\nnot integration and not tooling\n", ["tests", "-m", "not integration and not tooling"]),
    ("tests -x\n", ["tests", "-x"]),  # a YAML `|` value ends with a newline; still one line, so space-split
    ("", []),
])
def test_split_args(value, expected):
    assert rp.split_args(value) == expected


@pytest.mark.parametrize("setting, cpu_max, cpus, expected", [
    ("auto", "400000 100000", 24, 4),  # the cgroup quota, not the host's 24 CPUs
    ("auto", "150000 100000", 24, 2),  # a fraction of a CPU rounds up
    ("auto", "max 100000", 6, 6),  # no quota: the CPU count
    ("auto", "max 100000", 64, 8),  # capped
    ("auto", "50000 100000", 2, 1),  # at least one
    ("0", "400000 100000", 24, 0),
    ("12", "400000 100000", 24, 12),  # a number is taken as given, even above the cap
])
def test_resolve_workers(setting, cpu_max, cpus, expected):
    assert rp.resolve_workers(setting, cpu_max, cpus) == expected


def test_passed_count_takes_the_summary_line():
    lines = [
        "tests/test_a.py ....                          [100%]\n",
        "= 1 failed, 45912 passed, 92 skipped, 8 xfailed, 213 warnings, 16688 subtests passed in 500.56s (0:08:20) =\n",
    ]
    assert rp.passed_count(lines) == 45912  # not the "16688 subtests passed"


def test_passed_count_is_zero_without_a_summary():
    assert rp.passed_count(["Traceback ...\n"]) == 0


def test_failed_ids_reads_pytests_cache(tmp_path):
    cache = tmp_path / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text(json.dumps({"b.py::test_two[a b]": True, "a.py::test_one": True}))
    assert rp.failed_ids(tmp_path) == ["a.py::test_one", "b.py::test_two[a b]"]


def test_failed_ids_without_a_cache(tmp_path):
    assert rp.failed_ids(tmp_path) == []


@pytest.mark.parametrize("kwargs, expected", [
    (dict(), []),
    (dict(failed=["a::t"]), ["1 test(s) fail on this branch"]),
    (dict(passed=5, min_passed=100), ["only 5 tests passed (expected at least 100) - the suite did not run"]),
    (dict(passed=0, rc=5), ["no tests were selected"]),
    (dict(passed=0, rc=5, min_passed=1), ["only 0 tests passed (expected at least 1) - the suite did not run"]),
    (dict(coverage=75.0, fail_under=80.0), ["line coverage 75.0% is below the required 80.0%"]),
    (dict(coverage=75.0, fail_under=70.0), []),
    (dict(coverage=None, fail_under=70.0), ["coverage-fail-under is set but coverage was not measured"]),
    (dict(coverage=10.0), []),  # no floor set: coverage never fails the run
])
def test_problems(kwargs, expected):
    args = dict(rc=0, passed=41, failed=[], min_passed=0, coverage=None, fail_under=None)
    assert rp.problems(**{**args, **kwargs}) == expected
