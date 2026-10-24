"""Pure, opt-in offline native group and corroborated-result contracts.

Import does not start a process, read credentials or contact any service.
"""
import hashlib
import json
import re
from pathlib import Path


class CorrectionRefused(ValueError):
    pass


PINNED_CLI = "5441781f2b6bbd07354b4a74560ee88c68c9bf05d6db3c7e24c452c91673dc53"
PINNED_CHECKER = "8394f4d130285dffd0bd93a811aca483c9afb3299e607fbc58748e02b49e24c0"
PINNED_UPSTREAM = "64a6c90ec32438742ca62f8f30f55afea4435172"

POLICY = {"schema": "offline-native-correction-v1", "verdict": "corroborated_layout_verdict_v1",
          "serializer": "pinned_cli_stdout_v1"}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_policy(value):
    if type(value) is not dict or canonical(value) != canonical(POLICY):
        raise CorrectionRefused("Unsupported offline correction policy")
    return json.loads(canonical(value))


def validate_runtime_pins(commit, sources, executables):
    if (commit != PINNED_UPSTREAM or type(sources) is not dict or type(executables) is not dict
            or sources.get("tools/verify_layout.py") != PINNED_CHECKER
            or executables.get("cli") != PINNED_CLI):
        raise CorrectionRefused("Pinned correction source/CLI contract required")


def serializer_variant(native, stdout):
    if type(native) is not str or type(stdout) is not bytes:
        raise CorrectionRefused("Exact native and witness bytes required")
    actual = native.encode("utf-8").replace(b"\r\n", b"\n")
    witness = stdout.replace(b"\r\n", b"\n")
    expected = b"Exit code 1\n" + witness
    if actual == expected:
        return "exact"
    if (witness.endswith(b"\n") and not witness.endswith(b"\n\n")
            and not actual.endswith(b"\n") and actual == expected[:-1]):
        return "omit_one_final_lf"
    raise CorrectionRefused("Pinned native serializer mismatch")


def resources(policy, name, args):
    """Conservative normalized resources; None denotes the entire workspace."""
    from study_upstream import local_path
    try:
        policy.validate(name, args)
    except ValueError as error:
        raise CorrectionRefused("Native tool policy refused") from error
    def key(value):
        return str(local_path(policy.root, value)).casefold()
    if name in {"Read", "Write", "Edit"}:
        path = key(args["file_path"])
        return ({path} if name != "Write" else set(), {path} if name != "Read" else set())
    if name in {"Glob", "Grep"}:
        return None, set()
    if name == "Agent":
        return None, None
    command = args["command"]
    if command.startswith("cd "):
        return None, None
    if command.startswith("python tools/verify_layout.py "):
        return {key(command.split(" ")[2])}, set()
    match = re.fullmatch(r"python tools/verify_pdf\.py (\S+) --pages [12] --min-chars [1-9][0-9]{0,3} --dump-text (\S+)", command)
    if match:
        return {key(match[1])}, {key(match[2])}
    raise CorrectionRefused("Unknown native resource effects")


def intersects(left, right):
    # A wildcard means every owned path, never a literal '*' filename.
    return bool(left) if right is None else bool(right) if left is None else bool(left & right)


def parse_groups(value, policy):
    if type(value) is not dict or set(value) != {"schema", "groups"} or value["schema"] != "offline-native-action-groups-v1":
        raise CorrectionRefused("Invalid native action-group document")
    groups, ids = [], set()
    if type(value["groups"]) is not list or not 1 <= len(value["groups"]) <= 999:
        raise CorrectionRefused("Invalid native group count")
    for group in value["groups"]:
        if (type(group) is not dict or set(group) != {"id", "tools"}
                or type(group["id"]) is not str or not re.fullmatch(r"g[0-9]{3}", group["id"])
                or group["id"] == "g000" or group["id"] in ids
                or type(group["tools"]) is not list or not 1 <= len(group["tools"]) <= 2):
            raise CorrectionRefused("Invalid native group")
        ids.add(group["id"])
        slots, effects, frozen = set(), [], []
        for tool in group["tools"]:
            if (type(tool) is not dict or set(tool) != {"slot", "name", "input"}
                    or type(tool["slot"]) is not str or tool["slot"] not in {"a", "b"} or tool["slot"] in slots
                    or type(tool["name"]) is not str):
                raise CorrectionRefused("Invalid native slot")
            slots.add(tool["slot"])
            effect = resources(policy, tool["name"], tool["input"])
            if len(group["tools"]) > 1 and effect == (None, None):
                raise CorrectionRefused("Compiler and reviewer must remain singleton")
            effects.append(effect)
            frozen.append(json.loads(canonical(tool)))
        if len(effects) == 2:
            (r1, w1), (r2, w2) = effects
            if intersects(w1, r2) or intersects(w1, w2) or intersects(w2, r1):
                raise CorrectionRefused("Conflicting native group resources")
        groups.append({"id": group["id"], "tools": frozen})
    return tuple(groups)


