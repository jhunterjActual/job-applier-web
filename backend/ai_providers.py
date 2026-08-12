"""Server-side AI provider selection and structured/multimodal adapters."""

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel

import config
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
}
DEFAULT_AI_PROVIDER = "gemini"
MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
PROVIDER_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class AIProviderSettings:
    provider: str
    model: str
    api_key: str


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
    return normalized if normalized in AI_PROVIDERS else DEFAULT_AI_PROVIDER


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
    if normalized == "openai":
        return bool(
            str(_profile_value(profile, "openai_api_key", "") or "").strip()
            or config.get_openai_api_key()
        )
    return bool(
        str(_profile_value(profile, "gemini_api_key", "") or "").strip()
        or config.get_gemini_api_key()
    )


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
    message = str(exc).lower()
    if "api key" in message or "unauth" in message or "401" in message:
        return AIProviderError("gemini", "authentication", "Google Gemini rejected the saved API key.")
    if "429" in message or "quota" in message or "rate" in message:
        return AIProviderError(
            "gemini",
            "rate_limit",
            "Google Gemini rate limit or quota was reached. Wait and try again, or choose another provider.",
        )
    if "404" in message or "not found" in message or "model" in message and "invalid" in message:
        return AIProviderError("gemini", "model_unavailable", "The selected Google Gemini model is unavailable for this API key.")
    if "403" in message or "permission" in message:
        return AIProviderError("gemini", "permission", "Google Gemini denied access for the saved API key and selected model.")
    return AIProviderError(
        "gemini",
        "provider_error",
        "Google Gemini could not complete the request. Check the selected model and try again.",
    )


def _generate_gemini(settings: AIProviderSettings, prompt: str, schema: type[BaseModel]) -> dict:
    try:
        client = genai.Client(api_key=settings.api_key)
        response = client.models.generate_content(
            model=settings.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
    except AIProviderError:
        raise
    except Exception as exc:
        raise _gemini_error(exc) from exc

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed.model_dump()
    if parsed is not None:
        try:
            return schema.model_validate(parsed).model_dump()
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
    direct = document.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in document.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
            if content.get("type") == "refusal":
                raise AIProviderError(
                    "openai",
                    "refusal",
                    "OpenAI declined this request. Review the job and resume content, or choose another provider.",
                )
    return ""


def _generate_openai(settings: AIProviderSettings, prompt: str, schema: type[BaseModel]) -> dict:
    payload = {
        "model": settings.model,
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
        with urllib.request.urlopen(request, timeout=PROVIDER_TIMEOUT_SECONDS) as response:
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
        client = genai.Client(api_key=settings.api_key)
        response = client.models.generate_content(
            model=settings.model,
            contents=[prompt, types.Part.from_bytes(data=image, mime_type="image/png")],
        )
    except AIProviderError:
        raise
    except Exception as exc:
        raise _gemini_error(exc) from exc
    text = (getattr(response, "text", "") or "").strip()
    if not text:
        raise AIProviderError("gemini", "empty_response", "Google Gemini could not read text from a resume page image.")
    return text


def _generate_openai_image_text(settings: AIProviderSettings, prompt: str, image: bytes) -> str:
    payload = {
        "model": settings.model,
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


def generate_structured(settings: AIProviderSettings, prompt: str, schema: type[BaseModel]) -> dict:
    """Generate and validate structured data through the selected provider."""
    provider = normalize_provider(settings.provider)
    normalized = AIProviderSettings(
        provider,
        (settings.model or default_model(provider)).strip(),
        (settings.api_key or "").strip(),
    )
    if not normalized.api_key:
        raise AIProviderError(
            provider,
            "missing_key",
            f"{provider_label(provider)} API key is not configured. Save one in Profile & Resume.",
        )
    if provider == "openai":
        return _generate_openai(normalized, prompt, schema)
    return _generate_gemini(normalized, prompt, schema)


def extract_text_from_images(
    settings: AIProviderSettings,
    images: list[bytes],
    cancel_check=None,
) -> list[str]:
    """OCR bounded resume-page images through the explicitly selected provider."""
    provider = normalize_provider(settings.provider)
    normalized = AIProviderSettings(
        provider,
        (settings.model or default_model(provider)).strip(),
        (settings.api_key or "").strip(),
    )
    if not normalized.api_key:
        raise AIProviderError(
            provider,
            "missing_key",
            f"{provider_label(provider)} API key is not configured. Save one in Profile & Resume before using AI OCR.",
        )
    prompt = (
        "Transcribe all visible resume text from this single page in natural reading order. "
        "Preserve headings, bullets, dates, names, URLs, and email addresses using plain text or Markdown. "
        "Do not summarize, correct, infer, or invent content. Return only the transcription."
    )
    results: list[str] = []
    for image in images:
        if cancel_check:
            cancel_check()
        if not image:
            raise AIProviderError(provider, "invalid_input", "A resume page image was empty before OCR.")
        if provider == "openai":
            text = _generate_openai_image_text(normalized, prompt, image)
        else:
            text = _generate_gemini_image_text(normalized, prompt, image)
        results.append(text)
        if cancel_check:
            cancel_check()
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
