import io
import json
from unittest.mock import Mock, patch
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from study_upstream import ToolPolicy
from study_native_corrections import CorrectionRefused, POLICY, ResultBindings, bounded_witness, classify_layout, parse_groups, serializer_variant, sha, validate_policy, validate_runtime_pins, load_prior_fit, canonical, PINNED_CLI, PINNED_CHECKER, PINNED_UPSTREAM


class CorrectionContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-correction-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = ToolPolicy(self.root, {"CLAUDE.md": "0" * 64})

    def group(self, *tools):
        return {"schema": "offline-native-action-groups-v1", "groups": [{"id": "g001", "tools": [
            {"slot": slot, "name": name, "input": args} for slot, name, args in tools]}]}

    def prior_fixture(self):
        from study_upstream import json_bytes, sse
        directory = self.root / "prior";directory.mkdir()
        context = {"schema": "offline-native-phase-binding-v1", "stage": "materials", "product": "UP", "case": "V01", "session_id": "synthetic-session", "workspace": str(self.root), "commit": PINNED_UPSTREAM, "campaign_policy_sha256": "a" * 64}
        binding = dict(context, stage="fit")
        action = ["Read", {"file_path": "cv/a.tex"}]
        node = {"type": "tool_result", "tool_use_id": "toolu_fixture_1", "content": "synthetic posting"}
        records = []
        for index in (1, 2):
            raw = json_bytes({"messages": [{"role": "user", "content": [node] if index == 2 else "Synthetic"}]})
            response = sse(action if index == 1 else None, index)
            (directory / f"request-{index:03d}.json").write_bytes(raw)
            (directory / f"response-{index:03d}.sse").write_bytes(response)
            records.append({"call_id": f"UP_V01_fit_c{index:03d}", "request_sha256": sha(raw), "response_sha256": sha(response), "reviewer": False})
        stdout = json_bytes({"type": "result", "subtype": "success", "is_error": False, "session_id": context["session_id"]})
        actions = json_bytes([action]);bound = json_bytes(binding)
        receipt = {"phase_binding_sha256": sha(bound), "status": "completed", "stage": "fit", "product": "UP", "case": "V01", "native_terminal": True, "source_unchanged": True, "broker_stopped": True, "process_cleanup": {"direct_child_terminal": True}, "actions_completed": 1, "expected_actions": 1, "reviewer_requests": 0, "tool_results": 1, "stdout_sha256": sha(stdout), "actions_sha256": sha(actions), "requests": records}
        for name, raw in (("phase-binding.json", bound), ("stdout.json", stdout), ("actions.json", actions), ("receipt.json", json_bytes(receipt))):
            (directory / name).write_bytes(raw)
        return directory, context, receipt, node

    def test_prior_fit_exact_evidence_import_only(self):
        directory, context, receipt, node = self.prior_fixture()
        imported = load_prior_fit(directory, context, self.policy)
        self.assertEqual(imported["results"], {"toolu_fixture_1": sha(canonical(node))})
        self.assertEqual(imported["bindings"][0]["json_pointer"], "/messages/0/content/0")
        for key in ("session_id", "workspace", "commit", "campaign_policy_sha256"):
            with self.assertRaises(CorrectionRefused): load_prior_fit(directory, dict(context, **{key: "changed"}), self.policy)

    def test_prior_linked_evidence_refused_before_open(self):
        directory, context, receipt, node = self.prior_fixture()
        original = Path.is_symlink
        for filename in ("receipt.json", "actions.json", "request-002.json", "response-001.sse", "stdout.json", "phase-binding.json"):
            with patch.object(Path, "is_symlink", lambda item: item.name == filename or original(item)):
                with self.assertRaises(ValueError): load_prior_fit(directory, context, self.policy)
        with self.assertRaises(CorrectionRefused): load_prior_fit(directory / ".." / "prior", context, self.policy)

    def test_prior_changed_raw_or_incomplete_phase_refuses(self):
        directory, context, receipt, node = self.prior_fixture()
        response = directory / "response-001.sse";original = response.read_bytes();response.write_bytes(original + b"extra")
        with self.assertRaises(CorrectionRefused): load_prior_fit(directory, context, self.policy)
        response.write_bytes(original)
        for value in ("failed", "incomplete_no_retry"):
            (directory / "receipt.json").write_bytes(canonical(dict(receipt, status=value)))
            with self.assertRaises(CorrectionRefused): load_prior_fit(directory, context, self.policy)

    def test_prior_selfconsistent_error_visual_or_unknown_result_refuses(self):
        directory, context, receipt, node = self.prior_fixture()
        for change in ({"is_error": True}, {"content": [{"type": "image"}]}, {"tool_use_id": "unknown"}):
            raw = canonical({"messages": [{"role": "user", "content": [dict(node, **change)]}]})
            (directory / "request-002.json").write_bytes(raw)
            changed = json.loads(json.dumps(receipt));changed["requests"][1]["request_sha256"] = sha(raw)
            (directory / "receipt.json").write_bytes(canonical(changed))
            with self.assertRaises(CorrectionRefused): load_prior_fit(directory, context, self.policy)

    def grouped_broker(self):
        from campaign_state import Campaign
        from test_campaign_state import policy
        from study_upstream import ScriptedBroker
        campaign = Campaign.create(self.root / "state.jsonl", policy())
        campaign.begin_stage("CT", "V01", "fit")
        directory = self.root / "broker";directory.mkdir()
        actions = self.group(("a", "Read", {"file_path": "cv/a.pdf"}), ("b", "Read", {"file_path": "cover_letters/b.pdf"}))
        broker = ScriptedBroker(campaign, "grouped", directory, self.policy, actions, POLICY)
        broker.correction = SimpleNamespace(cleanup_unknown=False, before_group=lambda *args: None)
        return broker, campaign, directory

    def test_group_sse_ids_and_all_results_before_next_parent(self):
        from test_study_upstream import payload
        broker, campaign, directory = self.grouped_broker()
        call, response = broker.dispatch(payload())
        campaign.settle(call, "0", "fixture/group")
        self.assertIn(b"toolu_fixture_1_g001_a", response)
        self.assertIn(b"toolu_fixture_1_g001_b", response)
        self.assertEqual(broker.index, 2)
        missing = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_fixture_1_g001_a", "content": "first"}]}]
        both = missing[0]["content"] + [{"type": "tool_result", "tool_use_id": "toolu_fixture_1_g001_b", "content": "second"}]
        final, response = broker.dispatch(payload(messages=[{"role": "user", "content": both}]))
        self.assertIn(b"SYNTHETIC_MECHANICS_COMPLETE", response)
        self.assertEqual(campaign.snapshot()["admitted_requests"], 2)
        self.assertEqual(len(broker.bindings.bindings), 2)

    def test_incomplete_group_refusal_latches_no_replay(self):
        from test_study_upstream import payload
        broker, campaign, directory = self.grouped_broker()
        call, _ = broker.dispatch(payload());campaign.settle(call, "0", "fixture/group")
        with self.assertRaises(ValueError): broker.dispatch(payload())
        self.assertTrue(broker.failed)
        self.assertTrue((directory / "received-002.json").exists())
        with self.assertRaises(ValueError): broker.dispatch(payload())
        self.assertEqual(campaign.snapshot()["admitted_requests"], 1)

    def test_group_reviewer_requires_outstanding_agent_and_rejected_body_retained(self):
        from test_study_upstream import payload
        broker, campaign, directory = self.grouped_broker()
        with self.assertRaises(ValueError): broker.dispatch(payload(True))
        self.assertEqual(campaign.snapshot()["admitted_requests"], 0)
        self.assertEqual((directory / "received-001.json").read_bytes(), payload(True))

    def witness(self, stdout=b"report", stderr=b"", terminal=True, **kwargs):
        process = SimpleNamespace(pid=12345, stdout=io.BytesIO(stdout), stderr=io.BytesIO(stderr), returncode=1, poll=lambda: 1)
        with patch("subprocess.Popen", return_value=process):
            return bounded_witness(["pinned-python", "pinned-checker", "cv/a.pdf"], self.root, {}, self.root / "witness", lambda: {"pdf": "a" * 64}, lambda pid, started: {"owned_pid": pid, "terminal": terminal}, **kwargs)

    def test_witness_launch_failure_retains_unknown(self):
        with patch("subprocess.Popen", side_effect=OSError("synthetic")):
            record, raw = bounded_witness(["pinned"], self.root, {}, self.root / "failure", lambda: {"pdf": "a" * 64}, lambda *args: {"terminal": True})
        self.assertTrue(record["cleanup_unknown"])
        self.assertFalse(record["supported_verdict"])
        self.assertEqual(raw, b"")

    def test_witness_source_mutation_refuses_capture_validity(self):
        process = SimpleNamespace(pid=12345, stdout=io.BytesIO(b"report"), stderr=io.BytesIO(), returncode=1, poll=lambda: 1)
        values = iter(({"pdf": "a" * 64}, {"pdf": "b" * 64}))
        with patch("subprocess.Popen", return_value=process):
            record, raw = bounded_witness(["pinned"], self.root, {}, self.root / "mutation", lambda: next(values), lambda pid, started: {"owned_pid": pid, "terminal": True})
        self.assertFalse(record["bindings_unchanged"])
        self.assertFalse(record["capture_valid"])

    def test_witness_capture_is_not_verdict(self):
        record, raw = self.witness(b"LAYOUT PROBLEMS")
        self.assertTrue(record["capture_valid"])
        self.assertFalse(record["supported_verdict"])
        self.assertFalse(record["cleanup_unknown"])
        self.assertEqual(sha(raw), record["stdout_sha256"])
        self.assertEqual(raw, (self.root / "witness/stdout.bin").read_bytes())

    def test_witness_unknown_tree_blocks_despite_direct_terminal(self):
        record, raw = self.witness(terminal=False)
        self.assertTrue(record["cleanup_unknown"])
        self.assertFalse(record["capture_valid"])
        self.assertFalse(record["supported_verdict"])

    def test_witness_output_bound_during_capture(self):
        with patch("study_upstream.stop_owned_process", return_value={"direct_child_terminal": True}) as stop:
            record, raw = self.witness(b"x" * 100, stdout_limit=10)
        self.assertEqual(len(raw), 11)
        self.assertTrue(record["output_overflow"])
        self.assertFalse(record["capture_valid"])
        stop.assert_called_once()

    def test_complete_pinned_report_only(self):
        text = ("cv/a.pdf: 2 page(s) (page count is verify_pdf.py's job, not checked here)\n"
                "  p1: text y 64..438 of 842pt | bottom 403pt (48%) | largest gap 52pt at y113\n"
                "  p2: text y 64..438 of 842pt | bottom 403pt (48%) | largest gap 52pt at y113\n\n"
                "LAYOUT PROBLEMS:\n"
                "  - p1 ends 403pt (48%) early although more pages follow, which reads as a broken page break\n"
                "  - p2 is the last page and 48% empty, which reads as an unfinished document; restore the highest-relevance content previously cut\n")
        raw = text.encode()
        record = {"capture_valid": True, "cleanup_unknown": False, "terminal": True, "exit_code": 1, "stderr_sha256": sha(b""), "stdout_sha256": sha(raw), "bindings_unchanged": True}
        native = "Exit code 1\n" + text[:-1]
        self.assertEqual(classify_layout(native, raw, record, "cv/a.pdf")["serializer_variant"], "omit_one_final_lf")
        for changed in (raw + b"extra\n", raw.replace(b"p2 is the last page", b"skipped p2"), b"LAYOUT PROBLEMS"):
            with self.assertRaises(CorrectionRefused): classify_layout(native, changed, dict(record, stdout_sha256=sha(changed)), "cv/a.pdf")
        with self.assertRaises(CorrectionRefused): classify_layout(native, raw, dict(record, cleanup_unknown=True), "cv/a.pdf")

    def test_pin_mismatch_refuses_before_any_runtime_process_or_stage(self):
        from study_upstream import run_phase
        campaign = Mock(policy={"mode": "offline"})
        with patch("study_upstream.verify_runtime") as verify, patch("subprocess.Popen") as launch:
            with self.assertRaises(CorrectionRefused):
                run_phase(campaign, "UP", "V01", "fit", self.root, PINNED_UPSTREAM,
                    {"tools/verify_layout.py": PINNED_CHECKER}, {}, {"cli": "a" * 64},
                    self.root / "not-created", "00000000-0000-4000-8000-000000000001", b"synthetic", self.group(("a", "Read", {"file_path": "cv/a.pdf"})), correction_policy=POLICY)
        verify.assert_not_called();launch.assert_not_called();campaign.begin_stage.assert_not_called()
        self.assertFalse((self.root / "not-created").exists())

    def test_literal_correction_runtime_pins(self):
        validate_runtime_pins(PINNED_UPSTREAM, {"tools/verify_layout.py": PINNED_CHECKER}, {"cli": PINNED_CLI})
        for commit, source, cli in (("a"*40, PINNED_CHECKER, PINNED_CLI), (PINNED_UPSTREAM, "b"*64, PINNED_CLI), (PINNED_UPSTREAM, PINNED_CHECKER, "c"*64)):
            with self.assertRaises(CorrectionRefused): validate_runtime_pins(commit, {"tools/verify_layout.py": source}, {"cli": cli})

    def test_policy_exact(self):
        self.assertEqual(validate_policy(POLICY), POLICY)
        for value in (None, dict(POLICY, extra=True), dict(POLICY, serializer=True)):
            with self.assertRaises(CorrectionRefused): validate_policy(value)

    def test_serializer_only_two_variants(self):
        self.assertEqual(serializer_variant("Exit code 1\nLAYOUT PROBLEMS\n", b"LAYOUT PROBLEMS\r\n"), "exact")
        self.assertEqual(serializer_variant("Exit code 1\nLAYOUT PROBLEMS", b"LAYOUT PROBLEMS\n"), "omit_one_final_lf")
        for native, stdout in (("Exit code 1\nX", b"X\n\n"), ("Exit code 1\nX ", b"X\n"), ("Exit code 2\nX", b"X\n"), ("Exit code 1\nX\n", b"X")):
            with self.assertRaises(CorrectionRefused): serializer_variant(native, stdout)

    def test_disjoint_writes_immutable(self):
        value = self.group(("a", "Write", {"file_path": "cv/a.tex", "content": "a"}), ("b", "Write", {"file_path": "cover_letters/b.tex", "content": "b"}))
        frozen = parse_groups(value, self.policy)
        value["groups"][0]["tools"][0]["input"]["content"] = "changed"
        self.assertEqual(frozen[0]["tools"][0]["input"]["content"], "a")

    def test_wildcard_reader_conflicts_with_every_write(self):
        for name in ("Glob", "Grep"):
            with self.assertRaises(CorrectionRefused):
                parse_groups(self.group(("a", name, {"pattern": "*"}), ("b", "Write", {"file_path": "cv/a.tex", "content": "x"})), self.policy)

    def test_casefold_and_edit_dependencies(self):
        with self.assertRaises(CorrectionRefused):
            parse_groups(self.group(("a", "Read", {"file_path": "cv/A.tex"}), ("b", "Write", {"file_path": "cv/a.tex", "content": "x"})), self.policy)
        with self.assertRaises(CorrectionRefused):
            parse_groups(self.group(("a", "Write", {"file_path": "CLAUDE.md", "content": "x"})), self.policy)

    def test_compiler_and_reviewer_singleton(self):
        for name, args in (("Bash", {"command": "cd cv && xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error -output-directory=. a.tex"}), ("Agent", {"subagent_type": "application-reviewer"})):
            with self.assertRaises(CorrectionRefused):
                parse_groups(self.group(("a", name, args), ("b", "Read", {"file_path": "cv/a.tex"})), self.policy)

    def test_layout_and_pdf_checks_disjoint(self):
        value = self.group(("a", "Bash", {"command": "python tools/verify_layout.py cv/a.pdf"}), ("b", "Bash", {"command": "python tools/verify_pdf.py cover_letters/b.pdf --pages 1 --min-chars 50 --dump-text .scratch/b.txt"}))
        self.assertEqual(len(parse_groups(value, self.policy)[0]["tools"]), 2)

    def test_strict_group_schema(self):
        base = self.group(("a", "Read", {"file_path": "cv/a.pdf"}))
        for change in ({"extra": True}, {"schema": "other"}, {"groups": []}, {"groups": True}):
            with self.assertRaises(CorrectionRefused): parse_groups(dict(base, **change), self.policy)

    def test_result_binding_repeats_and_conflicts(self):
        ledger = ResultBindings();ledger.emit("t1", "Read", {"file_path": "cv/a.pdf"})
        node = {"type": "tool_result", "tool_use_id": "t1", "content": "first"}
        self.assertTrue(ledger.observe(node, b"raw1", "/messages/1/content/0"))
        self.assertFalse(ledger.observe(node, b"raw2", "/messages/2/content/0"))
        self.assertEqual(len(ledger.bindings), 1)
        self.assertTrue(ledger.complete({"t1"}))
        for invalid in (dict(node, content="changed"), dict(node, tool_use_id="unknown"), dict(node, is_error=1)):
            with self.assertRaises(CorrectionRefused): ledger.observe(invalid, b"raw3", "/messages/3")


if __name__ == "__main__":
    unittest.main()