class ResultBindings:
    """Bind first observations to exact bodies and reject conflicting histories."""
    def __init__(self):
        self.emitted, self.observed, self.bindings = {}, {}, []

    def emit(self, tool_id, name, args):
        if tool_id in self.emitted:
            raise CorrectionRefused("Duplicate emitted tool ID")
        self.emitted[tool_id] = (name, json.loads(canonical(args)))

    def observe(self, node, raw, pointer):
        tool_id = node.get("tool_use_id")
        if type(tool_id) is not str or tool_id not in self.emitted or type(node.get("is_error", False)) is not bool:
            raise CorrectionRefused("Unknown or invalid native result")
        hashed = sha(canonical(node))
        if tool_id in self.observed:
            if self.observed[tool_id] != hashed:
                raise CorrectionRefused("Conflicting native result history")
            return False
        self.observed[tool_id] = hashed
        self.bindings.append({"tool_id": tool_id, "result_sha256": hashed,
                              "body_sha256": sha(raw), "json_pointer": pointer})
        return True

    def complete(self, expected):
        return set(expected).issubset(self.observed)


def bounded_witness(command, cwd, env, directory, verify_bindings, observe_tree,
                    timeout=20, stdout_limit=1048576, stderr_limit=65536):
    """Owned read-only observer; uncertain cleanup is a separate durable blocker.

    The reviewed fixture supplies exact immutable binding and process-tree
    observers. A direct-child return code alone never establishes tree cleanup.
    No provider reservation or stage completion occurs in this helper.
    """
    import os
    import subprocess
    import threading
    import time
    from datetime import datetime, timezone
    from study_upstream import stop_owned_process, write_once
    if (type(timeout) is not int or not 1 <= timeout <= 20
            or type(stdout_limit) is not int or not 1 <= stdout_limit <= 1048576
            or type(stderr_limit) is not int or not 1 <= stderr_limit <= 65536
            or not callable(verify_bindings) or not callable(observe_tree)):
        raise CorrectionRefused("Invalid bounded witness configuration")
    directory = Path(directory)
    directory.mkdir(exist_ok=False)
    before = verify_bindings()
    if type(before) is not dict or not before:
        raise CorrectionRefused("Witness immutable inventory required")
    write_once(directory / "before.json", canonical(before) + b"\n")
    started_utc = datetime.now(timezone.utc).isoformat()
    record = {"classification": "offline_readonly_layout_witness", "started_utc": started_utc,
              "terminal": False, "cleanup_unknown": True, "observer_seconds": None}
    process, threads = None, []
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    capture_lock = threading.Lock()
    capture_errors = []
    started = time.monotonic()
    def capture(stream, name, limit):
        try:
            while True:
                block = stream.read(min(4096, limit + 1 - len(outputs[name])))
                if not block:
                    break
                with capture_lock:
                    outputs[name].extend(block)
                    over = len(outputs[name]) > limit
                if over:
                    overflow.set()
                    break
        except BaseException as error:
            capture_errors.append(type(error).__name__)
            overflow.set()
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        record["owned_pid"] = process.pid
        write_once(directory / "owner.json", canonical({"owned_pid": process.pid,
                   "started_utc": started_utc, "command_sha256": sha(canonical(command))}) + b"\n")
        for name, limit in (("stdout", stdout_limit), ("stderr", stderr_limit)):
            thread = threading.Thread(target=capture, args=(getattr(process, name), name, limit), daemon=True)
            threads.append(thread); thread.start()
        deadline = started + timeout
        while process.poll() is None and not overflow.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        if process.poll() is None or overflow.is_set():
            record["stop_reason"] = "output_bound" if overflow.is_set() else "deadline"
            record["process_cleanup"] = stop_owned_process(process)
        for thread in threads:
            thread.join(timeout=2)
        record["capture_complete"] = not any(thread.is_alive() for thread in threads) and not capture_errors
        record["output_overflow"] = overflow.is_set()
        record["exit_code"] = process.poll()
        tree = observe_tree(process.pid, started_utc)
        record["tree_observation"] = tree
        record["terminal"] = (type(tree) is dict and tree.get("owned_pid") == process.pid
                              and tree.get("terminal") is True and process.poll() is not None
                              and record["capture_complete"])
        record["cleanup_unknown"] = not record["terminal"]
        after = verify_bindings()
        record["bindings_unchanged"] = canonical(before) == canonical(after)
        record["capture_valid"] = (record["terminal"] and not overflow.is_set()
            and not capture_errors and process.returncode == 1 and not outputs["stderr"]
            and record["bindings_unchanged"] and "stop_reason" not in record)
        # Capture alone is never verdict/admission. The separate classifier
        # must validate the complete pinned report and native serialization.
        record["supported_verdict"] = False
    except BaseException as error:
        record["failure_class"] = type(error).__name__
        if process is not None:
            record["process_cleanup"] = stop_owned_process(process)
        # Observation/capture failure remains unknown even if the direct child
        # appears terminal. The owner must not finish or advance its stage.
        record["supported_verdict"] = False
    finally:
        with capture_lock:
            frozen_outputs = {name: bytes(value) for name, value in outputs.items()}
        for name in frozen_outputs:
            write_once(directory / (name + ".bin"), frozen_outputs[name])
            record[name + "_sha256"] = sha(frozen_outputs[name])
        record["observer_seconds"] = round(time.monotonic() - started, 6)
        write_once(directory / "receipt.json", canonical(record) + b"\n")
    return record, frozen_outputs["stdout"]


