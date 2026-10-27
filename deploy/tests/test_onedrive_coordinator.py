import datetime as dt
import importlib
from pathlib import Path
import sys
import unittest
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
c = importlib.import_module("deploy.onedrive_retention.coordinator")
e = importlib.import_module("deploy.onedrive_retention.endpoint")


def policy():
    return c.Policy({"schema": "careertrellis.onedrive-retention-policy/v1", "enabled": True,
        "application_release": c.RELEASE, "valid_until": "2027-01-24T00:00:00Z", "reserve_percent": 20,
        "peak_multiplier": 4, "namespace_budget_bytes": 512*1024**2, "keep_verified": 2,
        "grace_seconds": 172800, "schedule_hour_utc": 12, "schedule_minute_utc": 30,
        "artifact_revision": "a"*40, "paired_policy_sha256": "b"*64})


def response(request):
    stage = request["payload"]["stage"]
    facts = {"archive_sha256": "a"*64, "archive_bytes": 12345,
             "generation_sha256": "b"*64, "backup_label": "20261026-155056F"}
    if stage in ("publish", "restore"): facts["native_restore_accepted"] = stage == "restore"
    if stage == "retire": facts = {"catalog_sha256": "f"*64, "retired_count": 0}
    return {"schema": e.RESPONSE, "capture_transaction": request["capture_transaction"],
        "retention_policy_sha256": request["retention_policy_sha256"], "stage": stage,
        "state": "complete", "receipt_sha256": "c"*64,
        "facts": facts}


class Fake:
    def __init__(self, lost=None): self.events=[]; self.files={}; self.lost=lost
    @contextmanager
    def locked(self): yield
    def now(self): return dt.datetime.now(dt.timezone.utc)
    def preflight(self): self.events.append("preflight")
    def begin(self, *args): self.events.append("begin")
    def once(self, name, value):
        if name in self.files: raise RuntimeError("existing")
        self.files[name]=value
    def call(self, request):
        stage=request["payload"]["stage"]; self.events.append(stage)
        if stage==self.lost: raise RuntimeError("lost acknowledgement")
        return response(request)
    def web_preflight(self):
        self.events.append("fresh_web")
        return {"schema":"careertrellis.onedrive-restore-web-preflight/v1", "host":"careertrellis-staging-web-1",
            "authority":"postgres-r2-authoritative", "pending_phase":"activated", "activation_lease_absent":True,
            "services_healthy":True,"release_binding_verified":True,"release":c.RELEASE,"observed_at":self.now().isoformat()}
    def finish(self, state, value): self.events.append(state)


class Tests(unittest.TestCase):
    def test_pipeline_proof_is_fresh_after_download_and_before_restore(self):
        fake=Fake(); c.run("d"*32,policy(),fake)
        self.assertEqual(fake.events,["preflight","begin","hydrate","publish","fresh_web","restore","retire","complete"])
    def test_lost_ack_preserves_intent_and_never_redispatches(self):
        fake=Fake("publish")
        with self.assertRaises(c.Refused): c.run("d"*32,policy(),fake)
        self.assertEqual(fake.events.count("publish"),1)
        self.assertIn("publish-intent.json",fake.files); self.assertNotIn("restore",fake.events)
    def test_policy_weaker_or_bool_counter_refuses(self):
        for key,value in (("reserve_percent",19),("peak_multiplier",3),("keep_verified",1),
                          ("grace_seconds",True),("namespace_budget_bytes",512*1024**2+1)):
            with self.subTest(key=key),self.assertRaises(c.Refused): c.Policy(dict(policy().data,**{key:value}))
    def test_stale_web_proof_refuses(self):
        fake=Fake(); value=fake.web_preflight(); value["observed_at"]="2026-01-01T00:00:00Z"
        with self.assertRaises(c.Refused): c.web_proof(value,fake.now())
    def test_changed_archive_or_simulated_restore_never_reaches_retirement(self):
        for mode in ("archive_drift", "native_false"):
            fake=Fake(); original=fake.call
            def altered(request):
                value=original(request)
                if mode=="archive_drift" and value["stage"]=="publish":value["facts"]["archive_sha256"]="f"*64
                if mode=="native_false" and value["stage"]=="restore":value["facts"]["native_restore_accepted"]=False
                return value
            fake.call=altered
            with self.subTest(mode=mode),self.assertRaises(c.Refused):c.run("d"*32,policy(),fake)
            self.assertNotIn("retire",fake.events)
    def test_disabled_expired_policy_stops_before_begin(self):
        for change in ({"enabled":False},{"valid_until":"2026-01-01T00:00:00Z"}):
            fake=Fake()
            with self.assertRaises(c.Refused):c.run("d"*32,c.Policy(dict(policy().data,**change)),fake)
            self.assertEqual(fake.events,[])
