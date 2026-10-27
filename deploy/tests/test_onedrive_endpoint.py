from contextlib import contextmanager
import importlib
import hashlib
import datetime as dt
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from deploy.tests.test_onedrive_coordinator import policy, response
e=importlib.import_module("deploy.onedrive_retention.endpoint")
c=importlib.import_module("deploy.onedrive_retention.coordinator")


class Fake:
    def __init__(self): self.events=[]; self.existing=False; self.saved=None
    @contextmanager
    def locked(self): yield
    def preflight(self): pass
    def now(self):
        import datetime as dt
        return dt.datetime.now(dt.timezone.utc)
    def intent_exists(self,value): return self.existing
    def require_same_intent(self,value): c.need(self.saved==value)
    def observe(self,value): self.events.append("observe"); return response(value)
    def require_previous_terminal(self,value): self.events.append("terminal")
    def bind_predecessor(self,value): self.events.append("bind")
    def intent(self,value): self.events.append("intent");self.saved=value;self.existing=True
    def dispatch(self,value): self.events.append("dispatch")
    def failed(self,value): self.events.append("failed")


class Tests(unittest.TestCase):
    def test_offline_minimal_request_cannot_cross_native_bridge(self):
        p=policy(); minimal=e.request(p.digest,'d'*32,'hydrate',{'stage':'hydrate','receipt_facts':{}})
        with patch.object(e.NativeDB,'locked',side_effect=AssertionError('lock before peer proof')):
            with self.assertRaises(c.Refused):e.Endpoint(p,e.NativeDB(p)).handle(minimal)
        wrapped={'operation':'bundle_hydrate','transaction':'d'*32,
            'payload':{key:minimal[key] for key in ('retention_policy_sha256','capture_transaction','payload')}}
        with self.assertRaises(c.Refused):e.from_paired_request(wrapped)

    def test_present_history_is_validated_and_saved_inside_exact_request(self):
        from deploy.onedrive_retention import guard
        p=policy(); history={'synthetic':'validator-only'}
        with patch.object(guard,'validate_peer_history') as validate:
            requested=e.request(p.digest,'d'*32,'hydrate',{'stage':'hydrate','receipt_facts':{},'web_history':history})
            e.require_native_peer(requested)
        self.assertEqual(validate.call_count,2)
        self.assertEqual(requested['payload']['web_history'],history)

    def test_status_does_not_acquire_lock_or_mutate(self):
        fake=Fake()
        def forbidden(*args): raise AssertionError('status attempted mutation or lock')
        fake.locked=fake.intent=fake.dispatch=fake.failed=forbidden
        p=policy(); query=e.request(p.digest,'d'*32,'status',{'stage':'hydrate','receipt_facts':{}})
        self.assertEqual(e.Endpoint(p,fake).handle(query),response(query))
        self.assertEqual(fake.events,['observe'])
    def test_intent_precedes_dispatch_and_repeated_call_only_observes(self):
        fake=Fake(); p=policy(); request=e.request(p.digest,"d"*32,"hydrate",{"stage":"hydrate","receipt_facts":{}})
        endpoint=e.Endpoint(p,fake);endpoint.handle(request);endpoint.handle(request)
        self.assertEqual(fake.events,["terminal","bind","intent","dispatch","observe","observe"])
    def test_commands_paths_keys_and_wrong_policy_refuse(self):
        p=policy(); request=e.request(p.digest,"d"*32,"hydrate",{"stage":"hydrate","receipt_facts":{}})
        request["payload"]["command"]="anything"
        with self.assertRaises(c.Refused): e.Endpoint(p,Fake()).handle(request)
        request=e.request("f"*64,"d"*32,"hydrate",{"stage":"hydrate","receipt_facts":{}})
        with self.assertRaises(c.Refused): e.Endpoint(p,Fake()).handle(request)
    def test_fixed_distinct_units_and_native_network_boundary(self):
        plans=[e.command_plan(stage,"d"*32,"/fixed/private") for stage in c.STAGES]
        self.assertEqual(len({plan[2] for plan in plans}),5)
        self.assertIn("--property=PrivateNetwork=yes",plans[2])
        for setting in ("Type=exec","Restart=no","RemainAfterExit=yes","TimeoutStartSec=30s","RuntimeMaxSec=2h",
                        "TimeoutStopSec=90s","KillMode=control-group","SendSIGKILL=yes"):
            self.assertIn("--property="+setting,plans[2])
        self.assertNotIn("--property=TimeoutStartSec=infinity",plans[2])
        for plan in plans:self.assertIn(e.ENTRY,plan)
        with self.assertRaises(c.Refused):e.unit("hydrate","../"+"d"*32)
    def test_native_false_restore_never_authorizes_retirement(self):
        request=e.request(policy().digest,"d"*32,"status",{"stage":"restore","receipt_facts":{}})
        value=response(request);value["facts"]["native_restore_accepted"]="true"
        with self.assertRaises(c.Refused):e.validate_response(value,request)
    def test_unknown_previous_postmaster_or_cgroup_refuses_before_intent(self):
        fake=Fake()
        def unknown(value):raise c.Refused("unknown_terminal_state")
        fake.require_previous_terminal=unknown
        p=policy();request=e.request(p.digest,"d"*32,"hydrate",{"stage":"hydrate","receipt_facts":{}})
        with self.assertRaises(c.Refused):e.Endpoint(p,fake).handle(request)
        self.assertNotIn("intent",fake.events);self.assertNotIn("dispatch",fake.events)
    def test_lost_dispatch_ack_never_allows_second_dispatch(self):
        fake=Fake()
        def lost(value):fake.events.append("dispatch");raise RuntimeError("lost")
        fake.dispatch=lost
        p=policy();request=e.request(p.digest,"d"*32,"hydrate",{"stage":"hydrate","receipt_facts":{}})
        endpoint=e.Endpoint(p,fake)
        with self.assertRaises(c.Refused):endpoint.handle(request)
        endpoint.handle(request)
        self.assertEqual(fake.events.count("dispatch"),1)


