import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import candidate_from_result, fetch_offer, is_listing, public_url, search_plan


class ResearchTests(unittest.TestCase):
    def setUp(self):
        # Any accidental DNS access fails even when a test forgets its HTTP mock.
        self.dns = patch("research.socket.getaddrinfo", side_effect=AssertionError("Network forbidden"))
        self.dns.start()
        self.addCleanup(self.dns.stop)
        self.offer = {"url": "https://example.com/jobs/123", "title": "Search title", "content": "Original snippet"}

    def fetch(self, body="", status=200, validator=None, headers=None):
        calls = []

        def handle(request):
            calls.append(str(request.url))
            return httpx.Response(status, text=body, headers=headers or {"content-type": "text/html"})

        client = httpx.Client(transport=httpx.MockTransport(handle))
        with patch("research.httpx.Client", return_value=client), patch("research.public_url", side_effect=validator or (lambda url: url)):
            result = fetch_offer(self.offer)
        return result, calls

    def test_listing_and_individual_source_urls(self):
        pairs = [
            ("https://linkedin.com/jobs/search/?keywords=data", "https://linkedin.com/jobs/view/123"),
            ("https://fr.indeed.com/jobs?q=data", "https://fr.indeed.com/viewjob?jk=abc"),
            ("https://welcometothejungle.com/fr/jobs", "https://welcometothejungle.com/fr/companies/acme/jobs/data-intern_paris"),
            ("https://stage.fr/jobs/", "https://stage.fr/job/123/data-intern"),
        ]
        for listing, individual in pairs:
            with self.subTest(source=listing):
                self.assertTrue(is_listing(listing))
                self.assertFalse(is_listing(individual))
                self.assertIsNone(candidate_from_result({"url": listing}, "query"))
                self.assertIsNotNone(candidate_from_result({"url": individual}, "query"))

    def test_plan_covers_all_destinations_before_repeating(self):
        countries = ["France", "Bruxelles", "Suisse", "Tunisie"]
        profile = {"target_criteria": {"countries": countries, "start_date": "fin janvier 2027"}}
        plan = search_plan(profile, [], limit=12)
        self.assertEqual(len(plan), 12)
        # Round-robin keeps the first cycle intact: one query per destination.
        self.assertEqual([p["region"] for p in plan[:4]], countries)
        # The budget is shared: 9 Tavily queries and 3 direct-browsing plans.
        tavily = [p for p in plan if p.get("source", "tavily") == "tavily"]
        browse = [p for p in plan if p.get("source") == "browse"]
        self.assertEqual(len(tavily), 9)
        self.assertEqual(len(browse), 3)
        self.assertTrue(all(p.get("site") in {"wttj", "stage.fr"} for p in browse))
        self.assertTrue(any(p.get("site") == "stage.fr" and p["region"] == "France" for p in browse))
        # Year-tagged internship queries still appear in the Tavily slice.
        self.assertTrue(any("2027" in p["query"] for p in plan))
        # Each destination is covered by at least one plan.
        for country in countries:
            self.assertGreaterEqual(sum(p["region"] == country for p in plan), 1)
        self.assertEqual(len(search_plan(profile, [], limit=3)), 3)

    def job_html(self, **changes):
        job = {"@type": "JobPosting", "title": "Data intern", "description": "<p>Model risk &amp; pricing.</p>",
               "hiringOrganization": {"name": "Example Insurance"},
               "jobLocation": [{"address": {"addressLocality": "Bruxelles", "addressCountry": "BE"}}],
               "datePosted": "2026-09-15", "validThrough": "2099-01-01", "employmentType": "INTERN", **changes}
        return '<script type="application/ld+json">' + json.dumps({"@graph": [job]}) + '</script><a href="mailto:careers@example.com">Apply</a>'

    def test_jsonld_extracts_grounded_offer_and_contact(self):
        result, calls = self.fetch(self.job_html())
        self.assertEqual(calls, [self.offer["url"]])
        self.assertEqual(result["verification"], "jobposting")
        self.assertEqual(result["title"], "Data intern")
        self.assertEqual(result["company"], "Example Insurance")
        self.assertEqual(result["content"], "Model risk & pricing.")
        self.assertEqual(result["location"], "Bruxelles, BE")
        self.assertEqual(result["contract"], "INTERN")
        self.assertEqual(result["offer_date"], "2026-09-15")
        self.assertEqual(result["contact_candidates"], ["careers@example.com"])
        self.assertEqual(self.offer["title"], "Search title")

    def test_closed_http_and_expired_jsonld(self):
        for status in (404, 410):
            with self.subTest(status=status):
                result, _ = self.fetch(status=status)
                self.assertEqual(result["verification"], "closed")
        result, _ = self.fetch(self.job_html(validThrough="2000-01-01T00:00:00Z"))
        self.assertEqual(result["verification"], "closed")

    def test_blocked_page_keeps_snippet_only(self):
        for status, body in ((403, "Forbidden"), (200, "<p>Verify you are human " + "challenge " * 100 + "</p>")):
            with self.subTest(status=status):
                result, _ = self.fetch(body, status)
                self.assertEqual(result["verification"], "snippet_only")
                self.assertEqual(result["content"], "Original snippet")

    def test_multiple_postings_are_listing(self):
        result, _ = self.fetch(self.job_html() + self.job_html(title="Another job"))
        self.assertEqual(result["verification"], "listing")

    def test_private_url_rejected_before_http(self):
        self.offer["url"] = "http://localhost/jobs/123"
        result, calls = self.fetch(validator=public_url)
        self.assertEqual(calls, [])
        self.assertEqual(result["verification"], "snippet_only")

    def test_private_dns_and_mixed_dns_rejected(self):
        for addresses in (("127.0.0.1",), ("10.0.0.1",), ("::1",), ("8.8.8.8", "192.168.1.1")):
            with self.subTest(addresses=addresses):
                records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses]
                with patch("research.socket.getaddrinfo", return_value=records), self.assertRaises(ValueError):
                    public_url("https://example.com/jobs/123")

    def test_redirect_to_private_url_rejected_before_second_request(self):
        def validate(url):
            if "localhost" in url:
                return public_url(url)
            return url

        result, calls = self.fetch(status=302, headers={"location": "http://localhost/private"}, validator=validate)
        self.assertEqual(calls, [self.offer["url"]])
        self.assertEqual(result["verification"], "snippet_only")


if __name__ == "__main__":
    unittest.main()
