from __future__ import annotations

import argparse
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app.config import AppConfig, ConfigError, ServerConfig, load_config
from app.agent_runtime_collector import collect_agent_runtime_all
from app.cron_manager import collect_cron_jobs, get_cron_job_detail, open_cron_output_file
from app.maintenance_actions import run_backup, run_update
from app.rsync_planner import build_plan
from app.skills_manager import (
    copy_skills_between_servers,
    get_market_skill_detail,
    install_skill,
    list_skills,
    search_market_skills,
    sync_skills_incremental,
)
from app.ssh_runner import SSHRunner
from app.status_collector import collect_all_status
from app.sync_executor import execute_plan
from app.terminal_launcher_macos import open_terminal_for_host
from app.versioning import get_app_version

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = PROJECT_ROOT / "web"
APP_VERSION = get_app_version()


class AppState:
    def __init__(self) -> None:
        self.config = load_config(PROJECT_ROOT)
        self.runner = SSHRunner(self.config.sync.ssh_key_path)
        self.status_lock = threading.Lock()
        self.status_cache: dict = {"servers": {}, "updated_at": None}
        self.runtime_lock = threading.Lock()
        self.agent_runtime_cache: dict = {"servers": {}, "updated_at": None, "window_hours": 24}
        self.plans_lock = threading.Lock()
        self.plans: dict[str, dict] = {}
        self.stop_event = threading.Event()

    def reload_config(self) -> None:
        cfg = load_config(PROJECT_ROOT)
        self.config = cfg
        self.runner = SSHRunner(cfg.sync.ssh_key_path)


state = AppState()


def _normalize_copy_skill_names(skill_name: object, skill_names: object) -> list[str]:
    if isinstance(skill_names, list):
        selected = [item for item in skill_names if isinstance(item, str) and item.strip()]
        if selected:
            return selected
    if isinstance(skill_name, str) and skill_name.strip():
        return [skill_name]
    raise ValueError("skill_names must be non-empty list or skill_name must be non-empty string")


def _resolve_sync_servers(
    config: AppConfig,
    mode: str,
    source_server_input: object,
    target_server_input: object,
) -> tuple[ServerConfig, ServerConfig]:
    if len(config.servers) < 2:
        raise ValueError("At least two servers are required")

    by_key: dict[str, ServerConfig] = {}
    for item in config.servers:
        by_key[item.name] = item
        by_key[item.ssh_host] = item

    source_server: ServerConfig
    target_server: ServerConfig
    if (
        isinstance(source_server_input, str)
        and source_server_input
        and isinstance(target_server_input, str)
        and target_server_input
    ):
        source_server = by_key.get(source_server_input)  # type: ignore[assignment]
        target_server = by_key.get(target_server_input)  # type: ignore[assignment]
        if source_server is None or target_server is None:
            raise ValueError("Unknown source_server or target_server")
    else:
        if len(config.servers) > 2:
            raise ValueError("source_server and target_server are required when servers > 2")
        source_server = config.servers[0]
        target_server = config.servers[1]
        if mode == "b_to_a":
            source_server, target_server = target_server, source_server

    if source_server.name == target_server.name:
        raise ValueError("source_server and target_server must be different")
    return source_server, target_server


def _refresh_status_loop() -> None:
    runtime_interval_seconds = 30
    last_runtime_refresh = 0.0
    while not state.stop_event.is_set():
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            latest = collect_all_status(state.config, state.runner)
            with state.status_lock:
                state.status_cache = {
                    "servers": latest,
                    "updated_at": now_iso,
                }
        except Exception as exc:
            with state.status_lock:
                state.status_cache = {
                    "servers": {},
                    "updated_at": now_iso,
                    "error": f"status refresh failed: {exc}",
                }

        now_ts = time.time()
        if now_ts - last_runtime_refresh >= runtime_interval_seconds:
            try:
                runtime = collect_agent_runtime_all(state.config, state.runner, window_hours=24)
                with state.runtime_lock:
                    state.agent_runtime_cache = {
                        "servers": runtime,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "window_hours": 24,
                    }
            except Exception as exc:
                with state.runtime_lock:
                    state.agent_runtime_cache = {
                        "servers": {},
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "window_hours": 24,
                        "error": f"agent runtime refresh failed: {exc}",
                    }
            last_runtime_refresh = now_ts
        state.stop_event.wait(state.config.poll_interval_seconds)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")
    return value


