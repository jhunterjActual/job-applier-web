import os
import re
import html
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf
from config import get_gemini_api_key, get_google_maps_api_key
from ai_providers import AIProviderSettings
from maps_providers import (
    MapsProviderSettings,
    _headquarters_query,
    _looks_like_us_address,
    resolve_headquarters,
)


_PHONE_TOKEN = r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"
_MARKDOWN_LINK_TOKEN = r"\[[^\]\n]+\]\(https?://[^\s)]+\)"
_URL_TOKEN = r"(?:https?://|www\.|(?:linkedin|github)\.com/)[^\s<>()]+"
_EMAIL_TOKEN = r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
_CONTACT_TOKEN = re.compile(
    rf"({_MARKDOWN_LINK_TOKEN}|{_URL_TOKEN}|{_EMAIL_TOKEN}|{_PHONE_TOKEN})",
    re.IGNORECASE,
)
_INLINE_TOKEN = re.compile(
    rf"(\*\*[^*\n]+\*\*|(?<!\*)\*[^*\n]+\*(?!\*)|(?<!_)_[^_\n]+_(?!_)|"
    rf"{_MARKDOWN_LINK_TOKEN}|{_URL_TOKEN}|{_EMAIL_TOKEN}|{_PHONE_TOKEN})",
    re.IGNORECASE,
)


