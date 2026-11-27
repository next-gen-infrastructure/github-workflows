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
osint-a = { path = "../osint-a", editable = true }
osint-b = { path = "../osint-b/" }
other-c = { path = "../other-c" }
inner = { workspace = true }
remote = { git = "https://github.com/acme/osint-remote" }
local = { path = "vendor/osint-local" }
# osint-commented = { path = "../osint-commented" }

[tool.other]
osint-d = { path = "../osint-d" }
"""


def write(directory, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text(text)


def test_sibling_names_takes_only_path_dependencies_one_level_up(tmp_path):
    write(tmp_path, PYPROJECT)
    assert sb.sibling_names(tmp_path, "") == ["osint-a", "osint-b", "other-c"]
    assert sb.sibling_names(tmp_path, "osint-") == ["osint-a", "osint-b"]


def test_sibling_names_without_a_pyproject(tmp_path):
    assert sb.sibling_names(tmp_path, "") == []


def test_sibling_names_adds_up_every_reader(tmp_path, monkeypatch):
    write(tmp_path, PYPROJECT)
    monkeypatch.setattr(sb, "READERS", [sb.from_pyproject, lambda repo_dir, prefix: ["osint-z"]])
    assert sb.sibling_names(tmp_path, "osint-") == ["osint-a", "osint-b", "osint-z"]


def test_listed_repos_drops_comments_blanks_and_duplicates():
    text = "osint-b  # the second\n\n# only a comment\nosint-a\nosint-b\n"
    assert sb.listed_repos(text) == ["osint-a", "osint-b"]


@pytest.mark.parametrize("line", ["osint a", "../osint-a", "-x", "osint-a/sub"])
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
    deps = {"app": ["osint-a", "osint-b"], "osint-a": ["osint-c", "osint-b"], "osint-b": ["osint-c"], "osint-c": []}
    workspace, clone_one, calls = make_world(tmp_path, deps)
    assert sb.clone_closure(workspace, "osint-", clone_one) == ["osint-a", "osint-b", "osint-c"]
    assert sorted(calls) == ["osint-a", "osint-b", "osint-c"]  # each cloned once


def test_clone_closure_adds_listed_repos_as_roots_and_follows_them(tmp_path):
    deps = {"app": ["osint-a"], "osint-a": [], "extra": ["osint-b"], "osint-b": []}
    workspace, clone_one, _ = make_world(tmp_path, deps)
    assert sb.clone_closure(workspace, "osint-", clone_one, ["extra"]) == ["extra", "osint-a", "osint-b"]


def test_clone_closure_with_only_a_list(tmp_path):
    workspace = tmp_path / "ws" / "app"
    workspace.mkdir(parents=True)  # no pyproject.toml: a project the readers know nothing about
    _, clone_one, calls = make_world(tmp_path, {"app": [], "tool": []})
    assert sb.clone_closure(workspace, "", clone_one, ["tool"]) == ["tool"]
    assert calls == ["tool"]


def test_clone_closure_skips_a_directory_that_exists(tmp_path):
    workspace, clone_one, calls = make_world(tmp_path, {"app": ["osint-a"], "osint-a": []})
    (tmp_path / "ws" / "osint-a").mkdir()
    assert sb.clone_closure(workspace, "osint-", clone_one) == []
    assert calls == []


def test_clone_closure_stops_on_a_failed_clone(tmp_path):
    workspace, _, _ = make_world(tmp_path, {"app": ["osint-a"], "osint-a": []})

    def failing(repo):
        sys.exit("clone failed")

    with pytest.raises(SystemExit):
        sb.clone_closure(workspace, "osint-", failing)


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
    sb.clone("osint-a", tmp_path, "acme", "main", "dev", git)
    assert git.tried == ["main"]


def test_clone_falls_back_when_the_ref_is_missing(tmp_path):
    git = FakeGit({"dev"})
    sb.clone("osint-a", tmp_path, "acme", "feature-x", "dev", git)
    assert git.tried == ["feature-x", "dev"]


def test_clone_without_a_fallback_fails(tmp_path):
    git = FakeGit({"dev"})
    with pytest.raises(SystemExit, match="branch main not in osint-a"):
        sb.clone("osint-a", tmp_path, "acme", "main", "", git)
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
    make_repo(tmp_path, "osint-a")
    sb.stage(["osint-a"], tmp_path, tmp_path / "out", [])
    staged = sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert staged == ["osint-a/README.md", "osint-a/docs/big.txt", "osint-a/pyproject.toml", "osint-a/src/mod.py"]


def test_stage_only_the_listed_files(tmp_path):
    make_repo(tmp_path, "osint-a")
    sb.stage(["osint-a"], tmp_path, tmp_path / "out", ["src", "pyproject.toml"])
    staged = sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert staged == ["osint-a/pyproject.toml", "osint-a/src/mod.py"]


def test_stage_replaces_what_was_there(tmp_path):
    make_repo(tmp_path, "osint-a")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "stale").write_text("old")
    sb.stage(["osint-a"], tmp_path, tmp_path / "out", ["README.md"])
    assert not (tmp_path / "out" / "stale").exists()


@pytest.mark.parametrize("name, message", [("nope.txt", "osint-a has no nope.txt"), ("../escape", "refusing"), ("/etc/passwd", "refusing")])
def test_stage_rejects_a_missing_or_escaping_path(tmp_path, name, message):
    make_repo(tmp_path, "osint-a")
    with pytest.raises(SystemExit, match=message):
        sb.stage(["osint-a"], tmp_path, tmp_path / "out", [name])
