"""Tests for `tl setup` helpers."""

import json
import sys
from pathlib import Path

from tl_cli.commands import setup
from tl_cli.commands.setup import (
    _bundled_skill_blurbs,
    _find_claude_binary,
    _install_command_shim,
    _installed_plugin_version,
    _remove_matching_standalone_skills,
    _trees_identical,
    _update_plugin,
)


def _write_skill(skills_dir: Path, name: str, body: str) -> None:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


class TestBundledSkillBlurbs:
    def test_reads_name_and_blurb_sorted(self, tmp_path):
        skills = tmp_path / "skills"
        _write_skill(skills, "tl", "---\nname: tl\ntl-blurb: data analyst\ndescription: |\n  Long desc.\n---\n")
        _write_skill(skills, "alpha", "---\nname: alpha\ntl-blurb: first thing\ndescription: x\n---\n")
        assert _bundled_skill_blurbs(tmp_path) == [
            ("alpha", "first thing"),
            ("tl", "data analyst"),
        ]

    def test_skips_skill_without_blurb(self, tmp_path):
        skills = tmp_path / "skills"
        _write_skill(skills, "tl", "---\nname: tl\ntl-blurb: has one\ndescription: x\n---\n")
        _write_skill(skills, "other", "---\nname: other\ndescription: no blurb here\n---\n")
        assert _bundled_skill_blurbs(tmp_path) == [("tl", "has one")]

    def test_ignores_blurb_lookalike_in_body(self, tmp_path):
        # A `tl-blurb:` line in the markdown body (after frontmatter) must not be picked up.
        skills = tmp_path / "skills"
        _write_skill(
            skills,
            "tl",
            "---\nname: tl\ntl-blurb: real blurb\ndescription: x\n---\n\ntl-blurb: not this one\n",
        )
        assert _bundled_skill_blurbs(tmp_path) == [("tl", "real blurb")]

    def test_missing_skills_dir_returns_empty(self, tmp_path):
        assert _bundled_skill_blurbs(tmp_path) == []


class TestTreesIdentical:
    def test_identical_trees(self, tmp_path):
        for root in ("a", "b"):
            d = tmp_path / root / "sub"
            d.mkdir(parents=True)
            (d / "f.md").write_text("same", encoding="utf-8")
        assert _trees_identical(tmp_path / "a", tmp_path / "b")

    def test_different_content(self, tmp_path):
        for root, body in (("a", "one"), ("b", "two")):
            d = tmp_path / root
            d.mkdir()
            (d / "f.md").write_text(body, encoding="utf-8")
        assert not _trees_identical(tmp_path / "a", tmp_path / "b")

    def test_ignores_pycache_artifacts(self, tmp_path):
        for root in ("a", "b"):
            d = tmp_path / root / "scripts"
            d.mkdir(parents=True)
            (d / "run.py").write_text("print()", encoding="utf-8")
        cache = tmp_path / "b" / "scripts" / "__pycache__"
        cache.mkdir()
        (cache / "run.cpython-313.pyc").write_text("bytecode", encoding="utf-8")
        assert _trees_identical(tmp_path / "a", tmp_path / "b")

    def test_extra_file(self, tmp_path):
        for root in ("a", "b"):
            d = tmp_path / root
            d.mkdir()
            (d / "f.md").write_text("same", encoding="utf-8")
        (tmp_path / "b" / "extra.md").write_text("x", encoding="utf-8")
        assert not _trees_identical(tmp_path / "a", tmp_path / "b")


