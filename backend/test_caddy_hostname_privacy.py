"""Hostname transitions preserve the exact active v7 privacy boundary."""

from pathlib import Path
import re
import unittest

import caddy_log_privacy as privacy


class HostnamePrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = (Path(__file__).resolve().parent.parent /
                         "deploy/caddy/Caddyfile.staging").read_text(encoding="utf-8")
        cls.legacy = Path(__file__).with_name("Caddyfile.legacy-pilot-test").read_text(encoding="utf-8")

    def assertRejected(self, source):
        for validator in (privacy.validate_caddy_config, privacy.validate_caddy_privacy_floor):
            with self.subTest(validator=validator.__name__):
                with self.assertRaises(privacy.CaddyPrivacyError) as caught:
                    validator(source)
                self.assertEqual(caught.exception.code, "config_contract_invalid")

    def test_legacy_and_canonical_keep_privacy_floor(self):
        canonical_only = self.legacy.replace("staging-app.jhunterj.com {",
                                             "careertrellis.jhunterj.com {")
        retained_beta = self.candidate.split("staging-app.jhunterj.com {", 1)[0].replace(
            "careertrellis.jhunterj.com {", "staging-app.jhunterj.com {"
        )
        self.assertNotIn("basic_auth", retained_beta)
        for source in (self.legacy, retained_beta, self.candidate, canonical_only):
            privacy.validate_caddy_config(source)
            privacy.validate_caddy_privacy_floor(source)

    def test_canonical_beta_has_no_basic_auth_and_scoped_referrer_policy(self):
        serving, retired = self.candidate.split("staging-app.jhunterj.com {", 1)
        self.assertNotIn("basic_auth", self.candidate)
        feedback_paths = "/feedback /static/feedback.css /static/feedback.js"
        self.assertRegex(
            serving,
            r"(?m)^\s*@feedbackReferrer path " + re.escape(feedback_paths) + r"\s*$",
        )
        self.assertRegex(
            serving,
            r"(?m)^\s*@otherReferrer\s*\{\s*not path " + re.escape(feedback_paths) + r"\s*\}",
        )
        serving_referrers = [
            line.strip() for line in serving.splitlines()
            if "Referrer-Policy" in line and not line.lstrip().startswith("#")
        ]
        self.assertCountEqual(serving_referrers, [
            'header @feedbackReferrer >Referrer-Policy "no-referrer"',
            'header @otherReferrer >Referrer-Policy "same-origin"',
        ])
        retired_referrers = [
            line.strip() for line in retired.splitlines()
            if "Referrer-Policy" in line and not line.lstrip().startswith("#")
        ]
        self.assertEqual(retired_referrers, ['Referrer-Policy "no-referrer"'])

    def test_unreviewed_host_and_extra_sites_are_rejected(self):
        self.assertRejected(self.legacy.replace("staging-app.jhunterj.com {", "other.invalid {"))
        self.assertRejected(self.candidate + "\nother.invalid {\nrespond ok\n}\n")
        self.assertRejected(self.candidate + "\nstaging-app.jhunterj.com {\nrespond ok\n}\n")

    def test_two_serving_sites_are_rejected(self):
        self.assertRejected(self.candidate.replace(
            "staging-app.jhunterj.com {", "staging-app.jhunterj.com {\nreverse_proxy 127.0.0.1:8001"))
        self.assertRejected(self.legacy + self.legacy[self.legacy.index("staging-app.jhunterj.com {"):])
        self.assertRejected(self.legacy + "\ncareertrellis.jhunterj.com {\nrespond ok\n}\n")

    def test_auth_skip_must_be_on_serving_site_and_unconditional(self):
        missing = self.candidate.replace("\tlog_skip /auth*", "")
        self.assertRejected(missing)
        self.assertRejected(missing.replace("staging-app.jhunterj.com {",
                                            "staging-app.jhunterj.com {\nlog_skip /auth*"))
        self.assertRejected(self.candidate.replace("\tlog_skip /auth*",
                                                   "\thandle /limited/* {\nlog_skip /auth*\n}"))

    def test_leaky_log_additions_and_sink_changes_are_rejected(self):
        for addition in ("log {\noutput stderr\n}", "log extra {\noutput stderr\n}",
                         "log_append copied {http.request.header.Authorization}"):
            self.assertRejected(self.candidate.replace("staging-app.jhunterj.com {",
                                                        "staging-app.jhunterj.com {\n" + addition))
        self.assertRejected(self.candidate.replace("careertrellis-staging-access-v7.log", "other.log"))
        self.assertRejected(self.candidate.replace("request>headers delete", "request>headers hash", 1))

    def test_access_log_cannot_move_to_retired_site_or_nested_handle(self):
        start = self.candidate.index("\tlog {\n")
        end = self.candidate.index("\n\t# Authentication paths", start)
        log = self.candidate[start:end]
        without = self.candidate[:start] + self.candidate[end:]
        self.assertRejected(without.replace("staging-app.jhunterj.com {",
                                             "staging-app.jhunterj.com {\n" + log))
        self.assertRejected(self.candidate.replace(log, "handle /limited/* {\n" + log + "\n}"))


if __name__ == "__main__":
    unittest.main()
