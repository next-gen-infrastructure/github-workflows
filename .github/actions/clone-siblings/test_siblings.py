"""Tests for siblings.py. Run: uv run --no-project --with pytest pytest .github/actions/clone-siblings"""
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import siblings as sb  # noqa: E402

PYPROJECT = """
[project]
name = "x"

[tool.uv.sources]
lib-a = { path = "../lib-a", editable = true }
lib-b = { path = "../lib-b/" }
other-c = { path = "../other-c" }
inner = { workspace = true }
remote = { git = "https://github.com/acme/lib-remote" }
local = { path = "vendor/lib-local" }
# lib-commented = { path = "../lib-commented" }

[tool.other]
lib-d = { path = "../lib-d" }
"""


def write(directory, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text(text)


def test_sibling_names_takes_only_path_dependencies_one_level_up(tmp_path):
    write(tmp_path, PYPROJECT)
    assert sb.sibling_names(tmp_path, "") == ["lib-a", "lib-b", "other-c"]
    assert sb.sibling_names(tmp_path, "lib-") == ["lib-a", "lib-b"]


def test_sibling_names_without_a_pyproject(tmp_path):
    assert sb.sibling_names(tmp_path, "") == []


def test_sibling_names_adds_up_every_reader(tmp_path, monkeypatch):
    write(tmp_path, PYPROJECT)
    monkeypatch.setattr(sb, "READERS", [sb.from_pyproject, lambda repo_dir, prefix: ["lib-z"]])
    assert sb.sibling_names(tmp_path, "lib-") == ["lib-a", "lib-b", "lib-z"]


def test_listed_repos_drops_comments_and_blanks_and_keeps_the_last_line_of_a_repo():
    text = "lib-b  # the second\n\n# only a comment\nlib-a\nlib-b src\n"
    assert sb.listed_repos(text) == {"lib-b": ["src"], "lib-a": []}


def test_listed_repos_takes_the_paths_to_stage_after_the_name():
    assert sb.listed_repos("lib-data entries schemas  # data\n") == {"lib-data": ["entries", "schemas"]}


@pytest.mark.parametrize("line", ["../lib-a", "-x", "lib-a/sub"])
def test_listed_repos_rejects_a_bad_name(line):
    with pytest.raises(SystemExit, match="refusing sibling repo"):
        sb.listed_repos(line)


def make_world(tmp_path, deps):
    """A workspace whose siblings are described by `deps` {repo: [its siblings]}; returns (workspace, clone_one)."""
    workspace = tmp_path / "ws" / "app"
    write(workspace, "[tool.uv.sources]\n" + "".join(f'{d} = {{ path = "../{d}" }}\n' for d in deps["app"]))
    calls = []

    def clone_one(repo):
        calls.append(repo)
        write(tmp_path / "ws" / repo, "[tool.uv.sources]\n" + "".join(f'{d} = {{ path = "../{d}" }}\n' for d in deps[repo]))

    return workspace, clone_one, calls


def test_clone_closure_follows_siblings_of_siblings_once(tmp_path):
    deps = {"app": ["lib-a", "lib-b"], "lib-a": ["lib-c", "lib-b"], "lib-b": ["lib-c"], "lib-c": []}
    workspace, clone_one, calls = make_world(tmp_path, deps)
    assert sb.clone_closure(workspace, "lib-", clone_one) == ["lib-a", "lib-b", "lib-c"]
    assert sorted(calls) == ["lib-a", "lib-b", "lib-c"]  # each cloned once


def test_clone_closure_adds_listed_repos_as_roots_and_follows_them(tmp_path):
    deps = {"app": ["lib-a"], "lib-a": [], "extra": ["lib-b"], "lib-b": []}
    workspace, clone_one, _ = make_world(tmp_path, deps)
    assert sb.clone_closure(workspace, "lib-", clone_one, ["extra"]) == ["extra", "lib-a", "lib-b"]


def test_clone_closure_with_only_a_list(tmp_path):
    workspace = tmp_path / "ws" / "app"
    workspace.mkdir(parents=True)  # no pyproject.toml: a project the readers know nothing about
    _, clone_one, calls = make_world(tmp_path, {"app": [], "tool": []})
    assert sb.clone_closure(workspace, "", clone_one, ["tool"]) == ["tool"]
    assert calls == ["tool"]


def test_clone_closure_skips_a_directory_that_exists(tmp_path):
    workspace, clone_one, calls = make_world(tmp_path, {"app": ["lib-a"], "lib-a": []})
    (tmp_path / "ws" / "lib-a").mkdir()
    assert sb.clone_closure(workspace, "lib-", clone_one) == []
    assert calls == []


def test_clone_closure_stops_on_a_failed_clone(tmp_path):
    workspace, _, _ = make_world(tmp_path, {"app": ["lib-a"], "lib-a": []})

    def failing(repo):
        sys.exit("clone failed")

    with pytest.raises(SystemExit):
        sb.clone_closure(workspace, "lib-", failing)


class FakeGit:
    """Pretends `git clone`: succeeds only for the branches in `branches`."""

    def __init__(self, branches):
        self.branches, self.tried = branches, []

    def __call__(self, cmd, **kwargs):
        branch = cmd[cmd.index("-b") + 1]
        self.tried.append(branch)
        return subprocess.CompletedProcess(cmd, 0 if branch in self.branches else 128)


def test_clone_uses_the_ref_when_it_exists(tmp_path):
    git = FakeGit({"main"})
    sb.clone("lib-a", tmp_path, "acme", "main", "dev", git)
    assert git.tried == ["main"]


def test_clone_falls_back_when_the_ref_is_missing(tmp_path):
    git = FakeGit({"dev"})
    sb.clone("lib-a", tmp_path, "acme", "feature-x", "dev", git)
    assert git.tried == ["feature-x", "dev"]


def test_clone_without_a_fallback_fails(tmp_path):
    git = FakeGit({"dev"})
    with pytest.raises(SystemExit, match="branch main not in lib-a"):
        sb.clone("lib-a", tmp_path, "acme", "main", "", git)
    assert git.tried == ["main"]


def make_repo(parent, name):
    repo = parent / name
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "mod.py").write_text("x = 1")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("secret")
    (repo / "pyproject.toml").write_text("[project]")
    (repo / "README.md").write_text("# readme")
    (repo / "docs").mkdir()
    (repo / "docs" / "big.txt").write_text("big")
    return repo


