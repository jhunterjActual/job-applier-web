"""Scripted loopback rehearsal for the actual native upstream CLI.

Offline only. No provider forwarding, credential discovery or paid dispatcher.
Explicit invocation starts owned processes; import has no runtime side effects.
"""
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from uuid import UUID

from campaign_state import CampaignRefused


MODEL = "claude-sonnet-5-5"
FAKE_KEY = "synthetic-loopback-only-not-a-provider-key"
MAX_BODY = 16 * 1024 * 1024


class RehearsalRefused(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_once(path, raw):
    with Path(path).open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii") + b"\n"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RehearsalRefused("Duplicate native JSON key")
        result[key] = value
    return result


def local_path(root, value):
    if not isinstance(value, str) or not value or any(char in value for char in ("\0", "\n", "\r", "$", "`")):
        raise RehearsalRefused("Invalid native path")
    original = Path(value)
    original = original.absolute() if original.is_absolute() else root / original
    resolved = original.resolve()
    if not resolved.is_relative_to(root):
        raise RehearsalRefused("Native path escaped owned workspace")
    for part in (original, *original.parents):
        if part == root.parent:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise RehearsalRefused("Linked native path refused")
    relative = resolved.relative_to(root)
    if any(part.lower() in {".git", "data"} or part.lower().startswith(".env") for part in relative.parts):
        raise RehearsalRefused("Protected native path")
    return resolved


class ToolPolicy:
    def __init__(self, root, source_hashes):
        self.root = Path(root).resolve(strict=True)
        self.source_hashes = dict(source_hashes)

    def validate(self, name, args):
        if not isinstance(args, dict):
            raise RehearsalRefused("Invalid native tool input")
        fields = {
            "Read": {"file_path", "offset", "limit", "pages"},
            "Write": {"file_path", "content"},
            "Edit": {"file_path", "old_string", "new_string", "replace_all"},
            "Glob": {"pattern", "path"},
            "Grep": {"pattern", "path", "glob", "type", "output_mode", "head_limit", "offset", "multiline", "-i", "-n", "-A", "-B", "-C"},
            "Agent": {"description", "subagent_type", "prompt", "run_in_background", "model"},
            "Bash": {"command", "timeout", "description"},
        }
        if name not in fields or set(args) - fields[name]:
            raise RehearsalRefused("Unknown native tool arguments")
        if name == "Bash" and "timeout" in args and (type(args["timeout"]) is not int or not 1 <= args["timeout"] <= 120000):
            raise RehearsalRefused("Unbounded native command timeout")
        if name in {"Write", "Edit"}:
            for key in (("content",) if name == "Write" else ("old_string", "new_string")):
                if not isinstance(args.get(key), str) or len(args[key].encode("utf-8")) > 2 * 1024 * 1024:
                    raise RehearsalRefused("Unbounded native write")
            if "replace_all" in args and type(args["replace_all"]) is not bool:
                raise RehearsalRefused("Invalid native edit flag")
        if name in {"Read", "Write", "Edit"}:
            path = local_path(self.root, args.get("file_path"))
            relative = path.relative_to(self.root).as_posix()
            if name != "Read":
                parts = path.relative_to(self.root).parts
                if (relative in self.source_hashes or not parts or
                    (relative != "job_search_tracker.csv" and parts[0] not in {"cv", "cover_letters", "documents", ".scratch"}) or
                    path.suffix.lower() not in {".tex", ".md", ".txt", ".csv"}):
                    raise RehearsalRefused("Native write outside generated artifacts")
        elif name in {"Glob", "Grep"}:
            local_path(self.root, args.get("path", str(self.root)))
            pattern = args.get("pattern", "")
            if not isinstance(pattern, str) or (name == "Glob" and (pattern.startswith("/") or any(token in pattern for token in ("..", ":", "\\", "$", "`", "\n", "\r", "~")))):
                raise RehearsalRefused("Unsafe native discovery pattern")
        elif name == "Agent":
            if args.get("subagent_type") != "application-reviewer" or args.get("run_in_background") or args.get("model") not in (None, "inherit", MODEL):
                raise RehearsalRefused("Unsupported native reviewer")
        elif name == "Bash":
            command = args.get("command")
            basename = r"[A-Za-z0-9_-]+"
            compile_pattern = rf"cd (cv|cover_letters) && xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error -output-directory=\. ({basename}\.tex)"
            verify_pattern = rf"python tools/verify_pdf\.py (cv|cover_letters)/{basename}\.pdf --pages [12] --min-chars [1-9][0-9]{{0,3}} --dump-text \.scratch/{basename}\.txt"
            layout_pattern = rf"python tools/verify_layout\.py (cv|cover_letters)/{basename}\.pdf"
            if not isinstance(command, str) or not any(re.fullmatch(pattern, command) for pattern in (compile_pattern, verify_pattern, layout_pattern)):
                raise RehearsalRefused("Unapproved native command; no implicit expansion")
        else:
            raise RehearsalRefused("Unapproved native tool")


def sse(tool, number, text="SYNTHETIC_MECHANICS_COMPLETE"):
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0, "service_tier": "standard", "speed": "standard", "inference_geo": "global"}
    events = [{"type": "message_start", "message": {"id": f"msg_fixture_{number}", "type": "message", "role": "assistant", "model": MODEL, "content": [], "stop_reason": None, "usage": usage}}]
    if tool:
        name, args = tool
        block = {"type": "tool_use", "id": f"toolu_fixture_{number}", "name": name, "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(args)}
    else:
        block = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": text}
    events += [{"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_delta", "index": 0, "delta": delta},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use" if tool else "end_turn", "stop_sequence": None}, "usage": usage},
        {"type": "message_stop"}]
    return "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events).encode("utf-8")


class ScriptedBroker:
    """Serve frozen mechanical responses; no outbound HTTP implementation exists."""
    def __init__(self, campaign, prefix, directory, policy, actions):
        if campaign.policy["mode"] != "offline":
            raise RehearsalRefused("Scripted broker accepts offline campaign only")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", prefix):
            raise RehearsalRefused("Invalid request prefix")
        self.campaign, self.prefix, self.directory, self.policy = campaign, prefix, Path(directory), policy
        self.actions = tuple((name, json.loads(json.dumps(args))) for name, args in actions)
        for name, args in self.actions:
            policy.validate(name, args)
        self.index, self.requests, self.reviewer_requests, self.count_requests = 0, [], 0, 0
        self.tool_results, self.failed, self.read_ids = {}, False, set()
        self.expected_tool_ids = set()
        self.response_settled = threading.Event()
        self.refusals = []

    def dispatch(self, raw):
        # Snapshot exact bytes before either admission or response selection.
        raw = bytes(raw)
        if not 0 < len(raw) <= MAX_BODY:
            raise RehearsalRefused("Native body bound exceeded")
        if self.failed:
            raise RehearsalRefused("First failure retained; no automatic retry")
        self.response_settled.clear()
        value = json.loads(raw, object_pairs_hook=unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(RehearsalRefused("Nonfinite native JSON")))
        envelope_fields = {"model", "messages", "system", "max_tokens", "stream", "thinking",
            "tools", "output_config", "metadata", "context_management"}
        if (not isinstance(value, dict) or value.get("model") != MODEL or value.get("stream") is not True
            or type(value.get("max_tokens")) is not int or value["max_tokens"] != 32768
            or not isinstance(value.get("messages"), list) or set(value) - envelope_fields):
            raise RehearsalRefused("Unsupported native request")
        if (value.get("thinking") != {"type": "adaptive", "display": "omitted"}
            or value.get("output_config") != {"effort": "medium"}
            or value.get("context_management") != {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]}):
            raise RehearsalRefused("Native settings changed")
        definitions = value.get("tools", [])
        definition_fields = {"name", "description", "input_schema", "type", "strict", "defer_loading"}
        if not isinstance(definitions, list) or any(not isinstance(tool, dict) or not isinstance(tool.get("name"), str)
            or set(tool) - definition_fields or tool.get("type", "custom") != "custom"
            or not isinstance(tool.get("input_schema"), dict) or tool["input_schema"].get("type") != "object"
            for tool in definitions):
            raise RehearsalRefused("Invalid native tool definitions")
        tools = {tool["name"] for tool in definitions}
        if len(tools) != len(definitions):
            raise RehearsalRefused("Duplicate native tool definition")
        # The pinned CLI advertises its inert deferred placeholder even with an
        # explicit tool list. It is never executable by our response policy.
        if not tools.issubset({"Read", "Write", "Edit", "Glob", "Grep", "Bash", "Agent", "DeferredToolPlaceholder"}):
            raise RehearsalRefused("Unsupported native tool definitions")
        reviewer = "Agent" not in tools and "Write" not in tools
        def inspect(node, permitted_visual=False):
            if isinstance(node, dict):
                if "cache_control" in node:
                    raise RehearsalRefused("Native cache setting changed")
                kind = node.get("type")
                if kind == "tool_result":
                    tool_id = node.get("tool_use_id")
                    if not isinstance(tool_id, str):
                        raise RehearsalRefused("Invalid native tool result")
                    if node.get("is_error") is True:
                        raise RehearsalRefused("Native tool failed; retain first failure")
                    self.tool_results[tool_id] = False
                    permitted_visual = tool_id in self.read_ids
                if isinstance(kind, str) and kind in {"image", "document"} and not permitted_visual:
                    raise RehearsalRefused("Visual input without owned native Read")
                if isinstance(kind, str) and (kind in {"server_tool_use", "web_search_tool_result"} or kind.startswith(("web_search_", "web_fetch_", "computer_"))):
                    raise RehearsalRefused("External/server tools refused")
                for item in node.values():
                    inspect(item, permitted_visual)
            elif isinstance(node, list):
                for item in node:
                    inspect(item, permitted_visual)
        inspect(value)
        number = len(self.requests) + 1
        request_path = self.directory / f"request-{number:03d}.json"
        write_once(request_path, raw)
        call_id = f"{self.prefix}_c{number:03d}"
        self.campaign.reserve(call_id, "0.000001", f"mechanical/request-{number:03d}")
        # Never mutate request bytes after admission; these hashes bind the served
        # fixture. Synthetic usage settlement is not a real provider price quote.
        action = None
        if reviewer:
            self.reviewer_requests += 1
        elif self.index < len(self.actions):
            action = self.actions[self.index]
            self.policy.validate(*action)
            self.index += 1
            self.expected_tool_ids.add(f"toolu_fixture_{number}")
            if action[0] == "Read":
                self.read_ids.add(f"toolu_fixture_{number}")
        response = sse(action, number)
        write_once(self.directory / f"response-{number:03d}.sse", response)
        self.requests.append({"call_id": call_id, "request_sha256": digest(raw), "response_sha256": digest(response), "reviewer": reviewer})
        return call_id, response

    def server(self):
        broker = self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *args):
                pass

            def do_POST(self):
                call_id = None
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if (not 0 < length <= MAX_BODY or self.headers.get("x-api-key") != FAKE_KEY
                        or self.headers.get("Transfer-Encoding")):
                        raise RehearsalRefused("Unapproved loopback request")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise RehearsalRefused("Truncated loopback request")
                    if self.path in {"/v1/messages/count_tokens", "/messages/count_tokens"}:
                        # Mechanical counter only; never forwards a count request.
                        broker.count_requests += 1
                        if broker.count_requests > 100:
                            raise RehearsalRefused("Mechanical token-count ceiling")
                        response = b'{"input_tokens":10}'
                        content_type = "application/json"
                    elif self.path in {"/v1/messages", "/v1/messages?beta=true", "/messages"}:
                        call_id, response = broker.dispatch(raw)
                        content_type = "text/event-stream"
                    else:
                        raise RehearsalRefused("Unsupported loopback route")
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                    self.wfile.flush()
                    if call_id:
                        broker.campaign.settle(call_id, "0", "mechanical/zero-provider-cost")
                        broker.response_settled.set()
                except Exception as error:
                    broker.failed = True
                    frame = error.__traceback__
                    while frame is not None and frame.tb_next is not None:
                        frame = frame.tb_next
                    broker.refusals.append({"failure_class": type(error).__name__,
                        "reason": str(error) if isinstance(error, RehearsalRefused) else "scripted_request_failed",
                        "failure_line": frame.tb_lineno if frame is not None else None,
                        "failure_function": frame.tb_frame.f_code.co_name if frame is not None else None})
                    # If delivery was admitted, a write/settle exception leaves the
                    # durable reservation pending. There is no retry/reconciliation.
                    try:
                        self.send_error(403, "Scripted rehearsal refused")
                    except OSError:
                        pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 5
        return server


