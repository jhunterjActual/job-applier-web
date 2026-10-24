"""Scripted, offline-only CT native HTTP seam; no provider forwarding."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading

ENDPOINT = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5-5"
LIMITS = {"fit": 16384, "tailor": 32768, "headquarters": 4096}
MAX_BODY = 16 * 1024 * 1024


class MechanicalRefused(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MechanicalRefused("Duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise MechanicalRefused("Nonfinite JSON value")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class _Response:
    def __init__(self, owner, call_id, raw):
        self.owner, self.call_id, self.raw = owner, call_id, raw
        self.stream = io.BytesIO(raw)
        self.complete = False

    def __enter__(self):
        return self

    def read(self, size=-1):
        value = self.stream.read(size)
        if self.stream.tell() == len(self.raw):
            self.complete = True
        return value

    def __exit__(self, kind, value, traceback):
        if kind is None and self.complete:
            self.owner.campaign.settle(self.call_id, "0", "scripted/response-consumed")
        else:
            self.owner.campaign.mark_unknown(self.call_id, "scripted/response-incomplete")
        self.stream.close()
        return False


class ScriptedCTOpener:
    """One immutable normal CT phase; Campaign owner starts/finishes its stage.

    The supplied key is synthetic and checked only in memory. Each action is an
    exact operation/schema/response declaration, never an outbound request.
    """
    def __init__(self, campaign, case, stage, output, synthetic_key, actions):
        if campaign.snapshot()["mode"] != "offline":
            raise MechanicalRefused("Offline mode required")
        if not re.fullmatch(r"V(?:0[1-9]|1[0-2])", case) or stage not in ("fit", "materials"):
            raise MechanicalRefused("Invalid stage")
        if stage == "materials" and int(case[1:]) > 6:
            raise MechanicalRefused("Fit-only case")
        expected = ["fit"] if stage == "fit" else ["tailor", "headquarters"]
        if not isinstance(synthetic_key, str) or not synthetic_key.startswith("synthetic-"):
            raise MechanicalRefused("Explicit synthetic key required")
        actions = copy.deepcopy(actions)
        if not isinstance(actions, list) or not all(isinstance(a, dict) for a in actions) or [a.get("operation") for a in actions] != expected:
            raise MechanicalRefused("Exact phase operation list required")
        for action in actions:
            if set(action) != {"operation", "schema", "response"} or not isinstance(action["schema"], dict) or not isinstance(action["response"], dict):
                raise MechanicalRefused("Invalid scripted action")
            if len(canonical(action)) > MAX_BODY:
                raise MechanicalRefused("Oversized scripted action")
        self.refused = False
        self.campaign, self.case, self.stage = campaign, case, stage
        self.key, self.actions = synthetic_key, tuple(canonical(action) for action in actions)
        self._check_stage()
        self.output = Path(output)
        if ".." in self.output.parts or self.output.is_symlink() or self.output.exists():
            raise MechanicalRefused("New owned evidence path required")
        for parent in self.output.parents:
            if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
                raise MechanicalRefused("Linked evidence path")
        self.output.mkdir(parents=False)
        self.requests = []
        self.lock = threading.Lock()
        (self.output / "actions.json").write_bytes(canonical(actions))

    def _check_stage(self):
        state = self.campaign.snapshot()
        expected = {"product": "CT", "case": self.case, "stage": self.stage}
        order = self.campaign.policy["order"]
        index = state["completed_stages"]
        if state["halted"] or state["pending_calls"] or not state["active"] or index >= len(order) or order[index] != expected:
            raise MechanicalRefused("Exact active CT stage required")

    def open(self, request, *, timeout):
        with self.lock:
            if self.refused:
                raise MechanicalRefused("Transport refusal latched")
            try:
                return self._open(request, timeout)
            except Exception:
                self.refused = True
                # Fixed classification only, never headers, key or exception text.
                raw = request.data if isinstance(request.data, bytes) else b""
                with (self.output / "refusal.json").open("xb") as stream:
                    stream.write(canonical({"classification": "native_request_refused", "request_sha256": digest(raw)}))
                    stream.flush()
                    os.fsync(stream.fileno())
                raise

    def _open(self, request, timeout):
        self._check_stage()
        index = len(self.requests)
        if index >= len(self.actions):
            raise MechanicalRefused("Unexpected additional request")
        if request.full_url != ENDPOINT or request.get_method() != "POST" or type(timeout) is not int or not 1 <= timeout <= 90:
            raise MechanicalRefused("Native route/method/timeout refused")
        headers = {}
        for source in (request.headers, request.unredirected_hdrs):
            if type(source) is not dict:
                raise MechanicalRefused("Native header maps refused")
            for key, value in source.items():
                if not isinstance(key, str) or not isinstance(value, str) or key.lower() in headers:
                    raise MechanicalRefused("Duplicate native headers refused")
                headers[key.lower()] = value
        if set(headers) != {"x-api-key", "anthropic-version", "content-type"} or headers["x-api-key"] != self.key or headers["anthropic-version"] != "2023-06-01" or headers["content-type"] != "application/json":
            raise MechanicalRefused("Native synthetic headers refused")
        if not isinstance(request.data, bytes) or not 0 < len(request.data) <= MAX_BODY:
            raise MechanicalRefused("Native body refused")
        raw = bytes(request.data)
        try:
            payload = strict_json(raw)
        except (ValueError, UnicodeError):
            raise MechanicalRefused("Native JSON refused") from None
        action = json.loads(self.actions[index])
        if not isinstance(payload, dict) or set(payload) != {"model", "max_tokens", "thinking", "messages", "output_config"}:
            raise MechanicalRefused("Native envelope refused")
        if payload["model"] != MODEL or type(payload["max_tokens"]) is not int or payload["max_tokens"] != LIMITS[action["operation"]] or payload["thinking"] != {"type": "between_tools"}:
            raise MechanicalRefused("Native model/limits refused")
        if canonical(payload["output_config"]) != canonical({"format": {"type": "json_schema", "schema": action["schema"]}}):
            raise MechanicalRefused("Native output schema refused")
        messages = payload["messages"]
        if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict) or set(messages[0]) != {"role", "content"} or messages[0]["role"] != "user" or not isinstance(messages[0]["content"], str) or not messages[0]["content"].strip():
            raise MechanicalRefused("Native text input refused")
        request_name = f"request-{index+1:03d}.json"
        response_name = f"response-{index+1:03d}.json"
        response = canonical({"content": [{"type": "text", "text": json.dumps(action["response"])}], "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 10}})
        # Both immutable bodies precede durable admission; there is no send path.
        with (self.output / request_name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        with (self.output / response_name).open("xb") as stream:
            stream.write(response)
            stream.flush()
            os.fsync(stream.fileno())
        call_id = f"CT_{self.case}_{self.stage}_c{index+1:03d}"
        self.campaign.reserve(call_id, "0.000001", "scripted/request-frozen")
        self.requests.append({"call_id": call_id, "operation": action["operation"], "max_output_tokens": payload["max_tokens"], "request_sha256": digest(raw), "response_sha256": digest(response), "provider_forwarding": False})
        return _Response(self, call_id, response)

    def complete(self):
        state = self.campaign.snapshot()
        return not self.refused and len(self.requests) == len(self.actions) and not state["pending_calls"] and not state["halted"]
