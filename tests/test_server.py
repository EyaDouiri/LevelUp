"""Offline API regressions: temporary state, loopback HTTP, mocked external services."""
import copy
import http.client
import json
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from engine import profile_signature
from providers import ProviderError
from storage import Store


class ServerTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root / "test.sqlite3")
        self.store.set("legacy_imported", True)
        self.store.set("target_2027_v2", True)
        self.profile = {
            "name": "Synthetic Student", "email": "student@example.com",
            "experience": [], "projects": [], "education": [], "skills": {},
            "target_criteria": {"countries": ["France", "Bruxelles", "Suisse", "Tunisie"],
                                "start_date": "January 2027", "keywords_priority": ["data"],
                                "contract_types": ["internship"]},
        }
        self.store.set("profile", self.profile)
        self.patch("ROOT", self.root)
        self.token = self.patch("TOKEN")
        self.token.exists.return_value = True
        self.gmail = self.patch("draft_email", return_value={"id": "mock-draft-1"})
        self.patch("service", side_effect=AssertionError("Real Gmail forbidden"))
        self.patch("Team")
        self.renderer = self.patch("build_documents", side_effect=self.render_documents)
        self.email = self.patch("build_email", return_value=b"Synthetic MIME")
        self.patch("public_url", side_effect=lambda url: url)
        self.app = server.Application(self.store)
        self.httpd = None

    def patch(self, name, *args, **kwargs):
        patcher = patch.object(server, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def render_documents(self, profile, row, output):
        output = Path(output)
        self.assertTrue(output.resolve().is_relative_to(self.root.resolve()))
        output.mkdir(parents=True, exist_ok=True)
        result = []
        for name in ("cv.pdf", "letter.pdf"):
            path = output / name
            path.write_bytes((row["email_subject"] + "\n" + row["email_body"]).encode())
            result.append({"name": name, "path": str(path)})
        return result

    def prepared(self, suffix="1"):
        row, _ = self.store.add_application({
            "url": f"https://example.com/jobs/{suffix}", "title": "Data internship",
            "status": "review_needed", "email_subject": "Internship application",
            "email_body": "Synthetic application body.", "recipient": "careers@example.com",
            "document_profile": copy.deepcopy(self.profile),
            "prepared_profile": profile_signature(self.profile), "review": {"passed": True},
        })
        docs = self.render_documents(self.profile, row, self.root / "initial" / row["id"])
        return self.store.update_application(row["id"], {"document_paths": [d["path"] for d in docs]})

    def start_http(self):
        if self.httpd:
            return
        try:
            self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(self.app))
        except OSError as exc:
            self.skipTest(f"Loopback unavailable: {exc}")
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def close():
            self.httpd.shutdown()
            self.httpd.server_close()
            thread.join(timeout=5)

        self.addCleanup(close)

    def request(self, method, path, data=None, raw=None, origin=None):
        self.start_http()
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request(method, path, body=raw if raw is not None else json.dumps(data or {}), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_edit_invalidates_approval_and_regenerates_attachments(self):
        for key, value in (("email_body", "Revised body"), ("email_subject", "Revised subject"),
                           ("recipient", "new@example.com")):
            with self.subTest(field=key):
                row = self.prepared(key)
                self.app.approve(row["id"])
                edited = self.app.patch_application(row["id"], {key: value})
                self.assertEqual(edited["id"], row["id"])
                self.assertEqual(edited["status"], "review_needed")
                self.assertIsNone(edited["approved_at"])
                self.assertFalse(edited["review"]["passed"])
                self.assertNotEqual(edited["document_paths"], row["document_paths"])
                self.assertEqual(len(edited["artifacts"]), 2)
                for path in edited["document_paths"]:
                    self.assertTrue(Path(path).is_file())
                self.assertEqual(self.renderer.call_args.args[1][key], value)
                with self.assertRaises(ValueError):
                    self.app.make_draft(row["id"])
        self.gmail.assert_not_called()

    def test_notes_and_unchanged_email_preserve_approval(self):
        row = self.prepared()
        approved = self.app.approve(row["id"])
        edited = self.app.patch_application(row["id"], {"notes": "Follow up", "email_body": row["email_body"]})
        self.assertEqual(edited["approved_at"], approved["approved_at"])
        self.assertEqual(edited["status"], "approved")
        self.assertEqual(edited["document_paths"], row["document_paths"])
        self.renderer.assert_not_called()

    def test_no_draft_before_approval_or_with_missing_timestamp(self):
        row = self.prepared()
        for changes in ({}, {"status": "approved", "approved_at": None}):
            self.store.update_application(row["id"], changes)
            before = self.store.application(row["id"])
            with self.assertRaises(ValueError):
                self.app.make_draft(row["id"])
            self.assertEqual(self.store.application(row["id"]), before)
        self.email.assert_not_called()
        self.gmail.assert_not_called()

    def test_double_draft_is_idempotent(self):
        row = self.prepared()
        self.app.approve(row["id"])
        first = self.app.make_draft(row["id"])
        second = self.app.make_draft(row["id"])
        self.assertEqual(first, second)
        self.assertEqual(first["id"], row["id"])
        self.gmail.assert_called_once_with(b"Synthetic MIME", None)

    def test_concurrent_http_drafts_call_gmail_once(self):
        row = self.prepared()
        self.app.approve(row["id"])
        self.start_http()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request("POST", f"/api/applications/{row['id']}/draft"), range(2)))
        self.assertEqual([status for status, _ in results], [200, 200])
        self.gmail.assert_called_once()

    def test_edit_existing_draft_requires_reapproval_then_updates_same_draft(self):
        row = self.prepared()
        self.app.approve(row["id"])
        self.app.make_draft(row["id"])
        edited = self.app.patch_application(row["id"], {"email_body": "Updated letter"})
        with self.assertRaises(ValueError):
            self.app.make_draft(row["id"])
        self.app.approve(row["id"])
        self.app.make_draft(row["id"])
        self.assertEqual(self.gmail.call_count, 2)
        self.gmail.assert_called_with(b"Synthetic MIME", "mock-draft-1")
        self.assertEqual(self.email.call_args.args[2], self.app.documents(edited))

    def test_manual_status_cannot_forge_approval(self):
        row = self.prepared()
        for payload in ({"status": "approved"}, {"status": "draft_created"},
                        {"approved_at": "2026-09-15"}, {"review": {"passed": True}}):
            with self.subTest(payload=payload):
                status, _ = self.request("PATCH", f"/api/applications/{row['id']}", payload)
                self.assertEqual(status, 400)
                self.assertEqual(self.store.application(row["id"]), row)
        self.gmail.assert_not_called()

    def test_settings_validation_is_atomic_over_http(self):
        invalid = [{"daily_search_limit": True}, {"daily_search_limit": 0},
                   {"monthly_search_limit": 801}, {"daily_llm_limit": 81},
                   {"watch_hour": 24}, {"watch_hour": "9"}, {"auto_watch": 1},
                   {"unknown": 1}, {"auto_watch": True, "daily_search_limit": -1}]
        before = self.store.settings()
        for payload in invalid:
            with self.subTest(payload=payload):
                status, _ = self.request("PATCH", "/api/settings", payload)
                self.assertEqual(status, 400)
                self.assertEqual(self.store.settings(), before)
        valid = {"daily_search_limit": 1, "monthly_search_limit": 800, "daily_llm_limit": 80,
                 "watch_hour": 0, "auto_watch": True}
        status, _ = self.request("PATCH", "/api/settings", valid)
        self.assertEqual(status, 200)
        self.assertEqual(self.store.settings(), valid)

    def test_malformed_json_and_foreign_origin_have_no_effects(self):
        before = self.store.settings()
        for raw in ("{broken", "[]", "null"):
            status, _ = self.request("PATCH", "/api/settings", raw=raw)
            self.assertEqual(status, 400)
            self.assertEqual(self.store.settings(), before)
        status, _ = self.request("PATCH", "/api/settings", {"auto_watch": True}, origin="https://attacker.example")
        self.assertEqual(status, 403)
        self.assertEqual(self.store.settings(), before)
        status, _ = self.request("POST", "/api/run", origin="https://attacker.example")
        self.assertEqual(status, 403)
        self.app.team.start.assert_not_called()
        self.gmail.assert_not_called()

    def test_stable_ids_across_http_import_edit_and_restart(self):
        status, first = self.request("POST", "/api/applications", {"url": "https://example.com/jobs/123"})
        self.assertEqual(status, 201)
        status, duplicate = self.request("POST", "/api/applications", {"url": "https://www.example.com/jobs/123/?utm_source=test"})
        self.assertEqual(status, 200)
        id = first["application"]["id"]
        self.assertEqual(id, duplicate["application"]["id"])
        status, edited = self.request("PATCH", f"/api/applications/{id}", {"notes": "Keep me"})
        self.assertEqual(status, 200)
        self.assertEqual(edited["application"]["id"], id)
        restarted = server.Application(Store(self.store.path))
        self.assertEqual(restarted.store.application(id)["notes"], "Keep me")
        self.assertEqual(len(restarted.store.applications()), 1)

    def test_profile_change_blocks_approval_and_drafting(self):
        row = self.prepared()
        self.app.approve(row["id"])
        changed = copy.deepcopy(self.profile)
        changed["name"] = "Updated Student"
        self.store.set("profile", changed)
        with self.assertRaises(ValueError):
            self.app.approve(row["id"])
        with self.assertRaises(ValueError):
            self.app.make_draft(row["id"])
        self.gmail.assert_not_called()

    def test_uncertain_gmail_result_blocks_retry(self):
        row = self.prepared()
        self.app.approve(row["id"])
        self.gmail.side_effect = TimeoutError("Uncertain result")
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.app.make_draft(row["id"])
        self.gmail.assert_called_once()
        self.assertTrue(self.store.application(row["id"])["draft_pending"])

    def test_excel_export_route_writes_workbook_and_sync_route_reads_back(self):
        self.store.add_application({"url": "https://example.com/jobs/excel1", "title": "Data internship"})
        export_path = self.root / "suivi_candidatures.xlsx"
        status, body = self.request("POST", "/api/excel/export")
        self.assertEqual(status, 200)
        self.assertEqual(export_path, Path(body["path"]))
        self.assertTrue(export_path.is_file())
        # Manual edit: mark the offer "Rejeté" in the workbook.
        from openpyxl import load_workbook
        wb = load_workbook(export_path)
        ws = wb.active
        ws.cell(row=2, column=6, value="Rejeté")
        wb.save(export_path)
        status, body = self.request("POST", "/api/excel/sync")
        self.assertEqual(status, 200)
        self.assertEqual(body["updated"], [{"url": "https://example.com/jobs/excel1",
                                            "from": "discovered", "to": "rejected"}])
        self.assertEqual(self.store.applications()[0]["status"], "rejected")

    def test_voice_summary_route_returns_spoken_text(self):
        self.store.add_application({"url": "https://example.com/jobs/voice1", "title": "Data internship",
                                    "company": "Acme", "qualification_score": 92})
        fake = type("FakeProviders", (), {"text": lambda self, system, user: "Tu as une offre en cours."})()
        self.patch("Providers", lambda store: fake)
        status, body = self.request("GET", "/api/voice")
        self.assertEqual(status, 200)
        self.assertIn("text", body)
        self.assertIn("facts", body)
        self.assertEqual(body["text"], "Tu as une offre en cours.")
        self.assertEqual(body["facts"]["total_offres"], 1)

    def test_voice_summary_fallback_when_groq_unavailable(self):
        self.store.add_application({"url": "https://example.com/jobs/voice2", "title": "Stage actuariat"})
        real = server.Providers
        try:
            server.Providers = lambda store: type("Broken", (), {"text": lambda self, s, u: (_ for _ in ()).throw(
                ProviderError("quota"))})()
            summary = server.Application(self.store).voice_summary()
        finally:
            server.Providers = real
        self.assertIn("offre", summary["text"])
        self.assertEqual(summary["facts"]["total_offres"], 1)


if __name__ == "__main__":
    unittest.main()