def classify_layout(native, stdout, record, relative_pdf):
    """Only the observed pinned bottom-space layout verdict is supported in v1."""
    if (type(record) is not dict or record.get("capture_valid") is not True
            or record.get("cleanup_unknown") is not False or record.get("terminal") is not True
            or type(record.get("exit_code")) is not int or record.get("exit_code") != 1 or record.get("stderr_sha256") != sha(b"")
            or record.get("stdout_sha256") != sha(stdout) or not record.get("bindings_unchanged")):
        raise CorrectionRefused("Unproven terminal layout witness")
    if not isinstance(relative_pdf, str) or not re.fullmatch(r"(cv|cover_letters)/[A-Za-z0-9_-]+\.pdf", relative_pdf):
        raise CorrectionRefused("Unapproved layout target")
    text = stdout.decode("utf-8", errors="strict").replace("\r\n", "\n")
    lines = text.splitlines()
    targets = {relative_pdf, relative_pdf.replace("/", "\\")}
    headers = {f"{target}: 2 page(s) (page count is verify_pdf.py's job, not checked here)" for target in targets}
    if len(lines) != 7 or lines[0] not in headers or lines[3:5] != ["", "LAYOUT PROBLEMS:"]:
        raise CorrectionRefused("Unrecognized pinned layout report")
    for page, line in enumerate(lines[1:3], 1):
        if not re.fullmatch(rf"  p{page}: text y [0-9]+\.\.[0-9]+ of [0-9]+pt \| bottom [0-9]+pt \([0-9]+%\) \| largest gap [0-9]+pt at y[0-9]+", line):
            raise CorrectionRefused("Unrecognized pinned page metrics")
    problems = (
        r"  - p1 ends [0-9]+pt \([0-9]+%\) early although more pages follow, which reads as a broken page break",
        r"  - p2 is the last page and [0-9]+% empty, which reads as an unfinished document; restore the highest-relevance content previously cut",
    )
    if any(not re.fullmatch(pattern, line) for pattern, line in zip(problems, lines[5:])):
        raise CorrectionRefused("Unsupported pinned layout verdict")
    return {"verdict": "corroborated_layout_verdict_v1", "serializer_variant": serializer_variant(native, stdout),
            "native_content_sha256": sha(native.encode("utf-8")), "witness_stdout_sha256": sha(stdout)}


