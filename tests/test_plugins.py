import pytest

from ayaan import plugins
from ayaan.rules import validate_config
from ayaan.settings import Settings


def _settings(tmp_path):
    exe = tmp_path / "claude"
    exe.write_text("")
    return Settings(cli_executables=f"claude={exe}")


def test_parse_default_and_rejects_bad_entry():
    assert [p for p, _, _ in plugins.parse(plugins.DEFAULT)] == ["ponytail", "superpowers"]
    assert plugins.parse(" , ") == []
    with pytest.raises(ValueError):
        plugins.parse("ponytail@ponytail|git@github.com:x/y.git")
    assert "CLAUDE_PLUGINS" in validate_config({"CLAUDE_PLUGINS": "nope"})
    assert validate_config({"CLAUDE_PLUGINS": plugins.DEFAULT}) == {}


async def test_sync_adds_marketplace_then_installs_and_tolerates_already(tmp_path):
    calls = []

    async def run(exe, *args):
        calls.append(args)
        if args[:2] == ("plugin", "marketplace"):
            return 1, "Marketplace 'ponytail' is already added"
        return (0, "ok") if args[-1].startswith("ponytail") else (1, "plugin not found")

    res = await plugins.sync(plugins.DEFAULT, _settings(tmp_path), run=run)
    assert calls[0][:3] == ("plugin", "marketplace", "add") and calls[1] == (
        "plugin",
        "install",
        "ponytail@ponytail",
    )
    assert [r["ok"] for r in res] == [True, False]
    assert "plugin not found" in res[1]["detail"]


async def test_sync_without_executable_reports_each_plugin():
    res = await plugins.sync(plugins.DEFAULT, Settings(cli_executables="claude=relative/claude"))
    assert [r["ok"] for r in res] == [False, False]
