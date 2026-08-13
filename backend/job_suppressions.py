"""Privacy-minimized records used to keep deleted jobs from reappearing."""

import hashlib
import sqlite3
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from searcher import canonicalize_job_url


def job_url_fingerprint(url: str) -> str:
    """Return a stable, non-reversible identity for a canonical job URL."""
    canonical_url = canonicalize_job_url(url)
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def suppression_values(
    *,
    url: str,
    company: str | None,
    title: str | None,
    deleted_at: str | datetime,
    deletion_source: str,
) -> dict[str, Any]:
    """Return the bounded, privacy-minimized values retained after deletion."""
    canonical_url = canonicalize_job_url(url)
    return {
        "url_fingerprint": job_url_fingerprint(canonical_url),
        "hostname": (urlsplit(canonical_url).hostname or "unknown").lower()[:253],
        "company": str(company or "")[:180],
        "title": str(title or "")[:180],
        "deleted_at": deleted_at,
        "deletion_source": deletion_source,
    }


def record_job_suppression(
    connection: sqlite3.Connection,
    *,
    url: str,
    company: str | None,
    title: str | None,
    deleted_at: str,
    deletion_source: str,
) -> None:
    """Remember a deleted posting without retaining its full URL or description."""
    values = suppression_values(
        url=url,
        company=company,
        title=title,
        deleted_at=deleted_at,
        deletion_source=deletion_source,
    )
    connection.execute(
        """
        INSERT INTO job_suppressions (
            url_fingerprint, hostname, company, title, deleted_at, deletion_source
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(url_fingerprint) DO UPDATE SET
            hostname = excluded.hostname,
            company = excluded.company,
            title = excluded.title,
            deleted_at = excluded.deleted_at,
            deletion_source = excluded.deletion_source
        """,
        (
            values["url_fingerprint"], values["hostname"],
            values["company"], values["title"],
            values["deleted_at"], values["deletion_source"],
        ),
    )


def is_job_suppressed(connection: sqlite3.Connection, url: str) -> bool:
    """Return whether a canonical posting identity was previously deleted."""
    row = connection.execute(
        "SELECT 1 FROM job_suppressions WHERE url_fingerprint = ?",
        (job_url_fingerprint(url),),
    ).fetchone()
    return row is not None


def suppressed_job_fingerprints(connection: sqlite3.Connection) -> set[str]:
    """Load the small identity set once for a search run."""
    return {
        row[0]
        for row in connection.execute("SELECT url_fingerprint FROM job_suppressions").fetchall()
    }
