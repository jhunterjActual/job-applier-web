"""Server-side AI provider selection and structured/multimodal adapters."""

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import errors, types
import httpx
from pydantic import BaseModel

import config
import ai_contracts as contracts
from provider_secrets import (
    SHARED_SECRET_STORE,
    current_provider_secret,
    current_provider_secret_configured,
    provider_secret_store_kind,
)


AI_PROVIDERS = {
    "gemini": {
        "label": "Google Gemini",
        "default_model": "gemini-2.5-flash",
    },
    "openai": {
        "label": "OpenAI",
        "default_model": "gpt-5-mini",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "default_model": "claude-sonnet-5-5",
    },
}
DEFAULT_AI_PROVIDER = "gemini"
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
PROVIDER_TIMEOUT_SECONDS = 90
KEYWORD_PROVIDER_TIMEOUT_SECONDS = 45


@dataclass(frozen=True)
class AIProviderSettings:
    provider: str
    model: str
    api_key: str = field(repr=False)


class AIProviderError(RuntimeError):
    """A privacy-safe, provider-specific error suitable for the local UI."""

    def __init__(self, provider: str, code: str, message: str):
        super().__init__(message)
        self.provider = provider
        self.code = code


class CapabilityResponse(BaseModel):
    ready: bool


def provider_label(provider: str) -> str:
    normalized = normalize_provider(provider)
    return AI_PROVIDERS[normalized]["label"]


def normalize_provider(provider: str | None) -> str:
    normalized = (provider or DEFAULT_AI_PROVIDER).strip().lower()
    if normalized not in AI_PROVIDERS:
        raise AIProviderError('unknown', 'unsupported_provider', 'The selected AI provider is not supported.')
    return normalized


def default_model(provider: str) -> str:
    return AI_PROVIDERS[normalize_provider(provider)]["default_model"]


def _profile_value(profile: Any, name: str, default: Any = "") -> Any:
    """Read sqlite rows and test mappings that may predate provider columns."""
    if profile is None:
        return default
    try:
        keys = profile.keys()
    except AttributeError:
        keys = None
    if keys is not None and name not in keys:
        return default
    try:
        value = profile[name]
    except (KeyError, TypeError, IndexError):
        return default
    return default if value is None else value


def settings_from_profile(profile: Any) -> AIProviderSettings:
    """Resolve the selected provider without ever serializing its credential."""
    provider = normalize_provider(_profile_value(profile, "ai_provider", DEFAULT_AI_PROVIDER))
    model = str(_profile_value(profile, "ai_model", "") or "").strip() or default_model(provider)
    if provider_secret_store_kind() == SHARED_SECRET_STORE:
        return AIProviderSettings(
            provider=provider,
            model=model,
            api_key=current_provider_secret(provider),
        )
    if provider == "anthropic":
        raise AIProviderError(provider, 'unsupported_storage',
                              'Claude requires encrypted hosted credential storage.')
    if provider == "openai":
        saved_key = str(_profile_value(profile, "openai_api_key", "") or "").strip()
        api_key = saved_key or config.get_openai_api_key()
    else:
        saved_key = str(_profile_value(profile, "gemini_api_key", "") or "").strip()
        api_key = saved_key or config.get_gemini_api_key()
    return AIProviderSettings(provider=provider, model=model, api_key=api_key)


def provider_key_configured(profile: Any, provider: str) -> bool:
    normalized = normalize_provider(provider)
    if provider_secret_store_kind() == SHARED_SECRET_STORE:
        return current_provider_secret_configured(normalized)
    if normalized == "anthropic":
        return False
    if normalized == "openai":
        return bool(
            str(_profile_value(profile, "openai_api_key", "") or "").strip()
            or config.get_openai_api_key()
        )
    return bool(
        str(_profile_value(profile, "gemini_api_key", "") or "").strip()
        or config.get_gemini_api_key()
    )


def claude_workflow_key() -> str:
    """Resolve only the authenticated owner's encrypted Claude key."""
    if provider_secret_store_kind() != SHARED_SECRET_STORE:
        raise AIProviderError('anthropic', 'unsupported_storage',
                              'Claude requires encrypted hosted credential storage.')
    return current_provider_secret('anthropic')