def _contact_link(token: str) -> str:
    """Render a safe visible contact token as an external PDF hyperlink."""
    markdown_link = re.fullmatch(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", token, re.IGNORECASE)
    if markdown_link:
        label, target = markdown_link.groups()
        return (
            f'<a href="{html.escape(target, quote=True)}">'
            f"{html.escape(label, quote=True)}</a>"
        )

    if re.fullmatch(_EMAIL_TOKEN, token, re.IGNORECASE):
        return (
            f'<a href="mailto:{html.escape(token, quote=True)}">'
            f"{html.escape(token, quote=True)}</a>"
        )
    if re.fullmatch(_PHONE_TOKEN, token):
        prefix = "+" if token.strip().startswith("+") else ""
        digits = re.sub(r"[^0-9]", "", token)
        return (
            f'<a href="tel:{prefix}{digits}">'
            f"{html.escape(token, quote=True)}</a>"
        )

    visible = token.rstrip(".,;:")
    trailing = token[len(visible):]
    target = visible if visible.lower().startswith(("http://", "https://")) else f"https://{visible}"
    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return html.escape(token, quote=True)
    return (
        f'<a href="{html.escape(target, quote=True)}">'
        f"{html.escape(visible, quote=True)}</a>{html.escape(trailing, quote=True)}"
    )


def _linkify_contacts(text: str) -> str:
    output: list[str] = []
    cursor = 0
    for match in _CONTACT_TOKEN.finditer(text):
        output.append(html.escape(text[cursor:match.start()], quote=True))
        output.append(_contact_link(match.group(0)))
        cursor = match.end()
    output.append(html.escape(text[cursor:], quote=True))
    return "".join(output)


def _inline_markdown(text: str) -> str:
    """Render safe inline Markdown plus visible, clickable contact details."""
    source = text.strip()
    output: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN.finditer(source):
        output.append(html.escape(source[cursor:match.start()], quote=True))
        token = match.group(0)
        if token.startswith("**"):
            output.append(f"<strong>{_linkify_contacts(token[2:-2])}</strong>")
        elif token.startswith("*") or (token.startswith("_") and token.endswith("_")):
            output.append(f"<em>{_linkify_contacts(token[1:-1])}</em>")
        else:
            output.append(_contact_link(token))
        cursor = match.end()
    output.append(html.escape(source[cursor:], quote=True))
    return "".join(output)


def resume_pdf_metadata(candidate_name: str, job_title: str, company: str) -> dict[str, str]:
    """Build bounded, recruiter-readable metadata for a job-specific resume PDF."""
    def clean(value: str, limit: int = 240) -> str:
        normalized = re.sub(r"[\x00-\x1f\x7f]+", " ", value or "")
        return " ".join(normalized.split())[:limit]

    candidate = clean(candidate_name) or "Candidate"
    position = clean(job_title) or "Target Role"
    employer = clean(company) or "Target Employer"
    return {
        "title": clean(f"{candidate} - {position} Resume"),
        "author": candidate,
        "subject": clean(f"Resume prepared for {position} at {employer}"),
        "keywords": clean(f"resume, {position}, {employer}"),
        "creator": "CareerTrellis",
    }


def _apply_pdf_metadata(pdf_bytes: bytes, metadata: dict[str, str]) -> tuple[bytes, int]:
    """Add standard document properties without discarding Chromium's tagged structure."""
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_count = document.page_count
        existing = document.metadata or {}
        allowed = (
            "title", "author", "subject", "keywords", "creator", "producer",
            "creationDate", "modDate", "trapped",
        )
        updated = {key: str(existing.get(key) or "") for key in allowed}
        for key in ("title", "author", "subject", "keywords", "creator"):
            if metadata.get(key):
                updated[key] = metadata[key]
        document.set_metadata(updated)
        return document.tobytes(garbage=0, deflate=True), page_count
    finally:
        document.close()


def markdown_to_html(md_text: str) -> str:
    """
    Converts basic Markdown formatting (headers, bold text, lists, paragraphs)
    into standard semantic HTML strings for PDF rendering.
    
    Args:
        md_text (str): The raw Markdown text to format.
        
    Returns:
        str: The generated HTML body string.
    """
    source = (md_text or "").replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
    output: list[str] = []
    paragraph: list[str] = []
    in_list = False
    in_section = False
    in_entry = False

    def flush_paragraph() -> None:
        if paragraph:
            output.append(f"<p>{' '.join(_inline_markdown(line) for line in paragraph)}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    def close_entry() -> None:
        nonlocal in_entry
        if in_entry:
            output.append("</div>")
            in_entry = False

    for raw_line in source.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            flush_paragraph()
            close_list()
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            if level == 2:
                close_entry()
                if in_section:
                    output.append("</section>")
                output.append('<section class="resume-section">')
                in_section = True
            elif level == 3:
                close_entry()
                output.append('<div class="resume-entry">')
                in_entry = True
            output.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            continue

        if re.fullmatch(r"[-*_]{3,}", stripped):
            flush_paragraph()
            close_list()
            output.append("<hr>")
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{_inline_markdown(bullet.group(1))}</li>")
            continue

        close_list()
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    close_entry()
    if in_section:
        output.append("</section>")
    return "\n".join(output)

class PdfPageLimitError(ValueError):
    """The complete document cannot fit the selected page policy."""


def generate_pdf_from_html(
    html_content: str,
    output_pdf_path: str,
    is_resume: bool = True,
    max_pages: int = 2,
    metadata: dict[str, str] | None = None,
) -> dict:
    """
    Compiles an HTML body string with custom styles and prints it to a PDF
    on disk using Playwright's headless PDF printing capabilities.
    
    Args:
        html_content (str): The semantic HTML content to print.
        output_pdf_path (str): The local file path to write the PDF.
        is_resume (bool, optional): Whether this is a resume. Defaults to True.
    """
    # CSS template
    style = """
    <style>
        @page {
            size: Letter;
            margin: 0.58in 0.65in;
        }
        body {
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
            color: #333333;
            line-height: 1.3;
            margin: 0;
            padding: 0;
            font-size: 10.5pt;
        }
        h1 {
            font-size: 20pt;
            color: #1a365d;
            margin-top: 0;
            margin-bottom: 6pt;
            border-bottom: 2px solid #2b6cb0;
            padding-bottom: 5pt;
            text-transform: uppercase;
            letter-spacing: 0.6pt;
        }
        h2 {
            font-size: 13.5pt;
            color: #2b6cb0;
            margin-top: 12pt;
            margin-bottom: 6pt;
            border-bottom: 1px solid #e2e8f0;
            padding-bottom: 2pt;
            break-after: avoid;
        }
        h3 {
            font-size: 11pt;
            color: #4a5568;
            margin-top: 8pt;
            margin-bottom: 3pt;
            break-after: avoid;
        }
        p {
            margin-top: 0;
            margin-bottom: 5pt;
            orphans: 2;
            widows: 2;
        }
        ul {
            margin-top: 0;
            margin-bottom: 6pt;
            padding-left: 16pt;
        }
        li {
            margin-bottom: 3pt;
            break-inside: avoid;
            orphans: 2;
            widows: 2;
        }
        strong {
            color: #2d3748;
        }
        a {
            color: #1a365d;
            text-decoration: underline;
            text-decoration-thickness: 0.5pt;
            text-underline-offset: 1.5pt;
        }
        hr { display: none; }
        .resume-section { break-inside: auto; }
        .resume-entry { break-inside: avoid; }
        body.compact { font-size: 10pt; line-height: 1.22; }
        body.compact h1 { font-size: 18pt; margin-bottom: 4pt; padding-bottom: 3pt; }
        body.compact h2 { font-size: 12.5pt; margin-top: 8pt; margin-bottom: 4pt; }
        body.compact h3 { font-size: 10.5pt; margin-top: 5pt; margin-bottom: 2pt; }
        body.compact p, body.compact li { margin-bottom: 2pt; }
        body.compact ul { margin-bottom: 3pt; }
    </style>
    """
    
    document_metadata = metadata or {}
    default_title = "CareerTrellis Resume" if is_resume else "CareerTrellis Cover Letter"
    full_html = f"""
    <!DOCTYPE html>
    <html lang="en-US">
    <head>
        <meta charset="utf-8">
        <title>{html.escape(document_metadata.get('title', default_title), quote=True)}</title>
        <meta name="author" content="{html.escape(document_metadata.get('author', ''), quote=True)}">
        <meta name="description" content="{html.escape(document_metadata.get('subject', ''), quote=True)}">
        <meta name="keywords" content="{html.escape(document_metadata.get('keywords', ''), quote=True)}">
        {style}
    </head>
    <body class="{'resume' if is_resume else 'cover-letter'}">
        {html_content}
    </body>
    </html>
    """
    
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 701, "height": 945})
        page.set_content(full_html, wait_until="load")
        try:
            for compact in (False, True):
                if compact:
                    page.evaluate("document.body.classList.add('compact')")
                pdf_bytes = page.pdf(
                    format="Letter",
                    print_background=True,
                    prefer_css_page_size=True,
                    tagged=True,
                    outline=True,
                )
                pdf_bytes, page_count = _apply_pdf_metadata(pdf_bytes, document_metadata)
                if page_count <= max_pages:
                    output_path = Path(output_pdf_path)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(pdf_bytes)
                    return {"page_count": page_count, "compact": compact}
            raise PdfPageLimitError(
                f"Generated document exceeds the {max_pages}-page limit after compact formatting. "
                "Shorten the source content and regenerate it."
            )
        finally:
            browser.close()