def child_environment(directory, port, executables):
    directory = Path(directory)
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "ComSpec", "PATHEXT") if key in os.environ}
    for name in ("home", "config", "appdata", "localappdata", "temp"):
        (directory / name).mkdir(exist_ok=False)
    directories = list(dict.fromkeys(str(Path(path).parent) for path in executables.values()))
    if "SystemRoot" in env:
        directories.append(str(Path(env["SystemRoot"]) / "System32"))
    env.update(PATH=os.pathsep.join(directories), ANTHROPIC_API_KEY=FAKE_KEY,
        ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}", CLAUDE_CODE_GIT_BASH_PATH=str(executables["bash"]),
        HOME=str(directory / "home"), USERPROFILE=str(directory / "home"),
        CLAUDE_CONFIG_DIR=str(directory / "config"), APPDATA=str(directory / "appdata"),
        LOCALAPPDATA=str(directory / "localappdata"), TEMP=str(directory / "temp"), TMP=str(directory / "temp"),
        DISABLE_PROMPT_CACHING="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        CLAUDE_CODE_DISABLE_FAST_MODE="1", CLAUDE_CODE_MAX_RETRIES="0", CLAUDE_CODE_MAX_OUTPUT_TOKENS="32768",
        CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR="1", openin_any="p", openout_any="p", shell_escape="f")
    return env


