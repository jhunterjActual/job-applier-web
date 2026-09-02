"""Privacy-safe validation for Caddy configuration and JSON access logs."""

from __future__ import annotations

from collections.abc import Iterable
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
import gzip
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, unquote, urlsplit


FIXED_ERROR = "The Caddy privacy evidence is invalid."
MAX_LINE_BYTES = 1_048_576
MAX_URI_DECODE_LAYERS = 3
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
SENSITIVE_QUERY_PATTERN = re.compile(
    r"(?i)(?:^|[?&#])(?:"
    + "|".join(re.escape(key) for key in sorted(SENSITIVE_QUERY_KEYS))
    + r")="
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
ALLOWED_BASIC_CHALLENGE = 'Basic realm="restricted"'
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
REQUIRED_SITE_CONFIG_DIRECTIVES = (
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
REQUIRED_RUNTIME_CONFIG_DIRECTIVES = (
    "user_id delete",
    "request>remote_ip hash",
    "request>client_ip hash",
    "request>uri delete",
    "request>headers delete",
    "resp_headers delete",
    "wrap json",
)
REQUIRED_CONFIG_DIRECTIVES = (
    *REQUIRED_SITE_CONFIG_DIRECTIVES,
    *REQUIRED_RUNTIME_CONFIG_DIRECTIVES,
)
SITE_BLOCK_OPENER = "staging-app.jhunterj.com {"
SITE_LOG_OUTPUT_OPENER = (
    "output file /var/log/caddy/careertrellis-staging-access-v2.log {"
)


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
    return start, _block_end(lines, start)


def _block_end(lines: tuple[str, ...], start: int) -> int:
    depth = 0
    for index in range(start, len(lines)):
        depth += lines[index].count("{")
        depth -= lines[index].count("}")
        if depth == 0:
            return index
        if depth < 0:
            break
    raise CaddyPrivacyError("config_contract_invalid")


def validate_caddy_config(source: str) -> None:
    """Require both reviewed log filters in their exact active blocks."""
    lines = _active_config_lines(source)
    global_start, global_end = _block_bounds(lines, "{")
    runtime_log_start, runtime_log_end = _block_bounds(lines, "log default {")
    site_start, site_end = _block_bounds(lines, SITE_BLOCK_OPENER)
    log_start, log_end = _block_bounds(lines, "log {")
    filter_starts = tuple(
        index for index, line in enumerate(lines) if line == "format filter {"
    )
    if len(filter_starts) != 2:
        raise CaddyPrivacyError("config_contract_invalid")
    runtime_filter_start = next(
        (
            index
            for index in filter_starts
            if runtime_log_start < index < runtime_log_end
        ),
        -1,
    )
    site_filter_start = next(
        (index for index in filter_starts if log_start < index < log_end), -1
    )
    if runtime_filter_start < 0 or site_filter_start < 0:
        raise CaddyPrivacyError("config_contract_invalid")
    runtime_filter_end = _block_end(lines, runtime_filter_start)
    site_filter_end = _block_end(lines, site_filter_start)
    if not (
        global_start
        < runtime_log_start
        < runtime_filter_start
        < runtime_filter_end
        < runtime_log_end
        < global_end
        < site_start
        < log_start
        < site_filter_start
        < site_filter_end
        < log_end
        < site_end
    ):
        raise CaddyPrivacyError("config_contract_invalid")
    runtime_filter_lines = lines[runtime_filter_start + 1 : runtime_filter_end]
    site_filter_lines = lines[site_filter_start + 1 : site_filter_end]
    for directive in REQUIRED_RUNTIME_CONFIG_DIRECTIVES:
        if runtime_filter_lines.count(directive) != 1:
            raise CaddyPrivacyError("config_contract_invalid")
    for directive in REQUIRED_SITE_CONFIG_DIRECTIVES[:-1]:
        if site_filter_lines.count(directive) != 1:
            raise CaddyPrivacyError("config_contract_invalid")
    expected_counts = Counter(REQUIRED_CONFIG_DIRECTIVES)
    if any(lines.count(directive) != count for directive, count in expected_counts.items()):
        raise CaddyPrivacyError("config_contract_invalid")
    site_lines = lines[site_start + 1 : site_end]
    if (
        lines.count(REQUIRED_SITE_CONFIG_DIRECTIVES[-1]) != 1
        or site_lines.count(REQUIRED_SITE_CONFIG_DIRECTIVES[-1]) != 1
    ):
        raise CaddyPrivacyError("config_contract_invalid")
    log_lines = lines[log_start + 1 : log_end]
    runtime_log_lines = lines[runtime_log_start + 1 : runtime_log_end]
    if (
        log_lines.count(SITE_LOG_OUTPUT_OPENER) != 1
        or sum(line.startswith("output file ") for line in log_lines) != 1
        or any(line.startswith("output ") for line in runtime_log_lines)
        or any(line.startswith("exclude ") for line in runtime_log_lines)
    ):
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
    elif isinstance(value, (list, tuple)):
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


def _decoded_uri_layers(uri: str) -> tuple[str, ...]:
    layers = [uri]
    current = uri
    for _index in range(MAX_URI_DECODE_LAYERS):
        if re.search(r"%(?![0-9A-Fa-f]{2})", current):
            raise CaddyPrivacyError("uri_encoding_invalid")
        try:
            decoded = unquote(current, errors="strict")
        except UnicodeError as exc:
            raise CaddyPrivacyError("uri_encoding_invalid") from exc
        if decoded == current:
            return tuple(layers)
        layers.append(decoded)
        current = decoded
    if re.search(r"%[0-9A-Fa-f]{2}", current):
        raise CaddyPrivacyError("uri_encoding_excessive")
    return tuple(layers)


def _validate_uri(uri: str) -> bool:
    has_query = False
    for layer in _decoded_uri_layers(uri):
        if SENSITIVE_QUERY_PATTERN.search(layer):
            raise CaddyPrivacyError("sensitive_query_present")
        try:
            parsed = urlsplit(layer)
        except ValueError as exc:
            raise CaddyPrivacyError("uri_invalid") from exc
        normalized_path = parsed.path.rstrip("/") or "/"
        if normalized_path == "/auth" or normalized_path.startswith("/auth/"):
            raise CaddyPrivacyError("auth_route_logged")
        if parsed.query:
            has_query = True
            try:
                query_pairs = parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                    strict_parsing=False,
                    errors="strict",
                )
            except (UnicodeError, ValueError) as exc:
                raise CaddyPrivacyError("query_invalid") from exc
            if {key.lower() for key, _value in query_pairs} & SENSITIVE_QUERY_KEYS:
                raise CaddyPrivacyError("sensitive_query_present")
            _reject_sensitive_strings({"query": query_pairs})
        _reject_sensitive_strings(
            {"uri": layer, "path": parsed.path, "query": parsed.query}
        )
    return has_query


def _reject_sensitive_record(
    record: dict[str, object], response_headers: dict[str, object]
) -> None:
    challenge = response_headers.get("www-authenticate")
    if challenge is not None:
        values = challenge if isinstance(challenge, list) else [challenge]
        if values != [ALLOWED_BASIC_CHALLENGE]:
            raise CaddyPrivacyError("sensitive_value_present")
    _reject_sensitive_strings(
        {key: value for key, value in record.items() if key != "resp_headers"}
    )
    _reject_sensitive_strings(
        {
            key: value
            for key, value in response_headers.items()
            if key != "www-authenticate"
        }
    )


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
        try:
            line_size = len(line.encode("utf-8"))
        except UnicodeError as exc:
            raise CaddyPrivacyError("log_encoding_invalid") from exc
        if line_size > MAX_LINE_BYTES:
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
        if _validate_uri(uri):
            query_records += 1
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
        _reject_sensitive_record(record, response_headers)
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
        if argv and argv[-1] == "--allow-no-representative":
            allow_no_representative = True
            argv = argv[:-1]
        validate_only = len(argv) == 3 and argv[2] == "--validate-only"
        if (
            not validate_only
            and (len(argv) < 4 or argv[0] != "--config" or len(argv[2:]) % 2)
        ):
            raise CaddyPrivacyError("arguments_invalid")
        if validate_only and argv[0] != "--config":
            raise CaddyPrivacyError("arguments_invalid")
        config_path = Path(argv[1])
        log_paths: list[Path] = []
        for index in range(2, 2 if validate_only else len(argv), 2):
            if argv[index] != "--log":
                raise CaddyPrivacyError("arguments_invalid")
            log_paths.append(Path(argv[index + 1]))
        if len(set(log_paths)) != len(log_paths):
            raise CaddyPrivacyError("arguments_invalid")
        if config_path.is_symlink() or any(path.is_symlink() for path in log_paths):
            raise CaddyPrivacyError("source_invalid")
        validate_caddy_config(config_path.read_text(encoding="utf-8"))
        if validate_only:
            summary = CaddyPrivacySummary(0, 0, 0)
        else:
            with ExitStack() as stack:
                handles = []
                for log_path in log_paths:
                    if log_path.suffix == ".gz":
                        handle = stack.enter_context(
                            gzip.open(log_path, "rt", encoding="utf-8", errors="strict")
                        )
                    else:
                        handle = stack.enter_context(
                            log_path.open("r", encoding="utf-8", errors="strict")
                        )
                    handles.append(handle)
                summary = scan_json_lines(
                    (line for handle in handles for line in handle),
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
