"""Privacy-safe headquarters lookup adapters for selectable maps providers."""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field

import config
from ai_providers import AIProviderError, AIProviderSettings, generate_structured
from provider_secrets import (
    SHARED_SECRET_STORE,
    current_provider_secret,
    current_provider_secret_configured,
    provider_secret_store_kind,
)


MAPS_PROVIDERS = {
    "google": {"label": "Google Places", "requires_key": True},
    "openstreetmap": {"label": "OpenStreetMap Nominatim", "requires_key": False},
}
DEFAULT_MAPS_PROVIDER = "openstreetmap"
GOOGLE_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
OPENSTREETMAP_ATTRIBUTION = "© OpenStreetMap contributors"
MAX_MAPS_RESPONSE_BYTES = 1024 * 1024
MAPS_TIMEOUT_SECONDS = 12
NOMINATIM_MIN_INTERVAL_SECONDS = 1.0
NOMINATIM_USER_AGENT = "CareerTrellis/1.0 (local single-user app; https://github.com/jhunterjActual/job-applier)"

_nominatim_lock = threading.Lock()
_last_nominatim_request_at = 0.0


@dataclass(frozen=True)
class MapsProviderSettings:
    provider: str
    api_key: str = ""


@dataclass(frozen=True)
class HeadquartersResult:
    address: str = "Unknown"
    source: str = ""
    attribution: str = ""
    country_code: str = ""
    warning: str = ""


class MapsProviderError(RuntimeError):
    """A provider-specific error whose message is safe to show locally."""

    def __init__(self, provider: str, code: str, message: str):
        super().__init__(message)
        self.provider = provider
        self.code = code


class HeadquartersAIResponse(BaseModel):
    address: str = Field(max_length=250)
    country_code: str = Field(default="", max_length=3)
    verified: bool


def _profile_value(profile: Any, name: str, default: Any = "") -> Any:
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


def normalize_maps_provider(provider: str | None) -> str:
    normalized = (provider or DEFAULT_MAPS_PROVIDER).strip().lower()
    return normalized if normalized in MAPS_PROVIDERS else DEFAULT_MAPS_PROVIDER


def maps_provider_label(provider: str) -> str:
    return MAPS_PROVIDERS[normalize_maps_provider(provider)]["label"]


def maps_settings_from_profile(profile: Any) -> MapsProviderSettings:
    provider = normalize_maps_provider(_profile_value(profile, "maps_provider", DEFAULT_MAPS_PROVIDER))
    if provider_secret_store_kind() == SHARED_SECRET_STORE:
        saved_key = current_provider_secret("google_maps") if provider == "google" else ""
        return MapsProviderSettings(provider, saved_key)
    saved_key = str(_profile_value(profile, "google_maps_api_key", "") or "").strip()
    return MapsProviderSettings(provider, saved_key or config.get_google_maps_api_key())


def maps_provider_ready(profile: Any, provider: str | None = None) -> bool:
    selected = normalize_maps_provider(provider or _profile_value(profile, "maps_provider", DEFAULT_MAPS_PROVIDER))
    if selected == "openstreetmap":
        return True
    if provider_secret_store_kind() == SHARED_SECRET_STORE:
        return current_provider_secret_configured("google_maps")
    return bool(
        str(_profile_value(profile, "google_maps_api_key", "") or "").strip()
        or config.get_google_maps_api_key()
    )


def _looks_like_us_address(address: str) -> bool:
    lowered = f" {address or ''} ".lower()
    return any(marker in lowered for marker in (" united states ", " united states of america ", " usa "))


def _headquarters_query(company_name: str, prefer_us: bool) -> str:
    scope = "United States headquarters" if prefer_us else "global headquarters"
    return f"{company_name.strip()} {scope}"


def _read_json(response: Any, provider: str) -> Any:
    raw = response.read(MAX_MAPS_RESPONSE_BYTES + 1)
    if len(raw) > MAX_MAPS_RESPONSE_BYTES:
        raise MapsProviderError(
            provider,
            "oversized_response",
            f"{maps_provider_label(provider)} returned an unexpectedly large response, so it was rejected.",
        )
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MapsProviderError(
            provider,
            "invalid_response",
            f"{maps_provider_label(provider)} returned a response that could not be read.",
        ) from exc


