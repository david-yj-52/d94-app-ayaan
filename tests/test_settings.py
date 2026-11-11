from ayaan.settings import Settings


def test_executable_map_parses_pairs():
    s = Settings(cli_executables="claude=/a/claude, codex=/b/codex")
    assert s.executable_map == {"claude": "/a/claude", "codex": "/b/codex"}