def generate_resume_pdf(
    markdown_text: str,
    output_pdf_path: str,
    max_pages: int = 2,
    *,
    metadata: dict[str, str] | None = None,
) -> dict:
    """
    Converts a Markdown tailored resume to HTML and compiles it to a PDF.
    
    Args:
        markdown_text (str): The raw Markdown tailored resume.
        output_pdf_path (str): The path to save the generated PDF.
    """
    html_body = markdown_to_html(markdown_text)
    
    # We can perform some custom structural cleanup if needed, but a clean markdown conversion is usually enough
    return generate_pdf_from_html(
        html_body,
        output_pdf_path,
        is_resume=True,
        max_pages=max_pages,
        metadata=metadata,
    )

def generate_cover_letter_pdf(cover_letter_text: str, output_pdf_path: str) -> dict:
    """
    Converts a plain text cover letter into a styled HTML and prints to PDF.
    
    Args:
        cover_letter_text (str): The plain text cover letter content.
        output_pdf_path (str): The path to save the generated PDF.
    """
    # Convert newlines to paragraphs/breaks
    formatted_body = html.escape(cover_letter_text).replace('\n', '<br/>')
    html_body = f"<div class='cover-letter'>{formatted_body}</div>"
    
    return generate_pdf_from_html(html_body, output_pdf_path, is_resume=False, max_pages=1)

def find_us_headquarters(
    company_name: str,
    api_key: str = None,
    google_maps_key: str = None,
    *,
    prefer_us: bool = True,
    job_location: str = "",
    job_url: str = "",
) -> str:
    """Backward-compatible wrapper around the Google maps adapter."""
    result = resolve_headquarters(
        MapsProviderSettings("google", google_maps_key or get_google_maps_api_key()),
        AIProviderSettings("gemini", "gemini-2.5-flash", api_key or get_gemini_api_key()),
        company_name,
        prefer_us=prefer_us,
        job_location=job_location,
        job_url=job_url,
    )
    return result.address