class LayoutCorrectionSession:
    """Frozen offline witness runtime; no arbitrary command or error exception."""
    def __init__(self, policy, executables, executable_hashes, directory, env, observe_tree):
        from study_upstream import local_path
        if not callable(observe_tree):
            raise CorrectionRefused("Explicit owned-tree observer required")
        validate_runtime_pins(PINNED_UPSTREAM, policy.source_hashes, executable_hashes)
        self.policy, self.executables = policy, dict(executables)
        self.executable_hashes = dict(executable_hashes)
        self.directory = Path(directory)
        self.directory.mkdir(exist_ok=False)
        # Keep only the previously constructed isolated native environment;
        # remove even its synthetic broker credential/endpoint from observer.
        self.env = {key: value for key, value in env.items() if key in {
            "SystemRoot", "WINDIR", "ComSpec", "PATHEXT", "HOME", "USERPROFILE", "TEMP", "TMP"}}
        import os
        self.env["PATH"] = os.pathsep.join(dict.fromkeys(str(Path(executables[name]).parent) for name in ("python", "pdftotext")))
        self.observe_tree, self.cleanup_unknown = observe_tree, False
        self.emissions, self.verdicts, self.archives = {}, [], []
        self.framework_hashes = {str(path): sha(path.read_bytes()) for path in
            (Path(__file__), Path(__file__).with_name("study_upstream.py"))}
        self.local_path = local_path

    def inventory(self, pdf=None):
        result = {"observer_environment_sha256": sha(canonical(self.env))}
        for name, expected in self.framework_hashes.items():
            if sha(Path(name).read_bytes()) != expected:
                raise CorrectionRefused("Correction adapter source changed")
            result["framework/" + Path(name).name] = expected
        for name, expected in self.policy.source_hashes.items():
            path = self.local_path(self.policy.root, name)
            actual = sha(path.read_bytes())
            if actual != expected:
                raise CorrectionRefused("Frozen source changed before witness")
            result["source/" + name] = actual
        for name, path in self.executables.items():
            actual = sha(Path(path).read_bytes())
            if actual != self.executable_hashes[name]:
                raise CorrectionRefused("Frozen runtime changed before witness")
            result["runtime/" + name] = actual
        for folder in ("cv", "cover_letters"):
            base = self.policy.root / folder
            for original in sorted(base.rglob("*.tex")) if base.exists() else ():
                path = self.local_path(self.policy.root, str(original))
                result["generated_source/" + path.relative_to(self.policy.root).as_posix()] = sha(path.read_bytes())
        if pdf:
            result["observed_pdf"] = sha(self.local_path(self.policy.root, pdf).read_bytes())
        return result

    def before_group(self, tools, number):
        from study_upstream import write_once
        # Archive every existing generated TeX/PDF before the next modifying
        # group. Never overwrite first drafts or failed output versions.
        if any(name in {"Write", "Edit"} or (name == "Bash" and args["command"].startswith("cd ")) for _, name, args in tools):
            archive = self.directory / f"versions-{number:03d}"
            archive.mkdir(exist_ok=False)
            files = {}
            for folder in ("cv", "cover_letters"):
                base = self.policy.root / folder
                for original in sorted(base.rglob("*")) if base.exists() else ():
                    if original.suffix.lower() not in {".tex", ".pdf"}:
                        continue
                    path = self.local_path(self.policy.root, str(original))
                    raw = path.read_bytes()
                    relative = path.relative_to(self.policy.root).as_posix()
                    target = archive / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    write_once(target, raw)
                    files[relative] = sha(raw)
            write_once(archive / "manifest.json", canonical(files) + b"\n")
            self.archives.append({"before_request": number, "manifest_sha256": sha(canonical(files))})
        for tool_id, name, args in tools:
            if name == "Bash" and args["command"].startswith("python tools/verify_layout.py "):
                pdf = args["command"].split(" ")[2]
                self.emissions[tool_id] = {"pdf": pdf, "bindings": self.inventory(pdf)}

    def corroborate(self, tool_id, node):
        if tool_id not in self.emissions or self.cleanup_unknown:
            raise CorrectionRefused("Unsupported native failure or unknown observer")
        emitted = self.emissions[tool_id]
        if canonical(self.inventory(emitted["pdf"])) != canonical(emitted["bindings"]):
            raise CorrectionRefused("Layout input changed since native emission")
        report_dir = self.directory / ("witness-" + tool_id)
        self.cleanup_unknown = True
        record, stdout = bounded_witness(
            [str(self.executables["python"]), "tools/verify_layout.py", emitted["pdf"]],
            self.policy.root, self.env, report_dir, lambda: self.inventory(emitted["pdf"]), self.observe_tree)
        self.cleanup_unknown = record["cleanup_unknown"]
        verdict = classify_layout(node.get("content"), stdout, record, emitted["pdf"])
        verdict.update(tool_id=tool_id, observer_seconds=record["observer_seconds"])
        self.verdicts.append(verdict)
        return verdict