def stop_owned_process(process):
    """Bounded cleanup of this exact Popen owner; never claim descendant absence."""
    result = {"owned_pid": process.pid, "direct_child_terminal": process.poll() is not None}
    if result["direct_child_terminal"]:
        return result
    result["process_tree_requires_independent_verification"] = True
    try:
        if os.name == "nt":
            killer = Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"
            terminated = subprocess.run([str(killer), "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15, check=False)
            result["tree_termination_exit_code"] = terminated.returncode
        else:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=15)
    except BaseException as error:
        result["cleanup_failure_class"] = type(error).__name__
        # Even a failed tree stop must not abandon a still-live direct owner.
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        except BaseException as final_error:
            result["direct_cleanup_failure_class"] = type(final_error).__name__
    result["direct_child_terminal"] = process.poll() is not None
    return result


def reused_environment(previous, directory, port, executables):
    """Rebuild from allowlisted owned paths; never inherit a supplied environment."""
    allowed = {"SystemRoot", "WINDIR", "ComSpec", "PATHEXT", "PATH", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_GIT_BASH_PATH", "HOME", "USERPROFILE", "CLAUDE_CONFIG_DIR", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
        "DISABLE_PROMPT_CACHING", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "CLAUDE_CODE_DISABLE_FAST_MODE",
        "CLAUDE_CODE_MAX_RETRIES", "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR",
        "openin_any", "openout_any", "shell_escape"}
    if not isinstance(previous, dict) or set(previous) - allowed or previous.get("ANTHROPIC_API_KEY") != FAKE_KEY:
        raise RehearsalRefused("Reused runtime contains unapproved keys")
    # All reusable writable paths must remain within this explicit proof root.
    proof_root = Path(directory).parent.resolve(strict=True)
    for key in ("HOME", "USERPROFILE", "CLAUDE_CONFIG_DIR", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        path = Path(previous.get(key, ""))
        if not path.is_dir() or not path.resolve().is_relative_to(proof_root):
            raise RehearsalRefused("Reused runtime escaped owned proof root")
        for item in (path, *path.parents):
            if item == proof_root.parent:
                break
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise RehearsalRefused("Linked reused runtime refused")
    # Regenerate every fixed value using a disposable subdirectory, then retain
    # only the already-owned native session/config paths.
    seed = Path(directory) / "environment"
    seed.mkdir()
    env = child_environment(seed, port, executables)
    for key in ("HOME", "USERPROFILE", "CLAUDE_CONFIG_DIR", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        env[key] = previous[key]
    return env


def verify_runtime(workspace, commit, source_hashes, executables, executable_hashes):
    required = {"cli", "python", "xelatex", "pdftoppm", "pdftotext", "pdfinfo", "git", "bash"}
    if (not re.fullmatch(r"[0-9a-f]{40}", commit) or not required.issubset(executables)
        or any(not re.fullmatch(r"support_[0-9]{3}", key) for key in set(executables) - required)
        or set(executable_hashes) != set(executables)):
        raise RehearsalRefused("Exact source/runtime bindings required")
    for name, path in executables.items():
        if not Path(path).is_file() or digest(Path(path).read_bytes()) != executable_hashes[name]:
            raise RehearsalRefused("Pinned executable changed")
    result = subprocess.run([str(executables["git"]), "-C", str(workspace), "rev-parse", "HEAD"], capture_output=True, timeout=15, check=False)
    if result.returncode != 0 or result.stdout.decode("ascii").strip() != commit:
        raise RehearsalRefused("Pinned upstream commit changed")
    if not source_hashes:
        raise RehearsalRefused("Explicit source file hashes required")
    listing = subprocess.run([str(executables["git"]), "-C", str(workspace), "ls-files", "-z"], capture_output=True, timeout=15, check=False)
    if listing.returncode != 0 or set(listing.stdout.decode("utf-8").rstrip("\0").split("\0")) != set(source_hashes):
        raise RehearsalRefused("Exact tracked-source inventory required")
    for name, expected in source_hashes.items():
        path = local_path(Path(workspace).resolve(strict=True), name)
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise RehearsalRefused("Pinned source bytes changed")


def run_phase(campaign, product, case, stage, workspace, commit, source_hashes,
              executables, executable_hashes, directory, session_id, prompt, actions,
              runtime_environment=None, timeout_seconds=240):
    """Run one explicit offline native phase; never forwards provider traffic."""
    if campaign.policy["mode"] != "offline" or stage not in {"fit", "materials"} or (stage == "materials" and case not in {f"V{i:02d}" for i in range(1, 7)}):
        raise RehearsalRefused("Offline eligible native phase required")
    if str(UUID(session_id)) != session_id or type(prompt) is not bytes or not prompt or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
        raise RehearsalRefused("Explicit bounded native session required")
    workspace = Path(workspace).absolute()
    directory = Path(directory).absolute()
    for root in (workspace, directory):
        for item in (root, *root.parents):
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise RehearsalRefused("Linked rehearsal root refused")
    workspace = workspace.resolve(strict=True)
    if directory.exists() or not directory.parent.is_dir():
        raise RehearsalRefused("New explicit rehearsal directory required")
    verify_runtime(workspace, commit, source_hashes, executables, executable_hashes)
    policy = ToolPolicy(workspace, source_hashes)
    broker = ScriptedBroker(campaign, f"{product}_{case}_{stage}", directory, policy, actions)
    reviewer = (workspace / ".claude/agents/application-reviewer.md").read_text(encoding="utf-8").split("---", 2)[-1].strip()
    directory.mkdir(exist_ok=False)
    agents = {"application-reviewer": {"description": "Native application reviewer", "prompt": reviewer, "tools": ["Read", "Glob", "Grep"], "model": MODEL}}
    agents_path = directory / "agents.json"
    write_once(agents_path, json_bytes(agents))
    write_once(directory / "prompt.txt", prompt)
    write_once(directory / "actions.json", json_bytes(broker.actions))
    command = [str(executables["cli"]), "--model", MODEL, "--strict-mcp-config", "--setting-sources", "",
        "--agents", str(agents_path), "--append-system-prompt-file", str(workspace / "CLAUDE.md"),
        "--tools", "Read,Write,Edit,Glob,Grep,Bash,Agent", "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash,Agent(application-reviewer)",
        "--permission-mode", "dontAsk", "--output-format", "json", "--max-turns", "40",
        "--session-id" if stage == "fit" else "--resume", session_id, "-p"]
    receipt = {"classification": "scripted_native_mechanics_only", "provider_forwarding": False,
        "product": product, "case": case, "stage": stage, "commit": commit,
        "started_utc": datetime.now(timezone.utc).isoformat(), "status": "incomplete", "native_terminal": False,
        "source_inventory_sha256": digest(json_bytes(source_hashes)), "executables_sha256": digest(json_bytes(executable_hashes)),
        "prompt_sha256": digest(prompt), "actions_sha256": digest(json_bytes(broker.actions))}
    started = time.monotonic()
    server, thread, process, env = None, None, None, None
    try:
        server = broker.server()
        receipt["loopback_port"] = server.server_port
        env = child_environment(directory, server.server_port, executables) if runtime_environment is None else reused_environment(runtime_environment, directory, server.server_port, executables)
        campaign.begin_stage(product, case, stage)
        receipt["stage_admitted"] = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=os.name != "nt", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        receipt["native_pid"] = process.pid
        stdout, stderr = process.communicate(input=prompt, timeout=timeout_seconds)
        write_once(directory / "stdout.json", stdout)
        write_once(directory / "stderr.txt", stderr)
        value = json.loads(stdout)
        settled = broker.response_settled.wait(timeout=5) if broker.requests else False
        success = (process.returncode == 0 and value.get("type") == "result" and value.get("subtype") == "success"
            and value.get("is_error") is False and not value.get("permission_denials") and not broker.failed
            and settled and broker.index == len(broker.actions) and broker.expected_tool_ids.issubset(broker.tool_results)
            and (not any(name == "Agent" for name, _ in broker.actions) or broker.reviewer_requests >= 1))
        verify_runtime(workspace, commit, source_hashes, executables, executable_hashes)
        receipt.update(native_terminal=True, exit_code=process.returncode, source_unchanged=True)
        receipt["status"] = "native_succeeded_pending_cleanup" if success else "native_failed_pending_cleanup"
    except subprocess.TimeoutExpired as error:
        if error.stdout:
            write_once(directory / "stdout-partial.json", error.stdout)
        receipt.update(status="timeout_incomplete_no_retry", native_terminal=False,
            process_tree_requires_independent_verification=True)
    except BaseException as error:
        receipt["failure_class"] = type(error).__name__
        receipt["status"] = "incomplete_no_retry"
    finally:
        if process is not None:
            receipt["process_cleanup"] = stop_owned_process(process)
        if server is not None:
            if thread is not None and thread.is_alive():
                stopper = threading.Thread(target=server.shutdown, daemon=True)
                stopper.start()
                stopper.join(timeout=10)
                receipt["broker_shutdown_returned"] = not stopper.is_alive()
            try:
                server.server_close()
            except BaseException as error:
                receipt["broker_close_failure_class"] = type(error).__name__
        if thread is not None and thread.ident is not None:
            thread.join(timeout=5)
        receipt.update(stopped_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(time.monotonic() - started, 3),
            requests=broker.requests, reviewer_requests=broker.reviewer_requests, token_count_requests=broker.count_requests,
            actions_completed=broker.index, expected_actions=len(broker.actions),
            broker_stopped=thread is None or not thread.is_alive(), tool_results=len(broker.tool_results), refusals=broker.refusals)
        if receipt["status"] in {"native_succeeded_pending_cleanup", "native_failed_pending_cleanup"}:
            terminal = (receipt["broker_stopped"] and receipt.get("broker_shutdown_returned", True)
                and "broker_close_failure_class" not in receipt and receipt.get("process_cleanup", {}).get("direct_child_terminal") is True)
            if terminal:
                try:
                    succeeded = receipt["status"] == "native_succeeded_pending_cleanup"
                    campaign.finish_stage("succeeded" if succeeded else "failed", f"mechanical/{product}_{case}_{stage}")
                    receipt["status"] = "completed" if succeeded else "failed"
                except BaseException as error:
                    receipt.update(status="incomplete_no_retry", completion_failure_class=type(error).__name__)
            else:
                receipt["status"] = "incomplete_no_retry"
        write_once(directory / "receipt.json", json_bytes(receipt))
    return receipt, env
