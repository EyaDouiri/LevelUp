import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import BudgetExceeded, Store, canonical_url


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")

    def concurrent(self, functions):
        barrier = threading.Barrier(len(functions))

        def invoke(fn):
            barrier.wait(timeout=10)
            return fn()

        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            return list(pool.map(invoke, functions))

    def test_canonical_urls(self):
        cases = {
            "http://www.example.com/job/123/?utm_source=x&b=2&a=1#apply":
                "https://example.com/job/123?a=1&b=2",
            "https://www.linkedin.com/jobs/view/data-intern-123456/?trackingId=x":
                "https://linkedin.com/jobs/view/123456",
            "https://fr.indeed.com/rc/clk?vjk=abc&source=x":
                "https://fr.indeed.com/viewjob?jk=abc",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonical_url(raw), expected)
                self.assertEqual(canonical_url(expected), expected)

    def test_same_run_dedup_preserves_first_record(self):
        first, added = self.store.add_application({"url": "https://example.com/job/1", "notes": "keep"})
        duplicate, again = self.store.add_application({"url": "https://www.example.com/job/1/?utm_source=x", "notes": "overwrite"})
        self.assertTrue(added)
        self.assertFalse(again)
        self.assertEqual(first["id"], duplicate["id"])
        self.assertEqual(duplicate["notes"], "keep")
        self.assertEqual(len(self.store.applications()), 1)

    def test_concurrent_inserts_deduplicate(self):
        results = self.concurrent([
            lambda i=i: self.store.add_application({"url": f"https://example.com/job/1?utm_source={i}"})
            for i in range(8)
        ])
        self.assertEqual(sum(added for _, added in results), 1)
        self.assertEqual(len({row["id"] for row, _ in results}), 1)
        self.assertEqual(len(self.store.applications()), 1)

    def test_concurrent_notes_and_status_updates_are_retained(self):
        row, _ = self.store.add_application({"url": "https://example.com/job/1"})
        self.concurrent([
            lambda: self.store.update_application(row["id"], {"notes": "Call recruiter"}),
            lambda: self.store.update_application(row["id"], {"status": "shortlisted"}),
        ])
        saved = self.store.application(row["id"])
        self.assertEqual(saved["notes"], "Call recruiter")
        self.assertEqual(saved["status"], "shortlisted")

    def test_only_one_concurrent_active_run(self):
        def create():
            try:
                return self.store.new_run()
            except ValueError:
                return None

        results = self.concurrent([create] * 8)
        self.assertEqual(sum(result is not None for result in results), 1)
        run = next(result for result in results if result)
        for status in ("running", "cancelling"):
            self.store.update_run(run["id"], status=status)
            with self.assertRaises(ValueError):
                self.store.new_run()
        self.store.update_run(run["id"], status="completed")
        self.assertEqual(self.store.new_run()["status"], "queued")

    def test_concurrent_budget_reservations_are_atomic(self):
        for kind, settings, usage_key in (
            ("search", {"daily_search_limit": 3, "monthly_search_limit": 100}, "search_today"),
            ("llm", {"daily_llm_limit": 3}, "llm_today"),
        ):
            with self.subTest(kind=kind):
                self.store.set("settings", settings)

                def reserve():
                    try:
                        self.store.reserve(kind)
                        return True
                    except BudgetExceeded:
                        return False

                self.assertEqual(sum(self.concurrent([reserve] * 8)), 3)
                self.assertEqual(self.store.usage()[usage_key], 3)

    def test_monthly_budget_rejection_does_not_charge(self):
        self.store.set("settings", {"daily_search_limit": 100, "monthly_search_limit": 2})
        self.store.reserve("search", 2)
        with self.assertRaises(BudgetExceeded):
            self.store.reserve("search")
        self.assertEqual(self.store.usage()["search_month"], 2)

    def test_negative_reservation_cannot_refund_budget(self):
        self.store.reserve("search", 2)
        with self.assertRaises(ValueError):
            self.store.reserve("search", -1)
        self.assertEqual(self.store.usage()["search_today"], 2)

    def test_recover_preserves_checkpoint_and_payload(self):
        run = self.store.new_run(payload={"regions": ["France"]})
        checkpoint = {"completed_queries": ["query-1"], "application_ids": ["abc"]}
        self.store.update_run(run["id"], status="running", checkpoint=checkpoint)
        restarted = Store(self.store.path)
        restarted.recover()
        saved = restarted.run(run["id"])
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["checkpoint"], checkpoint)
        self.assertEqual(saved["payload"], run["payload"])
        restarted.recover()
        self.assertEqual(restarted.run(run["id"]), saved)
        restarted.new_run()


if __name__ == "__main__":
    unittest.main()