class NativeBoundaryTests(unittest.TestCase):
    @contextmanager
    def entry(self, stub):
        package=importlib.import_module("deploy.onedrive_retention")
        with patch.dict(sys.modules,{"deploy.onedrive_retention.entry":stub}), \
                patch.object(package,"entry",stub,create=True):yield

    def evidence(self, receipt_tx="d"*32, attempt=1):
        from deploy.onedrive_retention import native_restore as native
        p=policy();query=e.request(p.digest,"d"*32,"status",{"stage":"restore","receipt_facts":{}})
        result=response(query)
        stamp=dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=4)
        download={"schema":"careertrellis.onedrive-bundle-download/v1","result":"download_verified",
            "transaction":receipt_tx,"backup_label":"20261026-155056F","generation_sha256":"b"*64,
            "archive_sha256":"a"*64,"archive_bytes":12345,"manifest_sha256":"e"*64,
            "native_restore_accepted":False,"completed_at":stamp.isoformat(),
            "destination":native.PREFIX+"a"*64+"/repository.tar","quota_after":{"total":100,"free":40}}
        download_raw=e.bundle.canonical(download)
        owner={"pid":123,"start_ticks":456,"process_group":123,
            "boot_id":"01234567-89ab-cdef-0123-456789abcdef"}
        complete={"schema":"careertrellis.onedrive-native-local-restore/v1","result":"verified",
            "transaction":receipt_tx,"attempt":attempt,"archive_sha256":"a"*64,
            "generation_sha256":"b"*64,"backup_label":"20261026-155056F","plan_sha256":"c"*64,
            "download_receipt_sha256":hashlib.sha256(download_raw).hexdigest(),
            "native_restore_accepted":True,"postmaster_terminal":True,"checksums_verified":True,
            "completed_at":(stamp+dt.timedelta(seconds=1)).isoformat()}
        result["receipt_sha256"]=hashlib.sha256(e.bundle.canonical(complete)).hexdigest()
        unit="careertrellis-onedrive-bundle-restore-"+receipt_tx+("" if attempt==1 else "-%02d"%attempt)+".service"
        snapshot={"LoadState":"loaded","ActiveState":"active","SubState":"exited","RemainAfterExit":"yes",
            "MainPID":"0","ControlPID":"0","Job":"","Result":"success","ExecMainStatus":"0",
            "ExecMainPID":"123","InvocationID":"f"*32,"ControlGroup":"/system.slice/"+unit}
        stop={"schema":"careertrellis.onedrive-native-stop-intent/v1","transaction":receipt_tx,
            "attempt":attempt,"unit":unit,"owner":owner,"receipt_sha256":result["receipt_sha256"],
            "unit_state":snapshot,"observed_at":(stamp+dt.timedelta(seconds=2)).isoformat()}
        proof={"schema":"careertrellis.onedrive-native-terminal/v1","transaction":receipt_tx,
            "attempt":attempt,"unit":unit,"owner":owner,"receipt_sha256":result["receipt_sha256"],
            "plan_sha256":complete["plan_sha256"],"unit_state":snapshot,
            "owner_terminal":True,"cgroup_empty":True,"postmaster_terminal":True,"checksums_verified":True,
            "failure_markers_absent":True,"observed_at":(stamp+dt.timedelta(seconds=3)).isoformat(),
            "stop_intent_sha256":hashlib.sha256(e.bundle.canonical(stop)).hexdigest(),
            "stopped_unit_state":{"LoadState":"not-found","ActiveState":"inactive","SubState":"dead",
                "MainPID":"0","ControlPID":"0","Job":"","ControlGroup":""}}
        documents={"complete.json":complete,"ownership.json":{"owner":owner,
            "download_receipt_sha256":complete["download_receipt_sha256"]},
            "unit-terminal-proof.json":proof,"unit-stop-intent.json":stop,"downloaded-receipt.json":download}
        return p,query,result,documents,native

    def observe(self, result, documents, query, p, native):
        def protected(path,**kwargs):
            name=Path(path).name
            if name not in documents:raise FileNotFoundError("synthetic missing proof")
            return e.bundle.canonical(documents[name])
        with self.entry(SimpleNamespace(observe_retention_stage=lambda *args:result)), \
                patch.object(native,"protected",side_effect=protected):
            return e.NativeDB(p).observe(query)

    def test_missing_collector_dispatch_refuses_without_fallback_unit_command(self):
        p=policy();now=dt.datetime.now(dt.timezone.utc).isoformat()
        web={"schema":"careertrellis.onedrive-restore-web-preflight/v1","host":"careertrellis-staging-web-1",
            "authority":"postgres-r2-authoritative","pending_phase":"activated","activation_lease_absent":True,
            "services_healthy":True,"release_binding_verified":True,"release":c.RELEASE,"observed_at":now}
        value=e.request(p.digest,"d"*32,"restore",{"stage":"restore",
            "receipt_facts":{"predecessor_receipt_sha256":"a"*64},"web_preflight":web})
        for stub in (SimpleNamespace(),SimpleNamespace(dispatch_native_with_collector=lambda tx:False)):
            with self.entry(stub),patch.object(e.hydrate,"exclusive"),patch.object(e.hydrate,"command") as command:
                with self.assertRaises(c.Refused):e.NativeDB(p).dispatch(value)
                command.assert_not_called()

    def test_complete_without_external_proof_refuses(self):
        p,query,result,documents,native=self.evidence()
        documents.pop("unit-terminal-proof.json")
        with self.assertRaises(FileNotFoundError):self.observe(result,documents,query,p,native)

    def test_actual_receipt_and_download_bytes_bound_complete_succeeds(self):
        p,query,result,documents,native=self.evidence()
        self.assertEqual(self.observe(result,documents,query,p,native),result)

    def test_internally_valid_proof_for_other_transaction_or_attempt_refuses(self):
        for tx,attempt in (("e"*32,1),("d"*32,2)):
            p,query,result,documents,native=self.evidence(tx,attempt)
            native.validate_terminal_proof(documents["unit-terminal-proof.json"],documents["complete.json"],
                documents["ownership.json"]["owner"],stop_intent=documents["unit-stop-intent.json"])
            with self.subTest(tx=tx,attempt=attempt),self.assertRaises(c.Refused):
                self.observe(result,documents,query,p,native)

    def test_normalized_facts_cannot_diverge_from_native_or_download_receipt(self):
        for key,bad in (("archive_sha256","f"*64),("generation_sha256","f"*64),
                        ("backup_label","20261025-032436F"),("archive_bytes",12346)):
            p,query,result,documents,native=self.evidence()
            result["facts"][key]=bad
            with self.subTest(key=key),self.assertRaises(c.Refused):self.observe(result,documents,query,p,native)

    def test_changed_download_receipt_bytes_refuses_native_digest_binding(self):
        p,query,result,documents,native=self.evidence()
        documents["downloaded-receipt.json"]["archive_bytes"]=12346
        result["facts"]["archive_bytes"]=12346
        with self.assertRaises(c.Refused):self.observe(result,documents,query,p,native)
