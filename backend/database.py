import sqlite3
from datetime import datetime
from config import DB_PATH

def get_db_connection() -> sqlite3.Connection:
    """
    Establishes and returns a connection to the local SQLite database.
    Sets the row factory to sqlite3.Row to allow key-based column lookups.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_column_if_missing(cursor: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    """Add a column during startup migration when an older database lacks it."""
    columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init_db() -> None:
    """
    Initializes the SQLite database tables (profile, jobs, applications)
    if they do not already exist, and performs schema migrations
    and automatic cleanup of expired job listings on startup.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # User Profile table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS profile (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT,
        phone TEXT,
        github TEXT,
        linkedin TEXT,
        website TEXT,
        base_resume_text TEXT,
        gemini_api_key TEXT,
        openai_api_key TEXT DEFAULT '',
        ai_provider TEXT NOT NULL DEFAULT 'gemini',
        ai_model TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
        maps_provider TEXT NOT NULL DEFAULT 'openstreetmap',
        prefer_us_headquarters INTEGER NOT NULL DEFAULT 1,
        interface_language TEXT NOT NULL DEFAULT 'en',
        active_base_resume_id INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS base_resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        resume_mode TEXT NOT NULL DEFAULT 'general_professional',
        content TEXT NOT NULL DEFAULT '',
        evidence_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS base_resume_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        base_resume_id INTEGER NOT NULL,
        version_number INTEGER NOT NULL,
        name TEXT NOT NULL,
        resume_mode TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        evidence_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        UNIQUE(base_resume_id, version_number),
        FOREIGN KEY (base_resume_id) REFERENCES base_resumes(id) ON DELETE CASCADE
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_base_resume_versions_resume ON base_resume_versions(base_resume_id, version_number DESC)"
    )
    
    # Jobs table (postings found/scraped)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        company TEXT,
        description TEXT,
        url TEXT UNIQUE,
        match_score INTEGER,
        match_analysis TEXT,
        date_found TEXT,
        status TEXT DEFAULT 'matched', -- matched, tailored, form_filled, submitted, applied, outcomes
        archived_at TEXT,
        archived_from_status TEXT,
        last_checked_at TEXT,
        is_expired INTEGER DEFAULT 0,
        expiration_reason TEXT
        ,location TEXT
        ,work_arrangement TEXT
        ,employment_type TEXT
        ,compensation TEXT
        ,source TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS saved_searches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        keywords TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_run_at TEXT,
        schedule_frequency TEXT NOT NULL DEFAULT 'none',
        next_alert_at TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        UNIQUE(keywords, location)
    )
    """)
    
    # Applications table (jobs applied to)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        company TEXT,
        position TEXT,
        date_applied TEXT,
        us_hq TEXT,
        tailored_resume_path TEXT,
        cover_letter_path TEXT,
        tailored_resume_text TEXT,
        cover_letter TEXT,
        status TEXT DEFAULT 'tailored',
        created_at TEXT,
        tailored_at TEXT,
        form_filled_at TEXT,
        submitted_at TEXT,
        confirmed_at TEXT,
        application_method TEXT,
        submission_evidence TEXT,
        notes TEXT,
        follow_up_date TEXT,
        headquarters_source TEXT,
        headquarters_attribution TEXT,
        base_resume_id INTEGER,
        base_resume_name TEXT,
        base_resume_version INTEGER,
        interview_prep TEXT,
        interview_prep_updated_at TEXT,
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS application_status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        from_status TEXT NOT NULL,
        to_status TEXT NOT NULL,
        changed_at TEXT NOT NULL,
        source TEXT NOT NULL,
        notes TEXT,
        undone_at TEXT,
        FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS application_engagements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        engagement_type TEXT NOT NULL,
        name TEXT NOT NULL,
        organization TEXT NOT NULL DEFAULT '',
        contact_details TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'planned',
        activity_on TEXT,
        next_action_on TEXT,
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_engagements_job ON application_engagements(job_id, updated_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_engagements_next_action ON application_engagements(next_action_on)"
    )

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS job_suppressions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url_fingerprint TEXT NOT NULL UNIQUE,
        hostname TEXT NOT NULL,
        company TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        deleted_at TEXT NOT NULL,
        deletion_source TEXT NOT NULL
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_suppressions_deleted_at ON job_suppressions(deleted_at DESC)"
    )

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS source_diagnostics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recorded_at TEXT NOT NULL,
        provider TEXT NOT NULL,
        diagnostic_code TEXT NOT NULL,
        counters_json TEXT NOT NULL
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_diagnostics_recorded_at ON source_diagnostics(recorded_at DESC)"
    )

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS headquarters_cache (
        cache_key TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        address TEXT NOT NULL,
        country_code TEXT NOT NULL DEFAULT '',
        attribution TEXT NOT NULL DEFAULT '',
        resolved_at TEXT NOT NULL
    )
    """)
    
    # Check and add suggested_keywords column if it doesn't exist
    _add_column_if_missing(cursor, "profile", "base_resume_text", "TEXT DEFAULT ''")
    _add_column_if_missing(cursor, "profile", "suggested_keywords", "TEXT DEFAULT ''")
    _add_column_if_missing(cursor, "profile", "google_maps_api_key", "TEXT DEFAULT ''")
    _add_column_if_missing(cursor, "profile", "openai_api_key", "TEXT DEFAULT ''")
    _add_column_if_missing(cursor, "profile", "ai_provider", "TEXT NOT NULL DEFAULT 'gemini'")
    _add_column_if_missing(cursor, "profile", "ai_model", "TEXT NOT NULL DEFAULT 'gemini-2.5-flash'")
    _add_column_if_missing(cursor, "profile", "maps_provider", "TEXT NOT NULL DEFAULT 'google'")
    _add_column_if_missing(cursor, "profile", "resume_mode", "TEXT DEFAULT 'general_professional'")
    _add_column_if_missing(cursor, "profile", "prefer_us_headquarters", "INTEGER NOT NULL DEFAULT 1")
    _add_column_if_missing(cursor, "profile", "interface_language", "TEXT NOT NULL DEFAULT 'en'")
    _add_column_if_missing(cursor, "profile", "active_base_resume_id", "INTEGER")
    _add_column_if_missing(cursor, "base_resumes", "evidence_json", "TEXT NOT NULL DEFAULT '{}'")
    _add_column_if_missing(cursor, "base_resume_versions", "evidence_json", "TEXT NOT NULL DEFAULT '{}'")
    _add_column_if_missing(cursor, "jobs", "archived_at", "TEXT")
    _add_column_if_missing(cursor, "jobs", "archived_from_status", "TEXT")
    _add_column_if_missing(cursor, "jobs", "last_checked_at", "TEXT")
    _add_column_if_missing(cursor, "jobs", "is_expired", "INTEGER DEFAULT 0")
    _add_column_if_missing(cursor, "jobs", "expiration_reason", "TEXT")
    _add_column_if_missing(cursor, "jobs", "location", "TEXT")
    _add_column_if_missing(cursor, "jobs", "work_arrangement", "TEXT")
    _add_column_if_missing(cursor, "jobs", "employment_type", "TEXT")
    _add_column_if_missing(cursor, "jobs", "compensation", "TEXT")
    _add_column_if_missing(cursor, "jobs", "source", "TEXT")

    # Lifecycle migration. Keep date_applied for backwards compatibility while
    # recording each transition independently for all new activity.
    _add_column_if_missing(cursor, "applications", "created_at", "TEXT")
    _add_column_if_missing(cursor, "applications", "tailored_at", "TEXT")
    _add_column_if_missing(cursor, "applications", "form_filled_at", "TEXT")
    _add_column_if_missing(cursor, "applications", "submitted_at", "TEXT")
    _add_column_if_missing(cursor, "applications", "confirmed_at", "TEXT")
    _add_column_if_missing(cursor, "applications", "application_method", "TEXT")
    _add_column_if_missing(cursor, "applications", "submission_evidence", "TEXT")
    _add_column_if_missing(cursor, "applications", "notes", "TEXT")
    _add_column_if_missing(cursor, "applications", "follow_up_date", "TEXT")
    _add_column_if_missing(cursor, "applications", "tailored_resume_text", "TEXT")
    _add_column_if_missing(cursor, "applications", "cover_letter_path", "TEXT")
    _add_column_if_missing(cursor, "applications", "headquarters_source", "TEXT")
    _add_column_if_missing(cursor, "applications", "headquarters_attribution", "TEXT")
    _add_column_if_missing(cursor, "applications", "base_resume_id", "INTEGER")
    _add_column_if_missing(cursor, "applications", "base_resume_name", "TEXT")
    _add_column_if_missing(cursor, "applications", "base_resume_version", "INTEGER")
    _add_column_if_missing(cursor, "applications", "interview_prep", "TEXT")
    _add_column_if_missing(cursor, "applications", "interview_prep_updated_at", "TEXT")
    _add_column_if_missing(cursor, "saved_searches", "schedule_frequency", "TEXT DEFAULT 'none'")
    _add_column_if_missing(cursor, "saved_searches", "next_alert_at", "TEXT")

    cursor.execute("""
    UPDATE jobs
    SET source = CASE
        WHEN url LIKE '%greenhouse.io/%' THEN 'greenhouse'
        WHEN url LIKE '%jobs.lever.co/%' THEN 'lever'
        WHEN url LIKE '%jobs.ashbyhq.com/%' THEN 'ashby'
        WHEN url LIKE '%jobs.smartrecruiters.com/%' THEN 'smartrecruiters'
        ELSE COALESCE(source, 'unknown')
    END
    WHERE source IS NULL OR source = ''
    """)

    cursor.execute("""
    UPDATE applications
    SET created_at = COALESCE(created_at, date_applied),
        tailored_at = CASE WHEN status = 'tailored' THEN COALESCE(tailored_at, date_applied) ELSE tailored_at END,
        submitted_at = CASE WHEN status = 'applied' THEN COALESCE(submitted_at, date_applied) ELSE submitted_at END,
        confirmed_at = CASE WHEN status = 'applied' THEN COALESCE(confirmed_at, date_applied) ELSE confirmed_at END,
        application_method = CASE WHEN status = 'applied' THEN COALESCE(application_method, 'legacy') ELSE application_method END,
        date_applied = CASE WHEN status = 'tailored' THEN NULL ELSE date_applied END
    """)

    # One lifecycle record per job. Do not discard legacy data if an old
    # database already contains duplicates; surface that condition instead.
    duplicate_count = cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT job_id FROM applications GROUP BY job_id HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    if duplicate_count == 0:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_job_id ON applications(job_id)")
        
    # Insert default empty profile if none exists
    cursor.execute("SELECT COUNT(*) FROM profile")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
        INSERT INTO profile (
            name, email, phone, github, linkedin, website, base_resume_text,
            gemini_api_key, openai_api_key, ai_provider, ai_model,
            maps_provider, suggested_keywords, google_maps_api_key
        )
        VALUES ('', '', '', '', '', '', '', '', '', 'gemini', 'gemini-2.5-flash', 'openstreetmap', '', '')
        """)

    # Preserve an existing user's resume by migrating it into a named, versioned
    # record. Keep the legacy profile fields synchronized for compatibility with
    # current search and tailoring code.
    profile = cursor.execute(
        "SELECT active_base_resume_id, base_resume_text, resume_mode FROM profile WHERE id = 1"
    ).fetchone()
    if profile and profile[0] is None and (profile[1] or "").strip():
        now = datetime.now().isoformat(timespec="seconds")
        resume_mode = profile[2] or "general_professional"
        resume_cursor = cursor.execute(
            """
            INSERT INTO base_resumes (name, resume_mode, content, created_at, updated_at)
            VALUES ('Primary Resume', ?, ?, ?, ?)
            """,
            (resume_mode, profile[1], now, now),
        )
        resume_id = resume_cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO base_resume_versions (
                base_resume_id, version_number, name, resume_mode, content, created_at
            ) VALUES (?, 1, 'Primary Resume', ?, ?, ?)
            """,
            (resume_id, resume_mode, profile[1], now),
        )
        cursor.execute(
            "UPDATE profile SET active_base_resume_id = ? WHERE id = 1",
            (resume_id,),
        )
    # Clean up any previously stored junk/closed postings
    cursor.execute("""
    DELETE FROM jobs 
    WHERE title IS NULL 
       OR title = '' 
       OR title = 'Job not found' 
       OR title LIKE '%no longer active%'
       OR title LIKE '%no longer available%'
       OR title LIKE '%position closed%'
       OR title LIKE '%job is closed%'
       OR title LIKE 'jobs at%'
       OR title LIKE '%Internet Explorer 11%'
       OR title LIKE '%Consent to Cookies%'
    """)
    
    conn.commit()
    conn.close()

# Initialize DB on import
init_db()
