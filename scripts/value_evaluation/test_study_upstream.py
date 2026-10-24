import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from campaign_state import Campaign, CampaignRefused
from test_campaign_state import policy
from study_upstream import (FAKE_KEY, MODEL, RehearsalRefused, ScriptedBroker, ToolPolicy,
    child_environment, reused_environment, run_phase, sse, stop_owned_process)


def payload(reviewer=False, messages=None):
    names = ["Read", "Glob", "Grep"] if reviewer else ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "Agent"]
    return json.dumps({"model": MODEL, "stream": True, "max_tokens": 32768,
        "thinking": {"type": "adaptive", "display": "omitted"}, "output_config": {"effort": "medium"},
        "context_management": {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]},
        "messages": messages or [{"role": "user", "content": "Synthetic mechanics only."}],
        "tools": [{"name": name, "input_schema": {"type": "object"}} for name in names]}).encode()


class NativeBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ct-native-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        for name in ("cv", "cover_letters", ".scratch", "documents"):
            (self.workspace / name).mkdir()
        self.bound = policy()
        self.campaign = Campaign.create(self.root / "state.jsonl", self.bound)
        self.campaign.begin_stage("CT", "V01", "fit")
        self.policy = ToolPolicy(self.workspace, {"cv/master.tex": "0" * 64})
        self.output = self.root / "requests"
        self.output.mkdir()

    def broker(self, actions=()):
        return ScriptedBroker(self.campaign, "synthetic", self.output, self.policy, actions)

    def test_exact_request_bytes_frozen_and_reserved_before_scripted_response(self):
        broker = self.broker()
        raw = bytearray(payload())
        original = bytes(raw)
        call, response = broker.dispatch(raw)
        raw[:] = b"changed"
        self.assertEqual(original, (self.output / "request-001.json").read_bytes())
        self.assertEqual(hashlib.sha256(original).hexdigest(), broker.requests[0]["request_sha256"])
        self.assertIn(b"SYNTHETIC_MECHANICS_COMPLETE", response)
        self.assertEqual(1, self.campaign.snapshot()["pending_calls"])
        self.assertEqual(1, self.campaign.snapshot()["exposure_microdollars"])
        self.assertEqual("synthetic_c001", call)

    def test_parent_and_named_reviewer_share_same_stage_count(self):
        broker = self.broker()
        for reviewer in (False, True):
            call, _ = broker.dispatch(payload(reviewer))
            self.campaign.settle(call, "0", "synthetic/usage")
        self.assertEqual(1, broker.reviewer_requests)
        with self.assertRaises(CampaignRefused):
            broker.dispatch(payload())
        self.assertEqual(2, self.campaign.snapshot()["admitted_requests"])

    def test_unknown_retains_count_and_blocks_native_replay(self):
        broker = self.broker()
        call, _ = broker.dispatch(payload())
        self.campaign.mark_unknown(call, "synthetic/unknown")
        with self.assertRaises(CampaignRefused):
            broker.dispatch(payload(True))
        self.assertTrue(self.campaign.snapshot()["halted"])

    def test_unsupported_model_features_duplicate_keys_and_failed_tools_never_admit(self):
        broker = self.broker()
        wrong = json.loads(payload())
        wrong["model"] = "unreviewed"
        failed = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "fixture", "is_error": True, "content": "synthetic failure"}]}]
        for raw in (json.dumps(wrong).encode(), b'{"model":"first","model":"second"}', payload(messages=failed)):
            with self.assertRaises((RehearsalRefused, ValueError)):
                broker.dispatch(raw)
        self.assertEqual(0, self.campaign.snapshot()["admitted_requests"])

    def test_server_only_serves_loopback_scripted_messages_and_count_never_forwards(self):
        broker = self.broker()
        server = broker.server()
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            request = Request(base + "/v1/messages/count_tokens", b"{}", {"x-api-key": FAKE_KEY}, method="POST")
            with urlopen(request, timeout=5) as response:
                self.assertEqual({"input_tokens": 10}, json.loads(response.read()))
            request = Request(base + "/v1/messages?beta=true", payload(), {"x-api-key": FAKE_KEY}, method="POST")
            with urlopen(request, timeout=5) as response:
                self.assertIn(b"message_stop", response.read())
            self.assertTrue(broker.response_settled.wait(timeout=5))
            self.assertEqual(1, broker.count_requests)
            self.assertEqual(1, self.campaign.snapshot()["admitted_requests"])
            self.assertEqual(0, self.campaign.snapshot()["pending_calls"])
            with self.assertRaises(HTTPError):
                urlopen(Request(base + "/unapproved", b"{}", {"x-api-key": FAKE_KEY}, method="POST"), timeout=5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def test_tool_commands_are_narrow_and_frozen_sources_cannot_be_edited(self):
        self.policy.validate("Bash", {"command": "cd cv && xelatex -no-shell-escape -interaction=nonstopmode -halt-on-error -output-directory=. fixture.tex"})
        self.policy.validate("Bash", {"command": "python tools/verify_pdf.py cv/fixture.pdf --pages 2 --min-chars 10 --dump-text .scratch/cv.txt"})
        self.policy.validate("Write", {"file_path": str(self.workspace / "cv/fixture.tex"), "content": "synthetic"})
        for name, args in (("Bash", {"command": "curl https://example.invalid"}),
            ("Bash", {"command": "cd cv && xelatex -shell-escape fixture.tex"}),
            ("Bash", {"command": "python tools/verify_layout.py cv/fixture.pdf; whoami"}),
            ("Write", {"file_path": str(self.workspace / "cv/master.tex")}),
            ("Read", {"file_path": str(self.root / "outside.txt")}),
            ("DeferredToolPlaceholder", {}),
            ("Agent", {"subagent_type": "unreviewed"})):
            with self.assertRaises(RehearsalRefused):
                self.policy.validate(name, args)

    def test_pinned_cli_inert_placeholder_definition_does_not_grant_tool_execution(self):
        value = json.loads(payload())
        value["tools"].append({"name": "DeferredToolPlaceholder", "input_schema": {"type": "object"}})
        broker = self.broker()
        _, response = broker.dispatch(json.dumps(value).encode())
        self.assertIn(b"SYNTHETIC_MECHANICS_COMPLETE", response)
        with self.assertRaises(RehearsalRefused):
            self.policy.validate("DeferredToolPlaceholder", {})

    def test_nullable_custom_schema_is_data_and_server_typed_placeholder_refused(self):
        value = json.loads(payload())
        value["tools"][0]["input_schema"]["properties"] = {"optional": {"type": ["string", "null"]}}
        broker = self.broker()
        call, _ = broker.dispatch(json.dumps(value).encode())
        self.campaign.settle(call, "0", "synthetic/usage")
        value["tools"].append({"name": "DeferredToolPlaceholder", "type": "web_search_20250305", "input_schema": {"type": "object"}})
        with self.assertRaises(RehearsalRefused):
            broker.dispatch(json.dumps(value).encode())

    def test_background_sandbox_flags_and_unbounded_timeout_are_refused(self):
        command = "python tools/verify_layout.py cv/fixture.pdf"
        for modifier in ({"run_in_background": True}, {"run_in_background": False},
                         {"dangerouslyDisableSandbox": True}, {"timeout": True}, {"timeout": 120001}):
            with self.subTest(modifier=modifier), self.assertRaises(RehearsalRefused):
                self.policy.validate("Bash", {"command": command, **modifier})

    def test_native_output_limit_and_undeclared_temperature_or_tool_choice_are_refused(self):
        for modifier in ({"max_tokens": 16384}, {"temperature": 0.5}, {"tool_choice": {"type": "any"}}):
            value = json.loads(payload())
            value.update(modifier)
            with self.subTest(modifier=modifier), self.assertRaises(RehearsalRefused):
                self.broker().dispatch(json.dumps(value).encode())
        self.assertEqual(0, self.campaign.snapshot()["admitted_requests"])

    def test_child_environment_excludes_inherited_auth_and_reuse_revalidates(self):
        executables = {name: self.root / "tools" / name for name in ("cli", "python", "xelatex", "pdftoppm", "pdftotext", "pdfinfo", "git", "bash")}
        runtime = self.root / "fit"
        runtime.mkdir()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-inherited-must-not-pass", "ANTHROPIC_AUTH_TOKEN": "synthetic-inherited-must-not-pass"}):
            env = child_environment(runtime, 12345, executables)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertEqual(FAKE_KEY, env["ANTHROPIC_API_KEY"])
        continuation = self.root / "materials"
        continuation.mkdir()
        reused = reused_environment(env, continuation, 23456, executables)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], reused["CLAUDE_CONFIG_DIR"])
        self.assertEqual("http://127.0.0.1:23456", reused["ANTHROPIC_BASE_URL"])
        with self.assertRaises(RehearsalRefused):
            reused_environment({**env, "OPENAI_API_KEY": "synthetic"}, continuation, 23456, executables)

    def test_fit_only_materials_and_paid_mode_refused_before_process_or_files(self):
        for case in ("V07", "V12"):
            with patch("study_upstream.subprocess.Popen") as launch:
                with self.assertRaises(RehearsalRefused):
                    run_phase(self.campaign, "UP", case, "materials", self.workspace, "a" * 40, {}, {}, {}, self.root / "not-created", "invalid", b"fixture", [])
                launch.assert_not_called()
            self.assertFalse((self.root / "not-created").exists())
        paid = copy.deepcopy(self.bound)
        paid.update(mode="paid", approval_reference="synthetic/not-authority")
        campaign = Campaign.create(self.root / "paid-declaration.jsonl", paid)
        with self.assertRaises(RehearsalRefused):
            ScriptedBroker(campaign, "synthetic", self.output, self.policy, [])

    def test_visual_requires_an_owned_native_read(self):
        broker = self.broker()
        visual = [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "data": "fixture"}}]}]
        with self.assertRaises(RehearsalRefused):
            broker.dispatch(payload(messages=visual))

    def test_script_fixture_preserves_native_tool_structure(self):
        response = sse(("Read", {"file_path": str(self.workspace / "cv/fixture.pdf"), "pages": "1-2"}), 1)
        self.assertIn(b'"stop_reason": "tool_use"', response)
        self.assertIn(b"input_json_delta", response)

    def test_generic_exception_and_interrupt_after_launch_cleanup_exact_owned_process(self):
        agents = self.workspace / ".claude/agents"
        agents.mkdir(parents=True)
        (agents / "application-reviewer.md").write_text("---\nname: application-reviewer\n---\nSynthetic reviewer.")
        for number, exception in enumerate((RuntimeError("synthetic failure"), KeyboardInterrupt())):
            campaign = Campaign.create(self.root / f"generic-{number}.jsonl", self.bound)
            process = Mock(pid=54321)
            process.communicate.side_effect = exception
            executables = {name: self.root / "tools" / name for name in ("cli", "python", "xelatex", "pdftoppm", "pdftotext", "pdfinfo", "git", "bash")}
            with (patch("study_upstream.verify_runtime"),
                  patch("study_upstream.subprocess.Popen", return_value=process),
                  patch("study_upstream.stop_owned_process", return_value={"direct_child_terminal": True}) as stop):
                receipt, _ = run_phase(campaign, "CT", "V01", "fit", self.workspace, "a" * 40, {}, executables, {},
                    self.root / f"exception-{number}", "00000000-0000-4000-8000-000000000001", b"synthetic", [])
            stop.assert_called_once_with(process)
            self.assertEqual("incomplete_no_retry", receipt["status"])
            self.assertEqual(54321, receipt["native_pid"])
            self.assertTrue(receipt["broker_stopped"])

    def test_tree_stop_failure_retains_pid_and_stops_direct_owner(self):
        process = Mock(pid=54321)
        process.poll.return_value = None
        process.kill.side_effect = lambda: setattr(process.poll, "return_value", 0)
        with (patch("study_upstream.subprocess.run", side_effect=OSError("synthetic tree stop failure")),
              patch("study_upstream.os.killpg", side_effect=OSError("synthetic tree stop failure"), create=True)):
            result = stop_owned_process(process)
        self.assertEqual(54321, result["owned_pid"])
        self.assertTrue(result["direct_child_terminal"])
        self.assertTrue(result["process_tree_requires_independent_verification"])
        self.assertEqual("OSError", result["cleanup_failure_class"])

    def test_both_platform_tree_stop_paths_are_fully_mocked(self):
        for platform in ("nt", "posix"):
            with self.subTest(platform=platform):
                process = Mock(pid=54321)
                process.poll.return_value = None
                process.kill.side_effect = lambda: setattr(process.poll, "return_value", 0)
                kill_group = Mock(side_effect=OSError("synthetic group stop failure"))
                fake_os = SimpleNamespace(name=platform, environ={"SystemRoot": "/synthetic/systemroot"}, killpg=kill_group)
                with (patch("study_upstream.os", fake_os),
                      patch("study_upstream.subprocess.run", side_effect=OSError("synthetic tree stop failure")) as run,
                      patch("signal.SIGKILL", 9, create=True)):
                    result = stop_owned_process(process)
                self.assertEqual("OSError", result["cleanup_failure_class"])
                self.assertTrue(result["direct_child_terminal"])
                process.kill.assert_called_once_with()
                process.wait.assert_called_once_with(timeout=5)
                if platform == "nt":
                    kill_group.assert_not_called()
                    run.assert_called_once()
                    self.assertEqual(["/PID", "54321", "/T", "/F"], run.call_args.args[0][1:])
                else:
                    run.assert_not_called()
                    kill_group.assert_called_once_with(54321, 9)

    def test_incomplete_direct_cleanup_never_finishes_stage(self):
        agents = self.workspace / ".claude/agents"
        agents.mkdir(parents=True)
        (agents / "application-reviewer.md").write_text("---\nname: application-reviewer\n---\nSynthetic reviewer.")
        campaign = Campaign.create(self.root / "cleanup-gate.jsonl", self.bound)
        process = Mock(pid=54321, returncode=0)
        process.communicate.return_value = (b'{"type":"result","subtype":"success","is_error":false,"permission_denials":[]}', b'')
        executables = {name: self.root / "tools" / name for name in ("cli", "python", "xelatex", "pdftoppm", "pdftotext", "pdfinfo", "git", "bash")}
        with (patch("study_upstream.verify_runtime"), patch("study_upstream.subprocess.Popen", return_value=process),
              patch("study_upstream.stop_owned_process", return_value={"direct_child_terminal": False})):
            receipt, _ = run_phase(campaign, "CT", "V01", "fit", self.workspace, "a" * 40, {}, executables, {},
                self.root / "cleanup-gate", "00000000-0000-4000-8000-000000000001", b"synthetic", [])
        self.assertEqual("incomplete_no_retry", receipt["status"])
        self.assertEqual(0, campaign.snapshot()["completed_stages"])
        self.assertTrue(campaign.snapshot()["active"])


if __name__ == "__main__":
    unittest.main()