def _strict_json_schema(schema: type[BaseModel]) -> dict:
    """Apply OpenAI strict-mode object constraints to a Pydantic schema."""
    document = schema.model_json_schema()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object" or "properties" in value:
                properties = value.get("properties", {})
                value["additionalProperties"] = False
                value["required"] = list(properties)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return document


def _validated_data(raw_text: str, schema: type[BaseModel], provider: str) -> dict:
    if not isinstance(raw_text, str) or len(raw_text.encode('utf-8')) > MAX_PROVIDER_RESPONSE_BYTES:
        raise AIProviderError(provider, 'oversized_response', 'The AI response exceeded the supported size.')
    text = (raw_text or "").strip()
    if not text:
        raise AIProviderError(
            provider,
            "empty_response",
            f"{provider_label(provider)} returned an empty structured response.",
        )
    try:
        return schema.model_validate_json(text).model_dump()
    except Exception as exc:
        raise AIProviderError(
            provider,
            "invalid_response",
            f"{provider_label(provider)} returned structured output that could not be validated. Try again or choose another model.",
        ) from exc


def _gemini_error(exc: Exception) -> AIProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return AIProviderError("gemini", "timeout", "Google Gemini timed out. Try again later.")
    if isinstance(exc, httpx.TransportError):
        return AIProviderError("gemini", "network", "Google Gemini could not be reached. Try again later.")
    # Prefer the SDK's status over untrusted response text. In particular, a
    # server error mentioning a quota must not be classified as a 429 response.
    status = exc.code if isinstance(exc, errors.APIError) else None
    if status == 400:
        details = exc.details if isinstance(exc.details, dict) else {}
        error = details.get("error", details)
        reasons = error.get("details", []) if isinstance(error, dict) else []
        if isinstance(reasons, list) and any(
            isinstance(item, dict) and item.get("reason") == "API_KEY_INVALID"
            for item in reasons
        ):
            return AIProviderError("gemini", "authentication", "Google Gemini rejected the saved API key.")
        return AIProviderError("gemini", "unsupported_request", "Google Gemini rejected the request configuration. Check the selected model.")
    message = str(exc).lower()
    if status == 401 or (status is None and ("api key" in message or "unauth" in message or "401" in message)):
        return AIProviderError("gemini", "authentication", "Google Gemini rejected the saved API key.")
    if status == 429 or (status is None and ("429" in message or "quota" in message or re.search(r"\brate[ -]?limit", message))):
        return AIProviderError(
            "gemini",
            "rate_limit",
            "Google Gemini rate limit or quota was reached. Wait and try again, or choose another provider.",
        )
    if status == 404 or (status is None and ("404" in message or "not found" in message or "model" in message and "invalid" in message)):
        return AIProviderError("gemini", "model_unavailable", "The selected Google Gemini model is unavailable for this API key.")
    if status == 403 or (status is None and ("403" in message or "permission" in message)):
        return AIProviderError("gemini", "permission", "Google Gemini denied access for the saved API key and selected model.")
    return AIProviderError(
        "gemini",
        "provider_error",
        "Google Gemini could not complete the request. Check the selected model and try again.",
    )


def _generate_gemini(
    settings: AIProviderSettings,
    prompt: str,
    schema: type[BaseModel],
    *,
    timeout_seconds: int,
    disable_thinking: bool,
    max_output_tokens: int,
) -> dict:
    try:
        client = genai.Client(
            api_key=settings.api_key,
            http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
        )
        response = client.models.generate_content(
            model=settings.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                max_output_tokens=max_output_tokens,
                thinking_config=(
                    types.ThinkingConfig(thinking_budget=0)
                    if disable_thinking
                    else None
                ),
            ),
        )
    except AIProviderError:
        raise
    except Exception as exc:
        raise _gemini_error(exc) from exc

    contracts.observe_usage('gemini', getattr(response, 'usage_metadata', None))
    _check_gemini_completion(response)
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return _validated_data(parsed.model_dump_json(), schema, 'gemini')
    if parsed is not None:
        try:
            return _validated_data(schema.model_validate(parsed).model_dump_json(), schema, 'gemini')
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderError(
                "gemini",
                "invalid_response",
                "Google Gemini returned structured output that could not be validated. Try again or choose another model.",
            ) from exc
    return _validated_data(getattr(response, "text", ""), schema, "gemini")