def _http_error(provider: str, status: int) -> MapsProviderError:
    label = maps_provider_label(provider)
    if status in {401, 403}:
        message = f"{label} rejected the saved credential or denied access."
        code = "authentication"
    elif status == 429:
        message = f"{label} rate limit or quota was reached. Wait before trying again."
        code = "rate_limit"
    elif status == 400:
        message = f"{label} rejected the lookup request. Check the provider configuration."
        code = "unsupported_request"
    elif status >= 500:
        message = f"{label} is temporarily unavailable. Try again later."
        code = "unavailable"
    else:
        message = f"{label} could not complete the lookup."
        code = "provider_error"
    return MapsProviderError(provider, code, message)


def _country_code_from_google(place: dict) -> str:
    for component in place.get("addressComponents", []):
        if not isinstance(component, dict) or "country" not in component.get("types", []):
            continue
        return str(component.get("shortText") or component.get("longText") or "").strip().upper()
    return ""


def _query_google(settings: MapsProviderSettings, query: str) -> list[HeadquartersResult]:
    if not settings.api_key:
        raise MapsProviderError(
            "google",
            "missing_key",
            "Google Places API key is not configured. Save one in Profile & Resume.",
        )
    payload = json.dumps({"textQuery": query, "maxResultCount": 5}).encode("utf-8")
    request = urllib.request.Request(
        GOOGLE_PLACES_SEARCH_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.api_key,
            "X-Goog-FieldMask": "places.formattedAddress,places.addressComponents",
            "User-Agent": "CareerTrellis/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=MAPS_TIMEOUT_SECONDS) as response:
            document = _read_json(response, "google")
    except urllib.error.HTTPError as exc:
        raise _http_error("google", exc.code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MapsProviderError(
            "google", "network", "Google Places could not be reached. Check the network connection and try again."
        ) from exc

    results = []
    if not isinstance(document, dict):
        return results
    for place in document.get("places", []):
        if not isinstance(place, dict):
            continue
        address = str(place.get("formattedAddress") or "").strip()
        if address:
            results.append(
                HeadquartersResult(
                    address=address[:250],
                    source="google",
                    attribution="Google Maps",
                    country_code=_country_code_from_google(place),
                )
            )
    return results


def _nominatim_base_url() -> str:
    raw = config.get_nominatim_base_url().strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise MapsProviderError("openstreetmap", "configuration", "The configured Nominatim service URL is invalid.")
    if parsed.query or parsed.fragment:
        raise MapsProviderError("openstreetmap", "configuration", "The configured Nominatim service URL is invalid.")
    return raw


def _wait_for_nominatim(cancel_check: Callable[[], None] | None) -> None:
    global _last_nominatim_request_at
    while True:
        remaining = NOMINATIM_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_nominatim_request_at)
        if remaining <= 0:
            _last_nominatim_request_at = time.monotonic()
            return
        if cancel_check:
            cancel_check()
        time.sleep(min(remaining, 0.1))


def _query_nominatim(
    query: str,
    *,
    country_code: str = "",
    cancel_check: Callable[[], None] | None = None,
) -> list[HeadquartersResult]:
    parameters = {
        "format": "jsonv2",
        "q": query,
        "addressdetails": "1",
        "limit": "5",
        "dedupe": "1",
    }
    if country_code:
        parameters["countrycodes"] = country_code.lower()
    url = f"{_nominatim_base_url()}/search?{urllib.parse.urlencode(parameters)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": NOMINATIM_USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with _nominatim_lock:
            _wait_for_nominatim(cancel_check)
            with urllib.request.urlopen(request, timeout=MAPS_TIMEOUT_SECONDS) as response:
                document = _read_json(response, "openstreetmap")
    except urllib.error.HTTPError as exc:
        raise _http_error("openstreetmap", exc.code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MapsProviderError(
            "openstreetmap",
            "network",
            "OpenStreetMap Nominatim could not be reached. Check the network connection and try again.",
        ) from exc

    results = []
    if not isinstance(document, list):
        return results
    for place in document:
        if not isinstance(place, dict):
            continue
        address = str(place.get("display_name") or "").strip()
        address_details = place.get("address") if isinstance(place.get("address"), dict) else {}
        if address:
            results.append(
                HeadquartersResult(
                    address=address[:250],
                    source="openstreetmap",
                    attribution=OPENSTREETMAP_ATTRIBUTION,
                    country_code=str(address_details.get("country_code") or "").strip().upper(),
                )
            )
    return results


def _select_result(results: list[HeadquartersResult], require_us: bool) -> HeadquartersResult | None:
    for result in results:
        is_us = result.country_code == "US" or _looks_like_us_address(result.address)
        if not require_us or is_us:
            return result
    return None


def lookup_headquarters(
    settings: MapsProviderSettings,
    company_name: str,
    *,
    prefer_us: bool = True,
    cancel_check: Callable[[], None] | None = None,
) -> HeadquartersResult:
    """Resolve a headquarters through the selected maps provider."""
    provider = normalize_maps_provider(settings.provider)
    normalized = MapsProviderSettings(provider, settings.api_key.strip())
    query = _headquarters_query(company_name, prefer_us)
    if provider == "openstreetmap":
        first_results = _query_nominatim(
            query,
            country_code="us" if prefer_us else "",
            cancel_check=cancel_check,
        )
    else:
        first_results = _query_google(normalized, query)
    selected = _select_result(first_results, require_us=prefer_us)
    if selected:
        return selected
    if prefer_us:
        global_query = _headquarters_query(company_name, False)
        if provider == "openstreetmap":
            fallback_results = _query_nominatim(global_query, cancel_check=cancel_check)
        else:
            fallback_results = _query_google(normalized, global_query)
        selected = _select_result(fallback_results, require_us=False)
        if selected:
            return selected
    return HeadquartersResult()


def resolve_headquarters(
    maps_settings: MapsProviderSettings,
    ai_settings: AIProviderSettings,
    company_name: str,
    *,
    prefer_us: bool = True,
    job_location: str = "",
    job_url: str = "",
    cancel_check: Callable[[], None] | None = None,
) -> HeadquartersResult:
    """Use the selected maps provider, then a clearly labeled AI fallback."""
    provider_warning = ""
    try:
        result = lookup_headquarters(
            maps_settings,
            company_name,
            prefer_us=prefer_us,
            cancel_check=cancel_check,
        )
        if result.address != "Unknown":
            return result
    except MapsProviderError as exc:
        provider_warning = str(exc)

    if cancel_check:
        cancel_check()
    if not ai_settings.api_key:
        return HeadquartersResult(warning=provider_warning)
    scope = (
        "the employer's U.S. headquarters when it has one, otherwise its primary global headquarters"
        if prefer_us
        else "the employer's primary global headquarters"
    )
    prompt = f"""
Identify {scope} for the exact employer {company_name!r}.
Job location context: {job_location or 'not provided'}
Job posting URL context: {job_url or 'not provided'}
Do not substitute a similarly named employer. Return a full postal address and
ISO two-letter country code only when confidently verified. Otherwise set
address to Unknown, country_code to an empty string, and verified to false.
""".strip()
    try:
        response = generate_structured(ai_settings, prompt, HeadquartersAIResponse)
    except AIProviderError:
        return HeadquartersResult(warning=provider_warning)
    address = str(response.get("address") or "").strip()
    if not response.get("verified") or not address or address.lower() == "unknown":
        return HeadquartersResult(warning=provider_warning)
    ai_warning = "AI-assisted headquarters result; verify the address before filing official records."
    if provider_warning:
        ai_warning = f"{provider_warning} {ai_warning}"
    return HeadquartersResult(
        address=address[:250],
        source=f"ai_{ai_settings.provider}",
        attribution="AI-assisted; verify before filing",
        country_code=str(response.get("country_code") or "").strip().upper(),
        warning=ai_warning,
    )


def validate_maps_provider(settings: MapsProviderSettings, cancel_check: Callable[[], None] | None = None) -> dict:
    """Run a small user-triggered lookup to verify provider configuration."""
    provider = normalize_maps_provider(settings.provider)
    if provider == "openstreetmap":
        results = _query_nominatim("United States", country_code="us", cancel_check=cancel_check)
        attribution = OPENSTREETMAP_ATTRIBUTION
    else:
        results = _query_google(MapsProviderSettings(provider, settings.api_key), "Googleplex United States")
        attribution = "Google Maps"
    if not results:
        raise MapsProviderError(provider, "capability", f"{maps_provider_label(provider)} did not return a test result.")
    return {
        "success": True,
        "provider": provider,
        "provider_label": maps_provider_label(provider),
        "message": f"{maps_provider_label(provider)} is ready for headquarters lookups.",
        "attribution": attribution,
    }