def _safe_join_web(path: str) -> Path | None:
    candidate = (WEB_ROOT / path).resolve()
    try:
        candidate.relative_to(WEB_ROOT.resolve())
    except ValueError:
        return None
    return candidate


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = f"OpenClawConsole/{APP_VERSION}"

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
            return
        if path == "/sync":
            self._serve_file("sync.html", "text/html; charset=utf-8")
            return
        if path == "/settings":
            self._serve_file("settings.html", "text/html; charset=utf-8")
            return
        if path == "/skills":
            self._serve_file("skills.html", "text/html; charset=utf-8")
            return
        if path == "/cron":
            self._serve_file("cron.html", "text/html; charset=utf-8")
            return
        if path.startswith("/web/"):
            rel = path.removeprefix("/web/")
            self._serve_static(rel)
            return

        if path == "/api/status":
            with state.status_lock:
                _json_response(self, HTTPStatus.OK, state.status_cache)
            return

        if path == "/api/config":
            payload = state.config.to_dict()
            if payload.get("sync", {}).get("ssh_key_path"):
                payload["sync"]["ssh_key_path"] = "***"
            _json_response(self, HTTPStatus.OK, payload)
            return
        if path == "/api/version":
            _json_response(self, HTTPStatus.OK, {"version": APP_VERSION})
            return

        if path == "/api/agent-runtime":
            with state.runtime_lock:
                _json_response(self, HTTPStatus.OK, state.agent_runtime_cache)
            return
        if path == "/api/skills/list":
            try:
                payload = list_skills(state.config, state.runner)
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return
        if path == "/api/cron/list":
            try:
                payload = collect_cron_jobs(state.config, state.runner)
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"detail": f"Not found: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            body = _read_json(self)
        except ValueError as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
            return

        if path == "/api/reload-config":
            try:
                state.reload_config()
            except ConfigError as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, {"ok": True})
            return

        if path == "/api/terminal/open":
            server_name = str(body.get("server", ""))
            selected = next(
                (item for item in state.config.servers if item.ssh_host == server_name or item.name == server_name),
                None,
            )
            if selected is None:
                _json_response(self, HTTPStatus.NOT_FOUND, {"detail": f"Unknown server: {server_name}"})
                return
            ok, message = open_terminal_for_host(selected.ssh_host, state.config.sync.ssh_key_path)
            if not ok:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": message})
                return
            _json_response(self, HTTPStatus.OK, {"ok": True, "server": selected.ssh_host})
            return

        if path == "/api/maintenance/update":
            server_name = body.get("server")
            if server_name is not None and not isinstance(server_name, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "server must be string"})
                return
            try:
                payload = run_update(state.config, state.runner, server_name)
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/maintenance/backup":
            server_name = body.get("server")
            if server_name is not None and not isinstance(server_name, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "server must be string"})
                return
            try:
                payload = run_backup(state.config, state.runner, server_name)
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/skills/install":
            server_name = body.get("server")
            repo_url = body.get("repo_url")
            prompt = body.get("prompt")
            market_path = body.get("market_path")
            market_name = body.get("market_name")
            if server_name is not None and not isinstance(server_name, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "server must be string"})
                return
            if repo_url is not None and not isinstance(repo_url, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "repo_url must be string"})
                return
            if prompt is not None and not isinstance(prompt, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "prompt must be string"})
                return
            if market_path is not None and not isinstance(market_path, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "market_path must be string"})
                return
            if market_name is not None and not isinstance(market_name, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "market_name must be string"})
                return
            if not (repo_url or prompt or market_path):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "repo_url or prompt or market_path is required"})
                return
            try:
                payload = install_skill(
                    config=state.config,
                    runner=state.runner,
                    server=server_name,
                    repo_url=repo_url.strip() if repo_url else None,
                    prompt=prompt.strip() if prompt else None,
                    market_path=market_path.strip() if market_path else None,
                    market_name=market_name.strip() if market_name else None,
                )
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/skills/search-market":
            prompt = body.get("prompt")
            limit = body.get("limit", 5)
            if not isinstance(prompt, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "prompt must be string"})
                return
            try:
                payload = search_market_skills(prompt=prompt, limit=int(limit))
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/skills/market-detail":
            market_path = body.get("market_path")
            market_name = body.get("market_name")
            if market_path is not None and not isinstance(market_path, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "market_path must be string"})
                return
            if market_name is not None and not isinstance(market_name, str):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "market_name must be string"})
                return
            if not (market_path or market_name):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "market_path or market_name is required"})
                return
            try:
                payload = get_market_skill_detail(
                    market_path=market_path.strip() if market_path else None,
                    market_name=market_name.strip() if market_name else None,
                )
            except ValueError as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
                return
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/skills/copy":
            source_server = body.get("source_server")
            target_server = body.get("target_server")
            skill_name = body.get("skill_name")
            skill_names = body.get("skill_names")
            if not isinstance(source_server, str) or not source_server:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "source_server must be non-empty string"})
                return
            if not isinstance(target_server, str) or not target_server:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "target_server must be non-empty string"})
                return
            try:
                selected_names = _normalize_copy_skill_names(skill_name=skill_name, skill_names=skill_names)
                payload = copy_skills_between_servers(
                    config=state.config,
                    runner=state.runner,
                    source_server=source_server,
                    target_server=target_server,
                    skill_names=selected_names,
                )
            except ValueError as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
                return
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/skills/sync":
            servers = body.get("servers")
            if servers is not None:
                if not isinstance(servers, list) or not all(isinstance(item, str) for item in servers):
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "servers must be list[string]"})
                    return
            try:
                payload = sync_skills_incremental(
                    config=state.config,
                    runner=state.runner,
                    servers=servers,
                )
            except ValueError as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
                return
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/cron/detail":
            server_name = body.get("server")
            job_id = body.get("job_id")
            lines = body.get("lines", 200)
            if not isinstance(server_name, str) or not server_name:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "server must be non-empty string"})
                return
            if not isinstance(job_id, str) or not job_id:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "job_id must be non-empty string"})
                return
            try:
                payload = get_cron_job_detail(
                    config=state.config,
                    runner=state.runner,
                    server=server_name,
                    job_id=job_id,
                    lines=int(lines),
                )
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/cron/open-output":
            server_name = body.get("server")
            remote_path = body.get("remote_path")
            if not isinstance(server_name, str) or not server_name:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "server must be non-empty string"})
                return
            if not isinstance(remote_path, str) or not remote_path:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "remote_path must be non-empty string"})
                return
            try:
                payload = open_cron_output_file(
                    project_root=PROJECT_ROOT,
                    config=state.config,
                    runner=state.runner,
                    server=server_name,
                    remote_path=remote_path,
                )
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return
            _json_response(self, HTTPStatus.OK, payload)
            return

        if path == "/api/sync/plan":
            mode = str(body.get("mode", ""))
            if mode not in {"one_way", "bidirectional", "a_to_b", "b_to_a"}:
                _json_response(
                    self,
                    HTTPStatus.BAD_REQUEST,
                    {"detail": "mode must be one_way|bidirectional|a_to_b|b_to_a"},
                )
                return

            roots = body.get("roots")
            if roots is None:
                roots = list(state.config.sync.roots)
            if not isinstance(roots, list) or not all(isinstance(row, str) for row in roots):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "roots must be a list of strings"})
                return

            allow_delete = body.get("allow_delete")
            if allow_delete is None:
                allow_delete = state.config.sync.allow_delete
            else:
                allow_delete = bool(allow_delete)

            source_server_input = body.get("source_server")
            target_server_input = body.get("target_server")

            try:
                source_server, target_server = _resolve_sync_servers(
                    config=state.config,
                    mode=mode,
                    source_server_input=source_server_input,
                    target_server_input=target_server_input,
                )
            except ValueError as exc:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
                return

            source_host = source_server.ssh_host
            target_host = target_server.ssh_host
            plan_mode = "bidirectional" if mode == "bidirectional" else "a_to_b"

            try:
                plan = build_plan(
                    runner=state.runner,
                    mode=plan_mode,
                    source_host=source_host,
                    target_host=target_host,
                    roots=roots,
                    excludes=state.config.sync.excludes,
                    allow_delete=allow_delete,
                )
                plan["source_server"] = source_server.name
                plan["target_server"] = target_server.name
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return

            plan_id = secrets.token_hex(12)
            with state.plans_lock:
                state.plans[plan_id] = {
                    "plan_id": plan_id,
                    "plan": plan,
                    "allow_delete": allow_delete,
                    "excludes": list(state.config.sync.excludes),
                }

            _json_response(self, HTTPStatus.OK, {"plan_id": plan_id, **plan})
            return

        if path == "/api/sync/run":
            plan_id = body.get("plan_id")
            if not isinstance(plan_id, str) or not plan_id:
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "plan_id is required"})
                return

            with state.plans_lock:
                item = state.plans.get(plan_id)
            if item is None:
                _json_response(self, HTTPStatus.NOT_FOUND, {"detail": f"Unknown plan_id: {plan_id}"})
                return

            conflict_resolutions = body.get("conflict_resolutions", [])
            if not isinstance(conflict_resolutions, list):
                _json_response(self, HTTPStatus.BAD_REQUEST, {"detail": "conflict_resolutions must be a list"})
                return

            try:
                result = execute_plan(
                    runner=state.runner,
                    plan=item["plan"],
                    excludes=item["excludes"],
                    allow_delete=item["allow_delete"],
                    conflict_resolutions=conflict_resolutions,
                )
            except Exception as exc:
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(exc)})
                return

            if not result.get("ok"):
                _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": result})
                return

            _json_response(self, HTTPStatus.OK, result)
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"detail": f"Not found: {path}"})

    def _serve_static(self, rel: str) -> None:
        path = _safe_join_web(rel)
        if path is None or not path.exists() or not path.is_file():
            _json_response(self, HTTPStatus.NOT_FOUND, {"detail": "Static file not found"})
            return
        ctype = "text/plain; charset=utf-8"
        if rel.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif rel.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif rel.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, filename: str, ctype: str) -> None:
        path = _safe_join_web(filename)
        if path is None or not path.exists() or not path.is_file():
            _json_response(self, HTTPStatus.NOT_FOUND, {"detail": f"Missing page: {filename}"})
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run(host: str = "127.0.0.1", port: int = 8088) -> None:
    thread = threading.Thread(target=_refresh_status_loop, daemon=True)
    thread.start()

    server = ThreadingHTTPServer((host, port), ConsoleHandler)
    print(f"OpenClaw console listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop_event.set()
        server.server_close()
        time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw Tencent Console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