def _openai_http_error(exc: urllib.error.HTTPError) -> AIProviderError:
    status = exc.code
    messages = {
        400: (
            "unsupported_request",
            "OpenAI rejected the structured-output request. Check that the selected model supports the Responses API.",
        ),
        401: ("authentication", "OpenAI rejected the saved API key."),
        403: ("permission", "OpenAI denied access for the saved API key and selected model."),
        404: ("model_unavailable", "The selected OpenAI model is unavailable for this API key."),
        429: (
            "rate_limit",
            "OpenAI rate limit or quota was reached. Wait and try again, or choose another provider.",
        ),
    }
    code, message = messages.get(status, ("provider_error", "OpenAI could not complete the request. Try again later."))
    return AIProviderError("openai", code, message)


def _openai_output_text(document: dict) -> str:
    if not isinstance(document, dict):
        raise AIProviderError('openai', 'invalid_response', 'OpenAI returned an invalid response envelope.')
    contracts.observe_usage('openai', document.get('usage'))
    if document.get('status') == 'incomplete':
        raise AIProviderError('openai', 'truncated', 'OpenAI did not complete the requested output.')
    if document.get('status') not in (None, 'completed'):
        raise AIProviderError('openai', 'provider_error', 'OpenAI did not complete the request.')
    direct = document.get("output_text")
    parts = []
    output = document.get('output', [])
    if not isinstance(output, list):
        raise AIProviderError('openai', 'invalid_response', 'OpenAI returned an invalid response envelope.')
    for item in output:
        if not isinstance(item, dict):
            continue
        content_items = item.get('content', [])
        if not isinstance(content_items, list):
            raise AIProviderError('openai', 'invalid_response', 'OpenAI returned invalid output content.')
        for content in content_items:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content['text'])
            if content.get("type") == "refusal":
                raise AIProviderError(
                    "openai",
                    "refusal",
                    "OpenAI declined this request. Review the job and resume content, or choose another provider.",
                )
    return direct if isinstance(direct, str) and direct.strip() else ''.join(parts)


def _check_gemini_completion(response):
    feedback = getattr(response, 'prompt_feedback', None)
    blocked = getattr(feedback, 'block_reason', None)
    if isinstance(blocked, str) and blocked not in ('', 'BLOCK_REASON_UNSPECIFIED'):
        raise AIProviderError('gemini', 'refusal', 'Google Gemini declined this request.')
    candidates = getattr(response, 'candidates', None)
    for candidate in candidates if isinstance(candidates, (list, tuple)) else []:
        reason = getattr(candidate, 'finish_reason', None)
        if reason == 'MAX_TOKENS':
            raise AIProviderError('gemini', 'truncated', 'Google Gemini did not complete the requested output.')
        if isinstance(reason, str) and reason not in ('', 'STOP', 'FINISH_REASON_UNSPECIFIED'):
            raise AIProviderError('gemini', 'refusal', 'Google Gemini declined this request.')


