from fakes import InMemoryQuery


async def test_put_workspace_null_semantics():
    q = InMemoryQuery()
    repos = [{"repoNm": "r"}]
    await q.put_workspace(
        "P", {"workspacePath": "/w", "defaultTool": "CODEX", "promptTemplate": "t", "repos": repos}
    )
    w = await q.put_workspace("P", {"workspacePath": None, "defaultTool": None, "repos": None})
    assert (w["workspacePath"], w["defaultTool"], w["repos"]) == ("/w", "CODEX", repos)
    assert w["promptTemplate"] is None  # absent = cleared
