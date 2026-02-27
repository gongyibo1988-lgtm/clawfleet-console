from app.config import AppConfig, ServerConfig, SyncConfig
import app.skills_manager as skills_manager
from app.skills_manager import (
    copy_skill_between_servers,
    copy_skills_between_servers,
    get_market_skill_detail,
    install_skill,
    list_skills,
    _parse_market_detail_output,
    search_market_skills,
    sync_skills_incremental,
)
from app.ssh_runner import CommandResult


class FakeRunner:
    def __init__(self, result: CommandResult):
        self.result = result

    def run_ssh(self, host: str, remote_command: str, timeout: int = 30) -> CommandResult:
        return self.result

    def run_local(self, command: list[str], timeout: int = 60) -> CommandResult:
        return self.result

    def ssh_options(self) -> list[str]:
        return ["-o", "StrictHostKeyChecking=accept-new"]


class SpyRunner(FakeRunner):
    def __init__(self, result: CommandResult):
        super().__init__(result)
        self.local_commands: list[list[str]] = []

    def run_local(self, command: list[str], timeout: int = 60) -> CommandResult:
        self.local_commands.append(command)
        return self.result


def _config() -> AppConfig:
    return AppConfig(
        poll_interval_seconds=5,
        servers=[
            ServerConfig(name="server-a", ssh_host="<SSH_USER>@203.0.113.10"),
            ServerConfig(name="server-b", ssh_host="<SSH_USER>@203.0.113.11"),
        ],
        sync=SyncConfig(),
    )


def test_list_skills_parses_json() -> None:
    original = skills_manager._fetch_market_catalog
    skills_manager._fetch_market_catalog = lambda: [  # type: ignore[assignment]
        {"name": "demo", "path": "skills/.curated/demo"},
    ]
    runner = FakeRunner(
        CommandResult(
            returncode=0,
            stdout=(
                '{"skills":['
                '{"name":"custom-one","path":"/root/.openclaw/workspace/skills/custom-one","source":"openclaw_workspace","installed_ts":100,"installed_at":"2026-02-01T00:00:00+00:00"},'
                '{"name":"demo","path":"/root/.openclaw/workspace/skills/demo","source":"openclaw_workspace","installed_ts":200,"installed_at":"2026-02-02T00:00:00+00:00"}'
                ']}'
            ),
            stderr="",
        )
    )
    try:
        payload = list_skills(_config(), runner)  # type: ignore[arg-type]
    finally:
        skills_manager._fetch_market_catalog = original  # type: ignore[assignment]
    assert "server-a" in payload["servers"]
    skills = payload["servers"]["server-a"]["skills"]
    assert skills[0]["name"] == "demo"
    assert skills[0]["skill_type"] == "official"
    assert skills[1]["skill_type"] == "custom"


def test_install_skill_requires_input() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout="", stderr=""))
    try:
        install_skill(_config(), runner, server="all", repo_url=None, prompt=None)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "repo_url or prompt or market_path is required" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_install_skill_market_selected_mode() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout='{"selected_skill":"demo"}', stderr=""))
    payload = install_skill(
        _config(),
        runner,
        server="server-a",
        repo_url=None,
        prompt=None,
        market_path="skills/.curated/notion-research-documentation",
        market_name="notion-research-documentation",
    )
    assert payload["servers"]["server-a"]["mode"] == "market_selected"


def test_search_market_skills_top5() -> None:
    original = skills_manager._search_clawhub_cli
    skills_manager._search_clawhub_cli = lambda prompt, limit: [  # type: ignore[assignment]
        {"name": "twitter-algorithm-optimizer", "path": "clawhub:twitter-algorithm-optimizer", "source": "clawhub", "score": None},
        {"name": "transcribe", "path": "clawhub:transcribe", "source": "clawhub", "score": None},
    ]
    try:
        payload = search_market_skills("twitter", limit=5)
        assert payload["market"] == "clawhub"
        assert payload["candidates"][0]["name"] == "twitter-algorithm-optimizer"
    finally:
        skills_manager._search_clawhub_cli = original  # type: ignore[assignment]


