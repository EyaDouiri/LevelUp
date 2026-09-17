"""Offline orchestration regression tests; no real profile or credentials are used."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine
from contracts import Assessment, ApplicationDraft, Critique, evidence_catalog
from providers import ProviderError
from storage import BudgetExceeded, Store


def profile():
    return {
        "name": "Synthetic Candidate",
        "education": [{"degree": "Data science and actuarial engineering"}],
        "experience": [{"title": "Intern", "description": "Built a pricing model"}],
        "projects": [{"name": "Claims analysis", "description": "Analyzed synthetic claims"}],
        "skills": {"programming": ["Python", "SQL"]},
        "target_criteria": {"countries": ["France", "Belgium", "Switzerland", "Tunisia"],
                            "keywords_priority": ["actuariat", "data"],
                            "contract_types": ["stage"], "start_date": "2027-01-25"},
    }


def assessment(score=75):
    return dict(score=score, relevant=True, reasons=["Relevant project"],
                missing_information=["Visa unknown", "Start date unknown"], blockers=[],
                matches=[dict(requirement="Pricing", evidence_id="exp:0", explanation="Pricing model")],
                company="", location="", contract="", start_date="", language="fr")


def draft():
    return dict(email_subject="Application for internship", email_body=(
        "I am applying for your internship. My pricing project and Python experience "
        "are relevant to this role. I am available from January 25, 2027."),
        cv_angle="combine", evidence_ids=["exp:0", "project:0"],
        cv_plan=dict(experience_ids=["exp:0"], project_ids=["project:0"], skill_keys=["programming"]))


def result(slug, title="Stage data 2027"):
    return dict(url=f"https://example.org/offers/{slug}", title=title, content="Pricing with Python")


class FakeProviders:
    def __init__(self):
        self.searches = {}
        self.search_calls = []
        self.calls = []
        self.responses = {Assessment: lambda data: assessment(),
                          ApplicationDraft: lambda data: draft(),
                          Critique: lambda data: dict(passed=True, issues=[], suggestions=[])}

    def search(self, query):
        self.search_calls.append(query)
        value = self.searches.get(query, [])
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value() if callable(value) else value)

    def discover(self, plan):
        if plan.get("source", "tavily") == "tavily":
            return self.search(plan["query"])
        return []

    def structured(self, role, instruction, data, contract):
        self.calls.append((contract, copy.deepcopy(data)))
        value = self.responses[contract](data)
        if isinstance(value, Exception):
            raise value
        return contract.model_validate(value).model_dump()


class TeamTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "test.sqlite3")
        self.profile = profile()
        self.providers = FakeProviders()
        self.team = engine.Team(self.store, self.providers, verifier=lambda row: dict(row))
        self.plans = [{"region": "France", "query": "first"},
                      {"region": "Tunisia", "query": "second"}]
        self.documents = []
        for patcher in (
            patch.object(engine, "ROOT", self.root),
            patch.object(engine, "search_plan", return_value=self.plans),
            patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")),
            patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")),
            patch.object(engine.Providers, "search", side_effect=AssertionError("Real provider forbidden")),
            patch.object(engine.Providers, "structured", side_effect=AssertionError("Real provider forbidden")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(engine, "build_documents", side_effect=self.build_documents)
        self.build_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def build_documents(self, candidate, application, output):
        output.relative_to(self.root)
        output.mkdir(parents=True, exist_ok=True)
        self.documents.append((copy.deepcopy(candidate), copy.deepcopy(application), output))
        docs = []
        for name in ("cv.pdf", "letter.pdf"):
            path = output / name
            path.write_bytes(b"%PDF-1.4\n% synthetic fixture\n%%EOF\n")
            docs.append({"name": name, "path": str(path)})
        return docs

    def execute(self, kind="hunt", **payload):
        run = self.store.new_run(kind, {"profile": self.profile, **payload})
        self.team.execute(run["id"])
        return self.store.run(run["id"])

    def offer(self):
        return self.store.add_application({**result("prepare"), "verification": "snippet_only"})[0]

    def calls(self, contract):
        return [data for schema, data in self.providers.calls if schema is contract]

    def test_search_failure_preserves_discoveries_and_retries_only_failed_query(self):
        self.providers.searches = {"first": [result("saved")], "second": ProviderError("Search unavailable")}
        run = self.execute()
        self.assertEqual(run["status"], "partial")
        self.assertEqual(len(self.store.applications()), 1)
        self.assertEqual(run["checkpoint"]["searched"], [0])
        self.providers.searches["second"] = [result("recovered")]
        with patch.object(self.team, "_launch", side_effect=self.team.execute):
            self.team.resume(run["id"])
        self.assertEqual(self.providers.search_calls, ["first", "second", "second"])
        self.assertEqual(len(self.store.applications()), 2)
        self.assertEqual(self.store.run(run["id"])["status"], "completed")

    def test_analyst_failure_keeps_offer_unassessed_for_retry(self):
        self.providers.searches["first"] = [result("saved")]
        self.providers.responses[Assessment] = lambda data: ProviderError("Analyst unavailable")
        run = self.execute()
        self.assertEqual(run["status"], "partial")
        self.assertEqual(run["checkpoint"]["assessed"], [])
        row = self.store.applications()[0]
        self.assertEqual(row["status"], "discovered")
        self.assertIsNone(row["qualification_score"])

    def test_shortlist_ranks_all_assessments_before_limiting(self):
        self.providers.searches["first"] = [result("weak", "Stage actuariat data 2027"), result("best", "Analyst")]
        self.providers.responses[Assessment] = lambda data: assessment(95 if data["offer"]["url"].endswith("best") else 30)
        run = self.execute(max=1)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(self.calls(Assessment)), 2)
        selected = [r for r in self.store.applications() if r["status"] == "shortlisted"]
        self.assertEqual([r["url"] for r in selected], [result("best")["url"]])

    def test_analyst_receives_full_evidence_and_preserves_unknowns(self):
        self.providers.searches["first"] = [result("unknown")]
        self.execute()
        data = self.calls(Assessment)[0]
        self.assertEqual(data["profile"], self.profile)
        self.assertEqual(data["evidence"], evidence_catalog(self.profile))
        row = self.store.applications()[0]
        self.assertEqual(row["assessment"]["blockers"], [])
        self.assertEqual(row["company"], "")
        self.assertEqual(row["location"], "")
        self.assertIn("Visa unknown", row["missing_information"])
        self.assertIn("Start date unknown", row["missing_information"])
        self.assertTrue(any("provisoire" in value for value in row["missing_information"]))
        self.assertEqual(row["status"], "shortlisted")

    def test_fabricated_analyst_evidence_cannot_shortlist(self):
        bad = assessment()
        bad["matches"][0]["evidence_id"] = "exp:999"
        self.providers.responses[Assessment] = lambda data: bad
        self.providers.searches["first"] = [result("fabricated")]
        run = self.execute()
        self.assertEqual(run["status"], "partial")
        self.assertTrue(any("preuve" in error for error in run["errors"]))
        self.assertNotIn("assessment", self.store.applications()[0])
        self.assertEqual(self.store.applications()[0]["status"], "discovered")

    def test_writer_rejects_fabricated_evidence_before_documents(self):
        bad = draft()
        bad["evidence_ids"] = ["project:999"]
        self.providers.responses[ApplicationDraft] = lambda data: bad
        run = self.execute("prepare", application_id=self.offer()["id"])
        self.assertEqual(run["status"], "failed")
        self.assertTrue(any("preuve" in error for error in run["errors"]))
        self.assertEqual(self.calls(Critique), [])
        self.build_mock.assert_not_called()

    def test_critic_feedback_reaches_next_writer_and_outputs_await_approval(self):
        criticism = dict(passed=False, issues=["Clarify availability"], suggestions=["Use exact date"])
        critiques = iter([criticism, dict(passed=True, issues=[], suggestions=[])])
        self.providers.responses[Critique] = lambda data: next(critiques)
        self.providers.responses[ApplicationDraft] = lambda data: {
            **draft(), "email_subject": "Revised application" if data["critic_feedback"] else "Initial application"}
        row = self.offer()
        run = self.execute("prepare", application_id=row["id"])
        self.assertEqual(run["status"], "completed", run["errors"])
        writers = self.calls(ApplicationDraft)
        self.assertEqual(len(writers), 2)
        self.assertIsNone(writers[0]["critic_feedback"])
        self.assertEqual(writers[1]["critic_feedback"], criticism)
        self.assertEqual(writers[1]["previous_draft"]["email_subject"], "Initial application")
        saved = self.store.application(row["id"])
        self.assertEqual(saved["email_subject"], "Revised application")
        self.assertEqual(saved["status"], "review_needed")
        self.assertIsNone(saved["approved_at"])
        self.assertNotIn("gmail_draft_id", saved)
        self.assertEqual([c for c, _ in self.providers.calls],
                         [Assessment, ApplicationDraft, Critique, ApplicationDraft, Critique])
        self.build_mock.assert_called_once()
        expected = self.root / "data" / "documents" / row["id"] / run["id"][:12]
        self.assertEqual(self.documents[0][2], expected)
        for path, artifact in zip(saved["document_paths"], saved["artifacts"]):
            self.assertEqual(Path(path).parent, expected)
            self.assertTrue(Path(path).is_file())
            self.assertEqual(artifact["url"], f"/api/artifacts/{row['id']}/{run['id'][:12]}/{Path(path).name}")

    def test_persistent_criticism_is_bounded_and_never_approves(self):
        self.providers.responses[Critique] = lambda data: dict(passed=False, issues=["Unresolved"], suggestions=["Fix"])
        row = self.offer()
        self.execute("prepare", application_id=row["id"])
        self.assertEqual(len(self.calls(ApplicationDraft)), 2)
        self.assertEqual(len(self.calls(Critique)), 2)
        saved = self.store.application(row["id"])
        self.assertFalse(saved["review"]["passed"])
        self.assertEqual(saved["status"], "review_needed")
        self.assertIsNone(saved["approved_at"])

    def test_cancel_during_search_saves_results_and_resume_skips_completed_query(self):
        run = self.store.new_run("hunt", {"profile": self.profile})
        def cancel_after_search():
            self.team.cancel(run["id"])
            return [result("before-cancel")]
        self.providers.searches = {"first": cancel_after_search, "second": [result("after-resume")]}
        self.team.execute(run["id"])
        saved = self.store.run(run["id"])
        self.assertEqual(saved["status"], "cancelled")
        self.assertEqual(saved["checkpoint"]["searched"], [0])
        self.assertEqual(len(self.store.applications()), 1)
        with patch.object(self.team, "_launch", side_effect=self.team.execute):
            self.team.resume(run["id"])
        self.assertEqual(self.providers.search_calls, ["first", "second"])
        self.assertEqual(self.store.run(run["id"])["status"], "completed")

    def test_server_recovery_retains_checkpoint_and_resumes_synchronously(self):
        row = self.offer()
        run = self.store.new_run("hunt", {"profile": self.profile})
        checkpoint = dict(plan=self.plans, searched=[0], candidates=[row["id"]], assessed=[])
        self.store.update_run(run["id"], status="running", checkpoint=checkpoint)
        recovered = Store(self.root / "test.sqlite3")
        recovered.recover()
        self.assertEqual(recovered.run(run["id"])["status"], "interrupted")
        self.assertEqual(recovered.run(run["id"])["checkpoint"], checkpoint)
        team = engine.Team(recovered, self.providers, verifier=lambda item: dict(item))
        with patch.object(team, "_launch", side_effect=team.execute):
            team.resume(run["id"])
        self.assertEqual(self.providers.search_calls, ["second"])
        self.assertEqual(recovered.run(run["id"])["status"], "completed")
        self.assertEqual(recovered.application(row["id"])["status"], "shortlisted")

    def test_budget_pause_retains_already_discovered_offers(self):
        self.providers.searches = {"first": [result("saved")], "second": BudgetExceeded("Daily limit")}
        run = self.execute()
        self.assertEqual(run["status"], "budget_paused")
        self.assertEqual(run["checkpoint"]["searched"], [0])
        self.assertEqual(len(self.store.applications()), 1)


if __name__ == "__main__":
    unittest.main()
