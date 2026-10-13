"""AI-002 transport behind hosted encrypted-key profile admission.

No key lookup, persistence, retries, redirects, tools, or payer fallback.
Callers must supply explicit settings; all current acceptance uses fake transports.
"""
import base64
import json
import urllib.error
import urllib.request

import ai_contracts as contracts
from ai_providers import AIProviderError, MAX_PROVIDER_RESPONSE_BYTES, _request


MODEL = 'claude-sonnet-5-5'
ENDPOINT = 'https://api.anthropic.com/v1/messages'
MAX_ENCODED_IMAGE_BYTES = 10_000_000
# A reviewed forward-correction release can disable Claude without changing
# stored profiles or encrypted credentials. No environment override is accepted.
CLAUDE_ENABLED = True


def claude_available():
    return CLAUDE_ENABLED


def _error(code):
    return AIProviderError('anthropic', code, f'Claude request did not complete ({code}).')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _error('redirect_rejected')


def _schema(schema):
    document = schema.model_json_schema()
    # The provider cannot enforce these constraints; the original Pydantic
    # schema still enforces them on receipt. Only static schemas are supplied.
    unsupported = {'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
                   'multipleOf', 'minLength', 'maxLength', 'minItems', 'maxItems'}
    def visit(value):
        if isinstance(value, dict):
            constraints = {key: value.pop(key) for key in unsupported if key in value}
            if constraints:
                value['description'] = (value.get('description', '') +
                                        ' Constraints: ' + json.dumps(constraints, sort_keys=True)).strip()
            if value.get('type') == 'object':
                value['additionalProperties'] = False
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(document)
    return document


def _preflight(settings, operation, structured, timeout):
    if not claude_available():
        raise _error('model_unavailable')
    if settings.provider != 'anthropic':
        raise _error('unsupported_provider')
    if settings.model != MODEL:
        raise _error('model_unavailable')
    if not settings.api_key or not settings.api_key.strip():
        raise _error('missing_key')
    limits = contracts.OPERATIONS.get(operation)
    if limits is None or limits.structured != structured:
        raise _error('unsupported_operation')
    if type(timeout) is not int or not 1 <= timeout <= 90:
        raise _error('invalid_input')
    return limits


def _message(settings, content, max_tokens, timeout, schema=None):
    if not claude_available():
        raise _error('model_unavailable')
    payload = {'model': settings.model, 'max_tokens': max_tokens,
               'thinking': {'type': 'between_tools'},
               'messages': [{'role': 'user', 'content': content}]}
    if schema is not None:
        payload['output_config'] = {'format': {'type': 'json_schema', 'schema': schema}}
    request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode('utf-8'),
        headers={'x-api-key': settings.api_key, 'anthropic-version': '2023-06-01',
                 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except AIProviderError:
        raise
    except urllib.error.HTTPError as exc:
        raise _error({400: 'unsupported_request', 401: 'authentication', 403: 'permission',
                      404: 'model_unavailable', 413: 'invalid_input', 429: 'rate_limit',
                      529: 'overloaded'}.get(exc.code, 'provider_error')) from None
    except TimeoutError:
        raise _error('timeout') from None
    except urllib.error.URLError as exc:
        raise _error('timeout' if isinstance(exc.reason, TimeoutError) else 'network') from None
    except Exception:
        raise _error('provider_error') from None
    if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
        raise _error('oversized_response')
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise _error('invalid_response') from None
    if not isinstance(result, dict):
        raise _error('invalid_response')
    contracts.observe_usage('anthropic', result.get('usage'))
    reason = result.get('stop_reason')
    if reason == 'refusal':
        raise _error('refusal')
    if reason in ('max_tokens', 'model_context_window_exceeded'):
        raise _error('truncated')
    if reason != 'end_turn':
        raise _error('invalid_response')
    blocks = result.get('content')
    if not isinstance(blocks, list):
        raise _error('invalid_response')
    texts = []
    for block in blocks:
        if not isinstance(block, dict) or block.get('type') != 'text' or not isinstance(block.get('text'), str):
            raise _error('invalid_response')
        texts.append(block['text'])
    text = ''.join(texts).strip()
    if not text:
        raise _error('empty_response')
    return text


def generate_structured(settings, prompt, schema, *, operation=None, timeout_seconds=90,
                        cancel_check=None, disable_thinking=False):
    operation = operation or contracts.SCHEMA_OPERATIONS.get(schema.__name__, 'structured')
    limits = _preflight(settings, operation, True, timeout_seconds)
    if not isinstance(prompt, str) or len(prompt.encode('utf-8')) > limits.max_input_bytes:
        raise _error('invalid_input')
    document = _schema(schema)
    def invoke():
        text = _message(settings, prompt, limits.max_output_tokens, timeout_seconds, document)
        try:
            return schema.model_validate_json(text).model_dump()
        except Exception:
            raise _error('invalid_response') from None
    # No RateCard accepted: the shared estimator does not model cache writes.
    return _request(settings, operation, invoke, cancel_check=cancel_check)


def extract_text_from_images(settings, images, cancel_check=None):
    limits = _preflight(settings, 'resume_ocr', False, 90)
    if (not isinstance(images, list) or len(images) > contracts.MAX_OCR_PAGES
            or any(not isinstance(page, bytes) or not page or len(page) > limits.max_input_bytes
                   or 4 * ((len(page) + 2) // 3) > MAX_ENCODED_IMAGE_BYTES
                   for page in images)):
        raise _error('invalid_input')
    results = []
    for page in images:
        content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                    'data': base64.b64encode(page).decode('ascii')}},
                   {'type': 'text', 'text': 'Transcribe this resume page in reading order. '
                    'Preserve headings and details. Do not infer, summarize or invent content.'}]
        results.append(_request(settings, 'resume_ocr',
            lambda: _message(settings, content, limits.max_output_tokens, 90), cancel_check=cancel_check))
    return results