class TestRemoveMatchingStandaloneSkills:
    def _plugin_with_skill(self, root: Path, name: str, body: str) -> Path:
        skill = root / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(body, encoding="utf-8")
        return skill

    def test_removes_identical_copy(self, tmp_path, monkeypatch):
        plugin_root = tmp_path / "plugin"
        self._plugin_with_skill(plugin_root, "tl", "---\nname: tl\n---\n")
        standalone = tmp_path / "claude-skills"
        monkeypatch.setattr(setup, "CLAUDE_SKILLS_DIR", standalone)
        copy = standalone / "tl"
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text("---\nname: tl\n---\n", encoding="utf-8")

        assert _remove_matching_standalone_skills(plugin_root) == (1, 0)
        assert not copy.exists()

    def test_keeps_modified_copy(self, tmp_path, monkeypatch):
        plugin_root = tmp_path / "plugin"
        self._plugin_with_skill(plugin_root, "tl", "---\nname: tl\n---\n")
        standalone = tmp_path / "claude-skills"
        monkeypatch.setattr(setup, "CLAUDE_SKILLS_DIR", standalone)
        copy = standalone / "tl"
        copy.mkdir(parents=True)
        (copy / "SKILL.md").write_text("---\nname: tl\n---\nuser edit\n", encoding="utf-8")

        assert _remove_matching_standalone_skills(plugin_root) == (0, 1)
        assert copy.exists()

    def test_ignores_unrelated_personal_skills(self, tmp_path, monkeypatch):
        plugin_root = tmp_path / "plugin"
        self._plugin_with_skill(plugin_root, "tl", "---\nname: tl\n---\n")
        standalone = tmp_path / "claude-skills"
        monkeypatch.setattr(setup, "CLAUDE_SKILLS_DIR", standalone)
        other = standalone / "my-own-skill"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("mine", encoding="utf-8")

        assert _remove_matching_standalone_skills(plugin_root) == (0, 0)
        assert other.exists()


class TestInstallCommandShim:
    def test_writes_shim_pointing_at_plugin_skill(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup, "CLAUDE_COMMANDS_DIR", tmp_path / "commands")
        dst = _install_command_shim()
        assert dst == tmp_path / "commands" / "tl.md"
        body = dst.read_text(encoding="utf-8")
        assert "tl-cli:tl" in body
        assert "$ARGUMENTS" in body


