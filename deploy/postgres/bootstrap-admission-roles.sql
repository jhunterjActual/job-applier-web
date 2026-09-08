-- Explicit administrator-only prerequisite. No credential installation or signup activation.
DO $$
DECLARE name text; r record;
BEGIN
    FOREACH name IN ARRAY ARRAY['careertrellis_admission_owner', 'careertrellis_admission_broker'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS', name);
        END IF;
        SELECT * INTO STRICT r FROM pg_roles WHERE rolname=name;
        IF r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
           OR r.rolinherit OR r.rolreplication OR r.rolbypassrls THEN
            RAISE EXCEPTION 'admission bootstrap role contract failed';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) THEN
            RAISE EXCEPTION 'admission roles must not inherit other roles';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.roleid=r.oid AND
            (name <> 'careertrellis_admission_owner' OR m.member <> 'careertrellis_migration'::regrole
             OR m.inherit_option OR NOT m.set_option OR m.admin_option)) THEN
            RAISE EXCEPTION 'unexpected admission role member';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) a
                   WHERE d.defaclrole=r.oid AND a.grantee<>r.oid) THEN
            RAISE EXCEPTION 'unexpected admission default privileges';
        END IF;
    END LOOP;
END $$;
GRANT careertrellis_admission_owner TO careertrellis_migration WITH INHERIT FALSE, SET TRUE;
CREATE SCHEMA IF NOT EXISTS careertrellis_admission AUTHORIZATION careertrellis_admission_owner;
CREATE SCHEMA IF NOT EXISTS careertrellis_lifecycle AUTHORIZATION careertrellis_schema_owner;
DO $$ BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname='careertrellis_admission')
          <> 'careertrellis_admission_owner'::regrole
       OR (SELECT nspowner FROM pg_namespace WHERE nspname='careertrellis_lifecycle')
          <> 'careertrellis_schema_owner'::regrole THEN
        RAISE EXCEPTION 'admission bootstrap schema ownership failed';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace n, LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
        WHERE n.nspname IN ('careertrellis_admission','careertrellis_lifecycle') AND a.grantee<>n.nspowner
        AND NOT (n.nspname='careertrellis_admission' AND a.privilege_type='USAGE' AND NOT a.is_grantable
                 AND a.grantee IN ('careertrellis_schema_owner'::regrole,'careertrellis_admission_broker'::regrole))) THEN
        RAISE EXCEPTION 'unexpected admission schema privileges';
    END IF;
END $$;
REVOKE ALL ON SCHEMA careertrellis_admission, careertrellis_lifecycle FROM PUBLIC;
