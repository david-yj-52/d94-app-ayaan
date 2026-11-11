from ayaan.settings import Settings


def test_executable_map_parses_pairs():
    s = Settings(cli_executables="claude=/a/claude, codex=/b/codex")
    assert s.executable_map == {"claude": "/a/claude", "codex": "/b/codex"}


def test_resolve_exe_rejects_relative_override(tmp_path, monkeypatch):
    from ayaan.settings import resolve_exe

    s = Settings(_env_file=None, cli_executables="claude=bin/claude, codex=/opt/codex")
    assert resolve_exe("CLAUDE", s) is None
    assert resolve_exe("codex", s) == "/opt/codex"
    exe = tmp_path / "gemini"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert resolve_exe("GEMINI", s) == str(exe)
    assert resolve_exe("git", s) is None
    monkeypatch.setenv("PATH", ".")
    monkeypatch.chdir(tmp_path)
    assert resolve_exe("gemini", s) is None  # relative PATH entry
