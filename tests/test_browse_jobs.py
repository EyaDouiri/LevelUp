import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browse_jobs import _clean_anchors, _wttj_json, stage_fr_jobs, wttj_jobs

JOBS_HTML = """
<html><body>
<a href="/job/123/data-intern-paris"><h3>Data intern Paris</h3></a>
<a href="/job/456/actuary-brussels">Actuary Brussels</a>
<a href="/jobs">Tous les stages</a>
<a href="/job/123/data-intern-paris">Doublon a ne pas re-ajouter</a>
<a href="https://fr.wikipedia.org/wiki/Stage">Wikipedia a ignorer</a>
</body></html>
"""


class BrowseJobsTests(unittest.TestCase):
    def response(self, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", "https://example.com"), **kwargs)

    def test_stage_fr_parses_individual_links_only(self):
        with patch("browse_jobs.httpx.get", return_value=self.response(text=JOBS_HTML)):
            offers = stage_fr_jobs()
        self.assertEqual([o["url"] for o in offers],
                         ["https://www.stage.fr/job/123/data-intern-paris", "https://www.stage.fr/job/456/actuary-brussels"])
        self.assertEqual(offers[0]["title"], "Data intern Paris")

    def test_stage_fr_network_error_returns_empty_list(self):
        with patch("browse_jobs.httpx.get", side_effect=httpx.ConnectError("boom")):
            self.assertEqual(stage_fr_jobs(), [])

    def test_wttj_json_normalizes_company_and_slug_links(self):
        payload = {"data": [
            {"name": "Data intern", "slug": "data-intern_paris",
             "organization": {"slug": "acme"}},
            {"name": "ML intern", "link": "https://www.welcometothejungle.com/fr/companies/acme/jobs/ml-intern_bxl", "organization": {}},
        ]}
        with patch("browse_jobs.httpx.get", return_value=self.response(json=payload)):
            offers = _wttj_json("stage", limit=10)
        self.assertEqual(offers[0]["url"], "https://www.welcometothejungle.com/fr/companies/acme/jobs/data-intern_paris")
        self.assertEqual(offers[1]["url"], "https://www.welcometothejungle.com/fr/companies/acme/jobs/ml-intern_bxl")

    def test_wttj_json_failure_is_ignored(self):
        with patch("browse_jobs.httpx.get", side_effect=httpx.ConnectError("boom")):
            self.assertEqual(_wttj_json("stage", limit=10), [])

    def test_wttj_jobs_falls_back_without_crashing(self):
        with patch("browse_jobs._wttj_json", return_value=[]), \
                patch("browse_jobs._wttj_playwright", return_value=[]):
            self.assertEqual(wttj_jobs("stage"), [])

    def test_clean_anchors_dedups_and_respects_limit(self):
        html = '<a href="/job/1/a">A</a><a href="/job/2/b">B</a><a href="/job/3/c">C</a>'
        found = _clean_anchors(html, "https://www.stage.fr/jobs", "/job/", limit=2)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0]["title"], "A")


if __name__ == "__main__":
    unittest.main()