def test_copy_skill_between_servers_validates_name() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout="", stderr=""))
    try:
        copy_skill_between_servers(_config(), runner, "server-a", "server-b", "../bad")
    except ValueError as exc:
        assert "invalid skill_name" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_copy_skills_between_servers_multi() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout="ok", stderr=""))
    payload = copy_skills_between_servers(
        _config(),
        runner,
        "server-a",
        "server-b",
        ["demo", "demo", "nested/skill"],
    )
    assert payload["ok"] is True
    assert payload["ok_count"] == 2
    assert payload["total"] == 2
    assert payload["skill_names"] == ["demo", "nested/skill"]


def test_copy_skill_between_servers_uses_two_stage_rsync() -> None:
    runner = SpyRunner(CommandResult(returncode=0, stdout="ok", stderr=""))
    payload = copy_skill_between_servers(_config(), runner, "server-a", "server-b", "demo")
    assert payload["ok"] is True
    assert len(runner.local_commands) == 2
    pull_cmd = runner.local_commands[0]
    push_cmd = runner.local_commands[1]
    assert pull_cmd[-2].startswith("<SSH_USER>@203.0.113.10:/root/.openclaw/workspace/skills/demo/")
    assert ":" not in pull_cmd[-1]
    assert ":" not in push_cmd[-2]
    assert push_cmd[-1].startswith("<SSH_USER>@203.0.113.11:/root/.openclaw/workspace/skills/demo/")


def test_sync_skills_incremental_builds_actions_and_executes() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout="", stderr=""))
    original_list = skills_manager.list_skills
    original_copy = skills_manager.copy_skill_between_servers
    called: list[tuple[str, str, str]] = []

    skills_manager.list_skills = lambda config, _runner: {  # type: ignore[assignment]
        "servers": {
            "server-a": {
                "server_name": "server-a",
                "skills": [
                    {"name": "alpha", "installed_ts": 100},
                    {"name": "beta", "installed_ts": 90},
                ],
                "error": None,
            },
            "server-b": {
                "server_name": "server-b",
                "skills": [
                    {"name": "alpha", "installed_ts": 80},
                    {"name": "gamma", "installed_ts": 110},
                ],
                "error": None,
            },
        }
    }

    def _fake_copy(config, runner, source_server, target_server, skill_name):  # type: ignore[no-untyped-def]
        called.append((source_server, target_server, skill_name))
        return {
            "generated_at": "2026-02-27T00:00:00+00:00",
            "source_server": source_server,
            "target_server": target_server,
            "skill_name": skill_name,
            "ok": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        }

    skills_manager.copy_skill_between_servers = _fake_copy  # type: ignore[assignment]
    try:
        payload = sync_skills_incremental(_config(), runner)
    finally:
        skills_manager.list_skills = original_list  # type: ignore[assignment]
        skills_manager.copy_skill_between_servers = original_copy  # type: ignore[assignment]

    assert payload["ok"] is True
    assert payload["total_actions"] == 3
    assert payload["ok_count"] == 3
    assert ("server-a", "server-b", "alpha") in called
    assert ("server-a", "server-b", "beta") in called
    assert ("server-b", "server-a", "gamma") in called


def test_parse_market_detail_output_extracts_features_and_examples() -> None:
    text = """
---
description: Analyze papers and summarize insights.
---
# Agentic Paper Digest
## When to use
- Parse arXiv papers
- Build concise summary
## Run (CLI preferred)
```bash
clawhub run research-paper-writer --topic llm
```
"""
    payload = _parse_market_detail_output("research-paper-writer", text)
    assert payload["name"] == "research-paper-writer"
    assert "summarize" in payload["summary"].lower()
    assert any("Parse arXiv papers" in item for item in payload["features"])
    assert any("clawhub run" in item for item in payload["examples"])


def test_get_market_skill_detail_via_cli(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class _Result:
        def __init__(self):
            self.returncode = 0
            self.stdout = "Description: test skill\nFeatures:\n- one\nExamples:\n- run one\n"
            self.stderr = ""

    monkeypatch.setattr(skills_manager.subprocess, "run", lambda *args, **kwargs: _Result())
    payload = get_market_skill_detail("clawhub:test-skill", None)
    assert payload["name"] == "test-skill"
    assert payload["source"] == "clawhub"
    assert payload["features"][0] == "one"
