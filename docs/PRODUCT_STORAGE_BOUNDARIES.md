# Product storage boundaries

CareerTrellis supports a standalone local workspace and a shared-host service.
The storage, credential and recovery contracts differ. This guide describes the
repository's supported modes; it does not attest to the current configuration,
version or health of a deployed service.

## Where records and generated files live

| Data | Standalone local installation | Shared-host installation |
|---|---|---|
| Profile, base resumes, saved jobs, applications and notes | SQLite in the installation's private data directory | Owner-scoped PostgreSQL repositories using tenant context and row-level security |
| Saved AI and maps credentials | Local SQLite workspace; protect access to the database and backups | Encrypted provider-secret records in PostgreSQL, with encryption keys managed separately by the operator |
| Generated application materials | Files in the local output directory | Private encrypted objects, with owner-scoped metadata in PostgreSQL and downloads authorized through the application |
| Restorable backups | Password-protected encrypted full-workspace archive | Operator-managed PostgreSQL backups plus private-object recovery copies; the local archive feature is unavailable |

Local defaults are `data/jobapplier.db` and `data/output/`. An operator can choose
a different data root. Git ignore rules keep these files out of ordinary source
control; they do not encrypt them or make a copied database safe to share.

Shared mode requires explicit store selection and validated configuration. It
does not silently fall back to the standalone database when a hosted store is
unavailable. The browser receives authorized downloads through the application,
not object-store credentials or direct object URLs.

## Inputs sent to external providers

Local storage does not mean every operation runs offline. AI-assisted matching,
tailoring and explicitly requested interview drafts send their required resume,
candidate-evidence and job context to the selected AI provider. Discovery, posting
retrieval and maps lookups contact their configured external services.

Text-based document extraction runs in the application process. Scanned-page OCR
requires the explicit import opt-in; only pages without readable embedded text
are sent as images to the selected AI provider. Review the transcription before
saving it. In hosted mode, the application process runs on the service host.

Saved provider credentials are used server-side and are not returned to the
browser. Hosted encryption protects stored credentials and objects, while the
running service must still decrypt authorized content to use it.

## Export and recovery

**Export My Data** creates a human-readable data export. It excludes stored API
keys and internal generated-file paths and is not a complete restorable backup.
It still contains personal information and should remain private.

**Encrypted Full Backup** and **Restore Full Backup** apply to standalone SQLite
workspaces. The archive includes saved credentials and generated materials.
The user keeps its password separately. Local restore replaces the workspace
after validation and retains the preceding copy for recovery; that retained copy
has the same privacy needs as the original local database.

Shared-host backup and restore endpoints fail closed. Hosted recovery requires
operator-managed database and object-store evidence and separately authorized
restore procedures. A successful database backup alone does not establish that
all generated objects are recoverable.

## Implementation and operational references

- [Data-store selection](../backend/data_store.py) and [profile repositories](../backend/profile_repository.py).
- [Provider-secret storage](../backend/provider_secrets.py), [object encryption and access](../backend/object_store.py), and [authorized object reads](../backend/stored_objects.py).
- [Runtime paths and configuration](../backend/config.py).
- [Shared backup and restore policy](STAGE_3_SHARED_BACKUP_RESTORE_POLICY.md) and [generated-object recovery contract](STAGE_3_GENERATED_OBJECT_RECOVERY.md).

Stage documents retain their historical acceptance and activation boundaries.
Use fresh exact-release evidence and the current runbook before a live change;
do not treat an old preparation statement as the deployed service's current state.