def load_prior_fit(directory, expected_context, policy):
    """Import one completed text Read's exact original evidence, never an ID exception."""
    from study_upstream import local_path, sse, unique_object
    directory = Path(directory)
    if ".." in directory.parts:
        raise CorrectionRefused("Traversal in prior phase refused")
    for path in (directory, *directory.parents):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise CorrectionRefused("Linked prior phase refused")
    directory = directory.resolve(strict=True)
    def read_evidence(name):
        path = local_path(directory, name)
        limit = 16 * 1024 * 1024 if name.startswith("request-") or name == "stdout.json" else 3 * 1024 * 1024
        if not path.is_file() or path.stat().st_size > limit:
            raise CorrectionRefused("Prior evidence file missing or oversized")
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise CorrectionRefused("Prior evidence file grew beyond bound")
        return raw
    def read_json(name):
        raw = read_evidence(name)
        value = json.loads(raw, object_pairs_hook=unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(CorrectionRefused("Nonfinite prior evidence")))
        return raw, value
    receipt_raw, receipt = read_json("receipt.json")
    binding_raw, binding = read_json("phase-binding.json")
    expected = dict(expected_context, stage="fit")
    if (canonical(binding) != canonical(expected) or receipt.get("phase_binding_sha256") != sha(binding_raw)
            or receipt.get("status") != "completed" or receipt.get("stage") != "fit"
            or receipt.get("product") != expected["product"] or receipt.get("case") != expected["case"]
            or receipt.get("native_terminal") is not True or receipt.get("source_unchanged") is not True
            or receipt.get("broker_stopped") is not True or receipt.get("process_cleanup", {}).get("direct_child_terminal") is not True
            or type(receipt.get("actions_completed")) is not int or receipt.get("actions_completed") != 1
            or type(receipt.get("expected_actions")) is not int or receipt.get("expected_actions") != 1
            or type(receipt.get("reviewer_requests")) is not int or receipt.get("reviewer_requests") != 0
            or type(receipt.get("tool_results")) is not int or receipt.get("tool_results") != 1):
        raise CorrectionRefused("Unbound or incomplete prior fit")
    stdout_raw, stdout = read_json("stdout.json")
    if (receipt.get("stdout_sha256") != sha(stdout_raw) or stdout.get("type") != "result"
            or stdout.get("subtype") != "success" or stdout.get("is_error") is not False
            or stdout.get("session_id") != expected["session_id"] or stdout.get("permission_denials")):
        raise CorrectionRefused("Prior native session/result mismatch")
    action_raw, actions = read_json("actions.json")
    if (receipt.get("actions_sha256") != sha(action_raw) or type(actions) is not list or len(actions) != 1
            or type(actions[0]) is not list or len(actions[0]) != 2 or actions[0][0] != "Read"):
        raise CorrectionRefused("Only one prior text Read is supported")
    policy.validate(*actions[0])
    requests = receipt.get("requests")
    if type(requests) is not list or len(requests) != 2:
        raise CorrectionRefused("Two complete prior fit exchanges required")
    records, observed = [], {}
    for index, item in enumerate(requests, 1):
        raw, request = read_json(f"request-{index:03d}.json")
        response = read_evidence(f"response-{index:03d}.sse")
        if (type(item) is not dict or item.get("call_id") != f"{expected['product']}_{expected['case']}_fit_c{index:03d}"
                or item.get("request_sha256") != sha(raw) or item.get("response_sha256") != sha(response)
                or item.get("reviewer") is not False or response != sse(actions[0] if index == 1 else None, index)):
            raise CorrectionRefused("Prior exchange/actions changed")
        messages = request.get("messages")
        if type(messages) is not list:
            raise CorrectionRefused("Invalid prior messages")
        for mi, message in enumerate(messages):
            content = message.get("content", []) if type(message) is dict else []
            if type(content) is not list:
                continue
            for bi, block in enumerate(content):
                if type(block) is not dict or block.get("type") != "tool_result":
                    continue
                if (index != 2 or set(block) - {"type", "tool_use_id", "content", "is_error"}
                        or block.get("tool_use_id") != "toolu_fixture_1" or type(block.get("content")) is not str
                        or type(block.get("is_error", False)) is not bool or block.get("is_error", False)):
                    raise CorrectionRefused("Unsupported prior result history")
                hashed = sha(canonical(block))
                if observed and observed.get("toolu_fixture_1") != hashed:
                    raise CorrectionRefused("Conflicting prior fit result")
                if not observed:
                    records.append({"tool_id": "toolu_fixture_1", "result_sha256": hashed,
                        "body_sha256": sha(raw), "json_pointer": f"/messages/{mi}/content/{bi}"})
                observed["toolu_fixture_1"] = hashed
    if set(observed) != {"toolu_fixture_1"}:
        raise CorrectionRefused("Prior Read result was not observed")
    return {"results": observed, "bindings": records, "receipt_sha256": sha(receipt_raw),
            "phase_binding_sha256": sha(binding_raw), "stdout_sha256": sha(stdout_raw),
            "actions_sha256": sha(action_raw), "exchanges": requests}
