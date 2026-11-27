"""Run the project's tests for python-test: parallel, failures re-run serially, coverage, a summary line.

Stdlib only; configured by environment variables (see action.yml). `uv sync` has already run.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

MAX_WORKERS = 8


def split_args(value):
    """One argument per line when the value has a newline, else split on spaces."""
    value = value.strip()
    return [line.strip() for line in value.splitlines() if line.strip()] if "\n" in value else value.split()


def resolve_workers(setting, cpu_max, cpu_count):
    """xdist workers: a number as given; `auto` is one per CPU of the cgroup quota, 1 to MAX_WORKERS.

    `cpu_max` is the content of /sys/fs/cgroup/cpu.max ("400000 100000", or "max 100000" without a quota).
    """
    if setting != "auto":
        return int(setting)
    quota, period = cpu_max.split()
    cpus = cpu_count if quota == "max" else -(-int(quota) // int(period))
    return max(1, min(cpus, MAX_WORKERS))


def passed_count(lines):
    """Tests passed, from pytest's last summary line ("45912 passed, 92 skipped ... in 500.56s")."""
    for line in reversed(lines):
        match = re.search(r"(\d+) passed", line)
        if match:
            return int(match.group(1))
    return 0


def failed_ids(cache_dir):
    """Node ids pytest recorded as failing, exact (they come from its own cache)."""
    path = pathlib.Path(cache_dir) / "v" / "cache" / "lastfailed"
    return sorted(json.loads(path.read_text())) if path.exists() else []


def problems(passed, failed, min_passed, coverage, fail_under):
    """Why the job must fail, as messages; empty means it passes."""
    found = []
    if passed < min_passed:
        found.append(f"only {passed} tests passed (expected at least {min_passed}) - the suite did not run")
    if failed:
        found.append(f"{len(failed)} test(s) fail on this branch")
    if fail_under is not None and coverage is None:
        found.append("coverage-fail-under is set but coverage was not measured")
    elif fail_under is not None and coverage < fail_under:
        found.append(f"line coverage {coverage}% is below the required {fail_under}%")
    return found


def run(cmd, env=None):
    """Run pytest, echoing its output as it comes; returns (exit code, output lines)."""
    lines = []
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        sys.stdout.write(line)
        lines.append(line)
    return proc.wait(), lines


def installed(module):
    probe = ["uv", "run", "--frozen", "--no-sync", "python", "-c", f"import {module}"]
    return subprocess.run(probe, capture_output=True).returncode == 0


def main():
    env = os.environ
    out = pathlib.Path(env["RUNNER_TEMP"]) / "pytest"
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache"
    fail_under = float(env["COV_FAIL_UNDER"]) if env.get("COV_FAIL_UNDER") else None

    cpu_max = pathlib.Path("/sys/fs/cgroup/cpu.max")
    workers = resolve_workers(env["WORKERS"], cpu_max.read_text() if cpu_max.exists() else "max 100000",
                              os.cpu_count() or 1)
    if workers and not installed("xdist"):
        if env["WORKERS"] != "auto":
            sys.exit(f"::error::workers={workers} needs pytest-xdist in the project's test dependencies")
        print("::notice::pytest-xdist is not a dependency of this project: running serially")
        workers = 0

    coverage = installed("pytest_cov")
    if not coverage:
        if fail_under is not None:
            sys.exit("::error::coverage-fail-under needs pytest-cov in the project's test dependencies")
        print("::notice::pytest-cov is not a dependency of this project: coverage is not measured")

    base = ["uv", "run", "--frozen", "pytest", *split_args(env["PYTEST_ARGS"]),
            "--continue-on-collection-errors", "-o", f"cache_dir={cache}"]
    cmd = base + (["-n", str(workers)] if workers else [])
    run_env = dict(env, COVERAGE_FILE=str(out / ".coverage"), COVERAGE_CORE="sysmon")
    if coverage:
        cmd += ["--cov", "--cov-report=", f"--cov-report=json:{out / 'coverage.json'}"]

    rc, lines = run(cmd, run_env)
    (out / "head.log").write_text("".join(lines))
    # 0 passed, 1 tests failed. 2 interrupted, 3 internal error, 4 usage error, 5 no tests selected: nothing measured.
    if rc >= 2:
        sys.exit(f"::error::pytest exited with {rc} before it could measure the suite")
    failed = failed_ids(cache)
    if rc == 1 and not failed:
        sys.exit("::error::pytest reported failures but none could be identified from its cache")

    # xdist decides which worker runs what, so a test that fails only because of the order or the worker it
    # landed on shows up in one run and not the next. Re-run the failures serially; keep those that fail again.
    order_dependent = []
    if failed and workers:
        print(f"Re-running {len(failed)} failed test(s) serially")
        run(base + ["--lf", "--lfnf=none", "-n", "0"], run_env)
        still_failing = failed_ids(cache)
        order_dependent = sorted(set(failed) - set(still_failing))
        failed = still_failing
    for test in order_dependent:
        print(f"::warning::{test} failed in the parallel run and passed serially: it depends on what ran before it")

    percent = None
    report = out / "coverage.json"
    if report.exists():
        percent = round(json.loads(report.read_text())["totals"]["percent_covered"], 2)
    passed = passed_count(lines)
    with open(env.get("GITHUB_OUTPUT", os.devnull), "a") as fh:
        fh.write(f"passed={passed}\n")
    with open(env.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as fh:
        fh.write(f"pytest: {passed} passed, {len(failed)} failed, {len(order_dependent)} order-dependent, "
                 f"coverage {'n/a' if percent is None else f'{percent}%'}\n")

    errors = problems(passed, failed, int(env["MIN_PASSED"]), percent, fail_under)
    for message in errors:
        print(f"::error::{message}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
