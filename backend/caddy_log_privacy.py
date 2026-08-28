"""Privacy-safe validation for Caddy configuration and JSON access logs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlsplit


FIXED_ERROR = "The Caddy privacy evidence is invalid."
MAX_LINE_BYTES = 1_048_576
# Caddy's built-in hash filter emits the first four SHA-256 bytes.
OPAQUE_HASH_PATTERN = re.compile(r"^[0-9a-f]{8}$")
EMAIL_PATTERN = re.compile(r"(?i)\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
CREDENTIAL_PATTERN = re.compile(r"(?i)\b(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]+")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:password|passphrase|token|secret|api[_-]?key|private[_-]?key)\s*[:=]"
)
IDENTITY_PATTERN = re.compile(
    r"(?i)\b(?:user|session|client|invitation)_[A-Za-z0-9_]{6,}\b"
)
PAYLOAD_PATTERN = re.compile(
    r"(?i)\b(?:resume|cover_letter|job_description|application_text|profile_text)\s*[:=]"
)
SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "challenge",
        "client_secret",
        "code",
        "code_challenge",
        "code_verifier",
        "id_token",
        "password",
        "refresh_token",
        "session",
        "state",
        "token",
        "verifier",
    }
)
FORBIDDEN_REQUEST_HEADERS = frozenset(
    {
        "authorization",
        "cf-connecting-ip",
        "cf-connecting-ipv6",
        "cookie",
        "forwarded",
        "proxy-authorization",
        "referer",
        "true-client-ip",
        "x-careertrellis-proxy-token",
        "x-forwarded-for",
        "x-real-ip",
    }
)
FORBIDDEN_RESPONSE_HEADERS = frozenset({"location", "set-cookie"})
ALLOWED_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "connection",
        "content-length",
        "content-type",
        "host",
        "origin",
        "priority",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-fetch-user",
        "upgrade-insecure-requests",
        "user-agent",
    }
)
REQUIRED_CONFIG_DIRECTIVES = (
    "user_id delete",
    "request>remote_ip hash",
    "request>client_ip hash",
    "request>headers>User-Agent hash",
    "request>headers>Referer delete",
    "request>headers>Cf-Connecting-Ip delete",
    "request>headers>Cf-Connecting-Ipv6 delete",
    "request>headers>True-Client-Ip delete",
    "request>headers>X-Forwarded-For delete",
    "request>headers>X-Real-Ip delete",
    "request>headers>Forwarded delete",
    "request>headers>Authorization delete",
    "request>headers>Proxy-Authorization delete",
    "request>headers>X-Careertrellis-Proxy-Token delete",
    "request>headers>Cookie delete",
    "resp_headers>Set-Cookie delete",
    "resp_headers>Location delete",
    "wrap json",
    "log_skip /auth*",
)
SITE_BLOCK_OPENER = "staging-app.jhunterj.com {"


class CaddyPrivacyError(RuntimeError):
    """One fixed failure without source content."""

    def __init__(self, code: str) -> None:
        super().__init__(FIXED_ERROR)
        self.code = code


@dataclass(frozen=True)
class CaddyPrivacySummary:
    records: int
    query_records: int
    representative_records: int


def _active_config_lines(source: str) -> tuple[str, ...]:
    lines: list[str] = []
    for raw_line in source.splitlines():
        active = raw_line.split("#", 1)[0].strip()
        if active:
            lines.append(" ".join(active.split()))
    return tuple(lines)


def _block_bounds(lines: tuple[str, ...], opener: str) -> tuple[int, int]:
    starts = tuple(index for index, line in enumerate(lines) if line == opener)
    if len(starts) != 1:
        raise CaddyPrivacyError("config_contract_invalid")
    start = starts[0]
    depth = 0
    for index in range(start, len(lines)):
        depth += lines[index].count("{")
        depth -= lines[index].count("}")
        if depth == 0:
            return start, index
        if depth < 0:
            break
    raise CaddyPrivacyError("config_contract_invalid")


def validate_caddy_config(source: str) -> None:
    """Require every reviewed control inside the one active access-log block."""
    lines = _active_config_lines(source)
    site_start, site_end = _block_bounds(lines, SITE_BLOCK_OPENER)
    log_start, log_end = _block_bounds(lines, "log {")
    filter_start, filter_end = _block_bounds(lines, "format filter {")
    if not (
        site_start < log_start < filter_start < filter_end < log_end < site_end
    ):
        raise CaddyPrivacyError("config_contract_invalid")
    filter_lines = lines[filter_start + 1 : filter_end]
    for directive in REQUIRED_CONFIG_DIRECTIVES[:-1]:
        if lines.count(directive) != 1 or filter_lines.count(directive) != 1:
            raise CaddyPrivacyError("config_contract_invalid")
    site_lines = lines[site_start + 1 : site_end]
    if (
        lines.count(REQUIRED_CONFIG_DIRECTIVES[-1]) != 1
        or site_lines.count(REQUIRED_CONFIG_DIRECTIVES[-1]) != 1
    ):
        raise CaddyPrivacyError("config_contract_invalid")
    log_lines = lines[log_start + 1 : log_end]
    if sum(line.startswith("output file ") for line in log_lines) != 1:
        raise CaddyPrivacyError("config_contract_invalid")
    if any(line == "output discard" for line in lines):
        raise CaddyPrivacyError("config_contract_invalid")


def _object(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CaddyPrivacyError(code)
    return value


def _header_map(value: object, code: str) -> dict[str, object]:
    headers = _object(value, code)
    lowered: dict[str, object] = {}
    for key, header_value in headers.items():
        if not isinstance(key, str):
            raise CaddyPrivacyError(code)
        normalized = key.lower()
        if normalized in lowered:
            raise CaddyPrivacyError(code)
        lowered[normalized] = header_value
    return lowered


def _hashed_values(value: object, code: str) -> int:
    values = value if isinstance(value, list) else [value]
    if not values:
        raise CaddyPrivacyError(code)
    for item in values:
        if not isinstance(item, str) or not OPAQUE_HASH_PATTERN.fullmatch(item):
            raise CaddyPrivacyError(code)
    return len(values)


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _reject_sensitive_strings(record: dict[str, object]) -> None:
    patterns = (
        EMAIL_PATTERN,
        CREDENTIAL_PATTERN,
        SECRET_ASSIGNMENT_PATTERN,
        IDENTITY_PATTERN,
        PAYLOAD_PATTERN,
    )
    for value in _strings(record):
        if any(pattern.search(value) for pattern in patterns):
            raise CaddyPrivacyError("sensitive_value_present")


def scan_json_lines(
    lines: Iterable[str], *, require_representative: bool = True
) -> CaddyPrivacySummary:
    """Validate access-log records without returning any source value."""
    records = 0
    query_records = 0
    representative_records = 0
    for line in lines:
        if not isinstance(line, str):
            raise CaddyPrivacyError("log_encoding_invalid")
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise CaddyPrivacyError("log_record_oversized")
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise CaddyPrivacyError("log_json_invalid") from exc
        record = _object(record, "log_shape_invalid")
        request = _object(record.get("request"), "request_shape_invalid")
        uri = request.get("uri")
        if not isinstance(uri, str):
            raise CaddyPrivacyError("request_shape_invalid")
        parsed = urlsplit(uri)
        normalized_path = parsed.path.rstrip("/") or "/"
        if normalized_path == "/auth" or normalized_path.startswith("/auth/"):
            raise CaddyPrivacyError("auth_route_logged")
        if parsed.query:
            query_records += 1
            try:
                query_keys = {
                    key.lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
                }
            except ValueError as exc:
                raise CaddyPrivacyError("query_invalid") from exc
            if query_keys & SENSITIVE_QUERY_KEYS:
                raise CaddyPrivacyError("sensitive_query_present")
        if "user_id" in record:
            raise CaddyPrivacyError("forbidden_field_present")
        request_headers = _header_map(
            request.get("headers", {}), "request_headers_invalid"
        )
        if FORBIDDEN_REQUEST_HEADERS & request_headers.keys():
            raise CaddyPrivacyError("forbidden_field_present")
        if request_headers.keys() - ALLOWED_REQUEST_HEADERS:
            raise CaddyPrivacyError("unexpected_request_header")
        response_headers = _header_map(
            record.get("resp_headers", {}), "response_headers_invalid"
        )
        if FORBIDDEN_RESPONSE_HEADERS & response_headers.keys():
            raise CaddyPrivacyError("forbidden_field_present")
        remote_count = 0
        for field in ("remote_ip", "client_ip"):
            if field in request:
                remote_count += _hashed_values(request[field], "client_hash_invalid")
        user_agent_count = 0
        if "user-agent" in request_headers:
            user_agent_count = _hashed_values(
                request_headers["user-agent"], "user_agent_hash_invalid"
            )
        if remote_count and user_agent_count:
            representative_records += 1
        _reject_sensitive_strings(record)
        records += 1
    if records == 0:
        raise CaddyPrivacyError("log_empty")
    if require_representative and representative_records == 0:
        raise CaddyPrivacyError("representative_record_absent")
    return CaddyPrivacySummary(records, query_records, representative_records)


def _safe_report(result: str, code: str, summary: CaddyPrivacySummary | None) -> str:
    records = summary.records if summary is not None else 0
    query_records = summary.query_records if summary is not None else 0
    representative_records = summary.representative_records if summary is not None else 0
    return "\n".join(
        (
            "operation=caddy-log-privacy-check",
            f"result={result}",
            f"error_code={code}",
            f"records={records}",
            f"query_records={query_records}",
            f"representative_records={representative_records}",
            "source_disclosed=no",
            "personal_data_disclosed=no",
            "secret_disclosed=no",
        )
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        allow_no_representative = False
        if len(argv) == 5 and argv[-1] == "--allow-no-representative":
            allow_no_representative = True
            argv = argv[:-1]
        if len(argv) != 4 or argv[0] != "--config" or argv[2] != "--log":
            raise CaddyPrivacyError("arguments_invalid")
        config_path = Path(argv[1])
        log_path = Path(argv[3])
        if config_path.is_symlink() or log_path.is_symlink():
            raise CaddyPrivacyError("source_invalid")
        validate_caddy_config(config_path.read_text(encoding="utf-8"))
        with log_path.open("r", encoding="utf-8", errors="strict") as handle:
            summary = scan_json_lines(
                handle,
                require_representative=not allow_no_representative,
            )
    except (CaddyPrivacyError, OSError, UnicodeError) as exc:
        code = exc.code if isinstance(exc, CaddyPrivacyError) else "source_invalid"
        print(_safe_report("blocked", code, None))
        return 1
    print(_safe_report("ok", "none", summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