def test_stage_everything_but_git(tmp_path):
    make_repo(tmp_path, "lib-a")
    sb.stage(["lib-a"], tmp_path, tmp_path / "out", [])
    staged = sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert staged == ["lib-a/README.md", "lib-a/docs/big.txt", "lib-a/pyproject.toml", "lib-a/src/mod.py"]


def test_stage_only_the_listed_files(tmp_path):
    make_repo(tmp_path, "lib-a")
    sb.stage(["lib-a"], tmp_path, tmp_path / "out", ["src", "pyproject.toml"])
    staged = sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert staged == ["lib-a/pyproject.toml", "lib-a/src/mod.py"]


def test_stage_a_listed_repo_takes_its_own_paths(tmp_path):
    make_repo(tmp_path, "lib-a")
    data = tmp_path / "lib-data"
    (data / "entries").mkdir(parents=True)
    (data / "entries" / "e.json").write_text("{}")
    (data / "schemas").mkdir()
    (data / "schemas" / "s.json").write_text("{}")
    (data / "README.md").write_text("# data")
    sb.stage(["lib-a", "lib-data"], tmp_path, tmp_path / "out", ["src"], {"lib-data": ["entries", "schemas"]})
    staged = sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert staged == ["lib-a/src/mod.py", "lib-data/entries/e.json", "lib-data/schemas/s.json"]


def test_stage_a_listed_repo_stays_strict_about_its_own_paths(tmp_path):
    make_repo(tmp_path, "lib-a")
    with pytest.raises(SystemExit, match="lib-a has no entries"):
        sb.stage(["lib-a"], tmp_path, tmp_path / "out", ["src"], {"lib-a": ["entries"]})


def test_stage_replaces_what_was_there(tmp_path):
    make_repo(tmp_path, "lib-a")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "stale").write_text("old")
    sb.stage(["lib-a"], tmp_path, tmp_path / "out", ["README.md"])
    assert not (tmp_path / "out" / "stale").exists()


@pytest.mark.parametrize("name, message", [("nope.txt", "lib-a has no nope.txt"), ("../escape", "refusing"), ("/etc/passwd", "refusing")])
def test_stage_rejects_a_missing_or_escaping_path(tmp_path, name, message):
    make_repo(tmp_path, "lib-a")
    with pytest.raises(SystemExit, match=message):
        sb.stage(["lib-a"], tmp_path, tmp_path / "out", [name])