class TestFindClaudeBinary:
    def test_prefers_execpath_env(self, tmp_path, monkeypatch):
        exe = tmp_path / "claude.exe"
        exe.write_text("", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_CODE_EXECPATH", str(exe))
        assert _find_claude_binary() == str(exe)

    def test_ignores_stale_execpath_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_EXECPATH", str(tmp_path / "gone.exe"))
        monkeypatch.setattr(setup.shutil, "which", lambda _: "/somewhere/claude")
        assert _find_claude_binary() == "/somewhere/claude"

    def test_prefers_path(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
        monkeypatch.setattr(setup.shutil, "which", lambda _: "/somewhere/claude")
        assert _find_claude_binary() == "/somewhere/claude"

    def test_finds_newest_desktop_app_binary(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)
        monkeypatch.setattr(setup.Path, "home", staticmethod(lambda: tmp_path))
        if sys.platform == "win32":
            base = tmp_path / "AppData" / "Roaming" / "Claude" / "claude-code"
            monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
            exe = "claude.exe"
        elif sys.platform == "darwin":
            base = tmp_path / "Library" / "Application Support" / "Claude" / "claude-code"
            exe = "claude"
        else:
            base = tmp_path / ".config" / "Claude" / "claude-code"
            exe = "claude"
        for version in ("2.1.165", "2.1.170"):
            d = base / version
            d.mkdir(parents=True)
            (d / exe).write_text("", encoding="utf-8")
        assert _find_claude_binary() == str(base / "2.1.170" / exe)

    def test_falls_back_to_local_bin(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)
        monkeypatch.setattr(setup.Path, "home", staticmethod(lambda: tmp_path))
        exe = "claude.exe" if sys.platform == "win32" else "claude"
        target = tmp_path / ".local" / "bin" / exe
        target.parent.mkdir(parents=True)
        target.write_text("", encoding="utf-8")
        assert _find_claude_binary() == str(target)

    def test_not_found_anywhere(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
        monkeypatch.setattr(setup.shutil, "which", lambda _: None)
        monkeypatch.setattr(setup.Path, "home", staticmethod(lambda: tmp_path))
        if sys.platform == "win32":
            monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
        assert _find_claude_binary() is None


class TestSharedSupportInstall:
    def _plugin_with_shared(self, tmp_path: Path) -> Path:
        plugin_root = tmp_path / "plugin"
        skills = plugin_root / "skills"
        _write_skill(skills, "tl", "---\nname: tl\n---\n")
        shared = skills / "_shared"
        shared.mkdir()
        (shared / "tl_data.py").write_text("x = 1\n", encoding="utf-8")
        return plugin_root

    def test_skill_trees_carry_shared_dir(self, tmp_path):
        plugin_root = self._plugin_with_shared(tmp_path)
        target = tmp_path / "target"
        assert setup._install_skill_trees(plugin_root, target) == 1
        assert (target / "_shared" / "tl_data.py").read_text() == "x = 1\n"

    def test_skill_trees_refresh_stale_shared_copy(self, tmp_path):
        plugin_root = self._plugin_with_shared(tmp_path)
        target = tmp_path / "target"
        stale = target / "_shared"
        stale.mkdir(parents=True)
        (stale / "tl_data.py").write_text("old = 1\n", encoding="utf-8")
        setup._install_skill_trees(plugin_root, target)
        assert (target / "_shared" / "tl_data.py").read_text() == "x = 1\n"

    def test_standalone_install_carries_shared_dir(self, tmp_path, monkeypatch):
        plugin_root = self._plugin_with_shared(tmp_path)
        monkeypatch.setattr(setup, "CLAUDE_SKILLS_DIR", tmp_path / "cs")
        monkeypatch.setattr(setup, "CLAUDE_COMMANDS_DIR", tmp_path / "cc")
        assert setup._install_standalone_skills(plugin_root) == 1
        assert (tmp_path / "cs" / "_shared" / "tl_data.py").exists()


class TestInstalledPluginVersion:
    def _record(self, tmp_path, monkeypatch, payload):
        plugins = tmp_path / "plugins"
        plugins.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(setup, "CLAUDE_PLUGINS_DIR", plugins)
        (plugins / "installed_plugins.json").write_text(json.dumps(payload), encoding="utf-8")
        return plugins

    def test_reads_version_from_install_record(self, tmp_path, monkeypatch):
        self._record(tmp_path, monkeypatch, {"version": 2, "plugins": {setup.PLUGIN_KEY: [{"scope": "user", "version": "0.7.2"}]}})
        assert _installed_plugin_version() == "0.7.2"

    def test_prefers_user_scope(self, tmp_path, monkeypatch):
        self._record(
            tmp_path,
            monkeypatch,
            {"plugins": {setup.PLUGIN_KEY: [{"scope": "project", "version": "0.1.0"}, {"scope": "user", "version": "0.9.9"}]}},
        )
        assert _installed_plugin_version() == "0.9.9"

    def test_ignores_unknown_version(self, tmp_path, monkeypatch):
        self._record(tmp_path, monkeypatch, {"plugins": {setup.PLUGIN_KEY: [{"scope": "user", "version": "unknown"}]}})
        assert _installed_plugin_version() is None

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup, "CLAUDE_PLUGINS_DIR", tmp_path / "plugins")
        assert _installed_plugin_version() is None

    def test_unreadable_record_is_unknown_not_outdated(self, tmp_path, monkeypatch):
        plugins = tmp_path / "plugins"
        plugins.mkdir(parents=True)
        monkeypatch.setattr(setup, "CLAUDE_PLUGINS_DIR", plugins)
        (plugins / "installed_plugins.json").write_text("{not json", encoding="utf-8")
        assert _installed_plugin_version() is None
        assert not [w for w in setup.check_plugin_version() if "Claude Code plugin" in w]

    def test_unexpected_shape_is_unknown(self, tmp_path, monkeypatch):
        self._record(tmp_path, monkeypatch, {"plugins": {setup.PLUGIN_KEY: {"scope": "user", "version": "0.7.2"}}})
        assert _installed_plugin_version() is None


class TestCheckPluginVersion:
    def test_warns_on_record_behind_cli(self, tmp_path, monkeypatch):
        plugins = tmp_path / "plugins"
        plugins.mkdir(parents=True)
        monkeypatch.setattr(setup, "CLAUDE_PLUGINS_DIR", plugins)
        monkeypatch.setattr(setup, "OPENCODE_SKILLS_DIR", tmp_path / "opencode")
        monkeypatch.setattr(setup, "AGENTS_SKILLS_DIR", tmp_path / "agents")
        (plugins / "installed_plugins.json").write_text(
            json.dumps({"plugins": {setup.PLUGIN_KEY: [{"scope": "user", "version": "0.7.2"}]}}), encoding="utf-8"
        )
        # A stamp claiming the current version must not mask the real one.
        (plugins / "tl-cli").mkdir()
        (plugins / "tl-cli" / ".version").write_text(setup.__version__)

        warnings = setup.check_plugin_version()
        assert any("0.7.2" in w for w in warnings)

    def test_falls_back_to_stamp_without_record(self, tmp_path, monkeypatch):
        plugins = tmp_path / "plugins"
        (plugins / "tl-cli").mkdir(parents=True)
        monkeypatch.setattr(setup, "CLAUDE_PLUGINS_DIR", plugins)
        monkeypatch.setattr(setup, "OPENCODE_SKILLS_DIR", tmp_path / "opencode")
        monkeypatch.setattr(setup, "AGENTS_SKILLS_DIR", tmp_path / "agents")
        (plugins / "tl-cli" / ".version").write_text("0.1.0")

        assert any("0.1.0" in w for w in setup.check_plugin_version())

    def test_silent_when_nothing_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup, "CLAUDE_PLUGINS_DIR", tmp_path / "plugins")
        monkeypatch.setattr(setup, "OPENCODE_SKILLS_DIR", tmp_path / "opencode")
        monkeypatch.setattr(setup, "AGENTS_SKILLS_DIR", tmp_path / "agents")
        assert setup.check_plugin_version() == []


class TestUpdatePlugin:
    def test_already_latest_is_not_a_change(self, monkeypatch):
        monkeypatch.setattr(setup, "_run_claude", lambda args, b: (True, 'tl-cli is already at the latest version (0.9.9).'))
        assert _update_plugin("claude") == (True, False, 'tl-cli is already at the latest version (0.9.9).')

    def test_version_advance_is_a_change(self, monkeypatch):
        monkeypatch.setattr(setup, "_run_claude", lambda args, b: (True, 'Plugin "tl-cli" updated from 0.7.2 to 0.9.9.'))
        ok, changed, _ = _update_plugin("claude")
        assert (ok, changed) == (True, True)

    def test_failure_is_not_a_change(self, monkeypatch):
        monkeypatch.setattr(setup, "_run_claude", lambda args, b: (False, "boom"))
        assert _update_plugin("claude") == (False, False, "boom")


class TestSetupCallSequence:
    def _stub_env(self, tmp_path, monkeypatch, record=None):
        plugin_root = tmp_path / "plugin"
        (plugin_root / "skills").mkdir(parents=True)
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        if record is not None:
            (plugins / "installed_plugins.json").write_text(json.dumps(record), encoding="utf-8")
        monkeypatch.setattr(setup, "_find_plugin_root", lambda: plugin_root)
        monkeypatch.setattr(setup, "_find_claude_binary", lambda: "/usr/bin/claude")
        monkeypatch.setattr(setup, "CLAUDE_PLUGINS_DIR", plugins)
        monkeypatch.setattr(setup, "CLAUDE_SKILLS_DIR", tmp_path / "skills")
        monkeypatch.setattr(setup, "CLAUDE_COMMANDS_DIR", tmp_path / "commands")
        calls = []
        monkeypatch.setattr(setup, "_run_claude", lambda args, b: (calls.append(args), (True, "ok"))[1])
        return calls, plugins

    def test_interactive_updates_after_install(self, tmp_path, monkeypatch):
        calls, _ = self._stub_env(tmp_path, monkeypatch)
        setup.setup_claude(json_output=False, toon_output=False)
        assert calls.index(["plugin", "update", setup.PLUGIN_KEY]) > calls.index(["plugin", "install", setup.PLUGIN_KEY])

    def test_noninteractive_updates_after_install(self, tmp_path, monkeypatch, capsys):
        calls, _ = self._stub_env(tmp_path, monkeypatch)
        setup._setup_noninteractive()
        assert calls.index(["plugin", "update", setup.PLUGIN_KEY]) > calls.index(["plugin", "install", setup.PLUGIN_KEY])
        assert json.loads(capsys.readouterr().out)["plugin_updated"] is True

    def test_stamp_records_installed_version_not_cli_version(self, tmp_path, monkeypatch, capsys):
        record = {"plugins": {setup.PLUGIN_KEY: [{"scope": "user", "version": "0.7.2"}]}}
        _, plugins = self._stub_env(tmp_path, monkeypatch, record=record)
        setup._setup_noninteractive()
        assert (plugins / "tl-cli" / ".version").read_text() == "0.7.2"
        assert json.loads(capsys.readouterr().out)["plugin_version"] == "0.7.2"

    def test_standalone_fallback_stamps_cli_version(self, tmp_path, monkeypatch, capsys):
        _, plugins = self._stub_env(tmp_path, monkeypatch)
        monkeypatch.setattr(setup, "_find_claude_binary", lambda: None)
        setup._setup_noninteractive()
        assert (plugins / "tl-cli" / ".version").read_text() == setup.__version__