def _generate_openai(
    settings: AIProviderSettings,
    prompt: str,
    schema: type[BaseModel],
    *,
    timeout_seconds: int,
    max_output_tokens: int,
) -> dict:
    payload = {
        "model": settings.model,
        "max_output_tokens": max_output_tokens,
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema.__name__.lower(),
                "schema": _strict_json_schema(schema),
                "strict": True,
            }
        },
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "CareerTrellis/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _openai_http_error(exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AIProviderError(
            "openai",
            "network",
            "OpenAI could not be reached. Check the network connection and try again.",
        ) from exc
    except Exception as exc:
        raise AIProviderError("openai", "provider_error", "OpenAI could not complete the request. Try again later.") from exc

    if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
        raise AIProviderError(
            "openai",
            "oversized_response",
            "OpenAI returned an unexpectedly large response, so it was rejected.",
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AIProviderError("openai", "invalid_response", "OpenAI returned a response that could not be read.") from exc
    return _validated_data(_openai_output_text(document), schema, "openai")


def _generate_gemini_image_text(settings: AIProviderSettings, prompt: str, image: bytes) -> str:
    try:
        client = genai.Client(
            api_key=settings.api_key,
            http_options=types.HttpOptions(timeout=PROVIDER_TIMEOUT_SECONDS * 1000),
        )
        response = client.models.generate_content(
            model=settings.model,
            contents=[prompt, types.Part.from_bytes(data=image, mime_type="image/png")],
            config=types.GenerateContentConfig(max_output_tokens=contracts.OPERATIONS['resume_ocr'].max_output_tokens),
        )
    except AIProviderError:
        raise
    except Exception as exc:
        raise _gemini_error(exc) from exc
    contracts.observe_usage('gemini', getattr(response, 'usage_metadata', None))
    _check_gemini_completion(response)
    text = (getattr(response, "text", "") or "").strip()
    if len(text.encode('utf-8')) > MAX_PROVIDER_RESPONSE_BYTES:
        raise AIProviderError('gemini', 'oversized_response', 'Google Gemini returned an oversized OCR response.')
    if not text:
        raise AIProviderError("gemini", "empty_response", "Google Gemini could not read text from a resume page image.")
    return text


def _generate_openai_image_text(settings: AIProviderSettings, prompt: str, image: bytes) -> str:
    payload = {
        "model": settings.model,
        "max_output_tokens": contracts.OPERATIONS['resume_ocr'].max_output_tokens,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{base64.b64encode(image).decode('ascii')}",
                },
            ],
        }],
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "CareerTrellis/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=PROVIDER_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            raise AIProviderError(
                "openai",
                "unsupported_request",
                "OpenAI rejected the OCR request. Check that the selected model supports image input.",
            ) from exc
        raise _openai_http_error(exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AIProviderError(
            "openai",
            "network",
            "OpenAI could not be reached. Check the network connection and try again.",
        ) from exc
    except Exception as exc:
        raise AIProviderError("openai", "provider_error", "OpenAI could not complete the OCR request.") from exc

    if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
        raise AIProviderError("openai", "oversized_response", "OpenAI returned an unexpectedly large OCR response.")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AIProviderError("openai", "invalid_response", "OpenAI returned an OCR response that could not be read.") from exc
    text = _openai_output_text(document).strip()
    if not text:
        raise AIProviderError("openai", "empty_response", "OpenAI could not read text from a resume page image.")
    return text


def _request(settings, operation, invoke, *, cancel_check=None, rate_card=None):
    """One dispatch, one optional receipt; never retries or changes the payer."""
    records = contracts._RECEIPTS.get()
    if rate_card is not None and (rate_card.provider, rate_card.model) != (settings.provider, settings.model):
        raise AIProviderError(settings.provider, 'invalid_rate', 'The AI cost estimate does not match the selected model.')
    if records is not None:
        with contracts._RECEIPT_LOCK.get():
            if len(records) >= contracts.MAX_RECEIPTS:
                raise AIProviderError(settings.provider, 'receipt_limit', 'The AI receipt collection limit was reached.')
            receipt_index = len(records)
            records.append(contracts.Receipt(settings.provider, settings.model, operation, 'in_progress',
                                            False, contracts.Usage(), None, None))
    state = {'usage': contracts.Usage()}
    token = contracts._ATTEMPT.set(state)
    dispatched = False
    outcome = 'provider_error'
    try:
        if cancel_check:
            outcome = 'cancelled'
            cancel_check()
        outcome = 'provider_error'
        dispatched = True
        result = invoke()
        if cancel_check:
            outcome = 'cancelled'
            cancel_check()
        outcome = 'success'
        return result
    except AIProviderError as exc:
        outcome = exc.code
        raise
    finally:
        contracts._ATTEMPT.reset(token)
        usage = state['usage']
        receipt = contracts.Receipt(settings.provider, settings.model, operation, outcome,
                                    dispatched, usage, contracts.estimated_cost(usage, rate_card),
                                    rate_card.version if rate_card else None)
        if records is not None:
            with contracts._RECEIPT_LOCK.get():
                records[receipt_index] = receipt


def _settings_for_operation(settings, operation, *, structured):
    provider = normalize_provider(settings.provider)
    if operation not in contracts.OPERATIONS or contracts.OPERATIONS[operation].structured != structured:
        raise AIProviderError(provider, 'unsupported_operation', 'The requested AI operation is not supported.')
    normalized = AIProviderSettings(provider, (settings.model or '').strip() or default_model(provider),
                                    (settings.api_key or '').strip())
    # Unlisted custom models retain the existing remote validation path; no
    # unverified local capability or account-availability claim is made for them.
    capabilities = contracts.MODEL_CAPABILITIES.get((provider, normalized.model))
    required = 'structured' if structured else 'image'
    if capabilities is not None and required not in capabilities:
        raise AIProviderError(provider, 'unsupported_operation', 'The selected model does not support this AI operation.')
    if not normalized.api_key:
        raise AIProviderError(provider, 'missing_key',
                              f'{provider_label(provider)} API key is not configured. Save one in Profile & Resume.')
    return normalized


def generate_structured(
    settings: AIProviderSettings,
    prompt: str,
    schema: type[BaseModel],
    *,
    timeout_seconds: int = PROVIDER_TIMEOUT_SECONDS,
    disable_thinking: bool = False,
    operation: str | None = None,
    cancel_check=None,
    rate_card=None,
) -> dict:
    """Generate and validate structured data through the selected provider."""
    if normalize_provider(settings.provider) == 'anthropic':
        if rate_card is not None:
            raise AIProviderError('anthropic', 'invalid_rate', 'Claude cost estimation is not available.')
        from claude_adapter import generate_structured as generate_claude
        return generate_claude(settings, prompt, schema, operation=operation,
                               timeout_seconds=timeout_seconds, cancel_check=cancel_check,
                               disable_thinking=disable_thinking)
    operation = operation or contracts.SCHEMA_OPERATIONS.get(schema.__name__, 'structured')
    normalized = _settings_for_operation(settings, operation, structured=True)
    limits = contracts.OPERATIONS[operation]
    if (not isinstance(prompt, str) or len(prompt.encode('utf-8')) > limits.max_input_bytes
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= PROVIDER_TIMEOUT_SECONDS):
        raise AIProviderError(normalized.provider, 'invalid_input', 'The AI input or timeout exceeds the supported limit.')
    def invoke():
        if normalized.provider == 'openai':
            return _generate_openai(normalized, prompt, schema, timeout_seconds=timeout_seconds,
                                    max_output_tokens=limits.max_output_tokens)
        return _generate_gemini(normalized, prompt, schema, timeout_seconds=timeout_seconds,
                                disable_thinking=disable_thinking, max_output_tokens=limits.max_output_tokens)
    return _request(normalized, operation, invoke, cancel_check=cancel_check, rate_card=rate_card)


def extract_text_from_images(
    settings: AIProviderSettings,
    images: list[bytes],
    cancel_check=None,
    *,
    rate_card=None,
) -> list[str]:
    """OCR bounded resume-page images through the explicitly selected provider."""
    if normalize_provider(settings.provider) == 'anthropic':
        if rate_card is not None:
            raise AIProviderError('anthropic', 'invalid_rate', 'Claude cost estimation is not available.')
        from claude_adapter import extract_text_from_images as extract_claude
        return extract_claude(settings, images, cancel_check)
    normalized = _settings_for_operation(settings, 'resume_ocr', structured=False)
    provider = normalized.provider
    if (not isinstance(images, list) or len(images) > contracts.MAX_OCR_PAGES
            or any(not isinstance(image, bytes) or not image
                   or len(image) > contracts.OPERATIONS['resume_ocr'].max_input_bytes for image in images)):
        raise AIProviderError(provider, 'invalid_input', 'The resume images exceed the supported page or size limit.')
    prompt = (
        "Transcribe all visible resume text from this single page in natural reading order. "
        "Preserve headings, bullets, dates, names, URLs, and email addresses using plain text or Markdown. "
        "Do not summarize, correct, infer, or invent content. Return only the transcription."
    )
    results: list[str] = []
    for image in images:
        def invoke():
            if provider == 'openai':
                return _generate_openai_image_text(normalized, prompt, image)
            return _generate_gemini_image_text(normalized, prompt, image)
        text = _request(normalized, 'resume_ocr', invoke, cancel_check=cancel_check, rate_card=rate_card)
        results.append(text)
    return results


def validate_provider_capability(settings: AIProviderSettings) -> dict:
    """Run a minimal schema request to validate key, model, and API capability."""
    data = generate_structured(
        settings,
        "Return ready=true using the supplied response schema. Do not include any other content.",
        CapabilityResponse,
    )
    if data.get("ready") is not True:
        raise AIProviderError(
            settings.provider,
            "capability",
            f"{provider_label(settings.provider)} did not confirm structured-output capability.",
        )
    return {
        "success": True,
        "provider": normalize_provider(settings.provider),
        "provider_label": provider_label(settings.provider),
        "model": settings.model,
        "message": f"{provider_label(settings.provider)} is ready for matching and tailoring.",
    }
