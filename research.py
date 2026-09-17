"""Broad discovery and source-grounded JobPosting extraction."""
import ipaddress
import json
import re
import socket
import unicodedata
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from storage import canonical_url, now


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKD", str(text)).lower() if not unicodedata.combining(c))


def search_plan(profile, companies, limit=12):
    criteria = profile["target_criteria"]
    countries = criteria["countries"]
    start = criteria["start_date"]
    plans = []
    # Round-robin destinations and vocabularies prevent the first source monopolizing the budget.
    year = re.search(r"20\d{2}", start)
    year = year[0] if year else str(datetime.now().year + 1)
    templates = ['stage actuariat {country}', 'stage data analyst {country}',
                 f'stage internship actuariat {{country}} {year}']
    for index, template in enumerate(templates):
        for country in countries:
            country = str(country)
            query = template.format(country=country)
            if "tunisi" in normalized(country):
                query = ['stage PFE data science Tunisie', 'site:tanitjobs.com stage PFE data Tunisie',
                         f'site:keejob.com stage data analyst Tunisie {year}'][index]
            elif index == 1 and "suisse" in normalized(country):
                query = 'data science internship Switzerland'
            elif index == 1 and "brux" in normalized(country):
                query = 'data analyst internship Brussels'
            plans.append({"region": country, "query": query, "source": "tavily"})
    for company in sorted(companies, key=lambda c: c.get("priority") != "high")[:4]:
        name = company.get("name", "").strip()
        if name:
            plans.append({"region": "Societe cible", "query": f'{name} stage internship actuariat data science {start} ' + " ".join(countries), "source": "tavily"})
    country_queries = plans[:len(countries) * len(templates)]
    company_queries = plans[len(countries) * len(templates):]
    company_slots = min(len(company_queries), max(0, limit - len(countries) * 2))
    # Reserve a quarter of the budget (rounded down) for direct browsing; the rest stays Tavily.
    browse = []
    for country in countries:
        key = normalized(str(country))
        browse.append({"region": str(country), "query": "stage actuariat data science",
                       "source": "browse", "site": "wttj"})
        if "france" in key:
            browse.append({"region": str(country), "query": "stage data",
                           "source": "browse", "site": "stage.fr"})
    browse_share = min(len(browse), max(0, limit // 4))
    selected = country_queries[:max(0, limit - browse_share - company_slots)] + company_queries[:company_slots]
    return selected + browse[:browse_share]


def is_listing(url):
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path.lower().rstrip("/")
    if any(host == domain or host.endswith('.' + domain) for domain in (
            "wikipedia.org", "youtube.com", "reddit.com", "facebook.com", "instagram.com")):
        return True
    if "glassdoor" in host and "-srch_" in path:
        return True
    if "efinancialcareers" in host and "/jobs/" in path and not re.search(r"\d{5,}", path):
        return True
    if any(piece in path for piece in ("/countries/", "/category/", "/intern-list", "/work/en/", "/work/fr/stages", "/program", "/wiki/", "/post/", "/formation", "/document/", "/blob/", "/metier/")):
        return True
    if path.endswith(("/interns", "/stages", "/internships.html", "/stagiaires.php", "/actuariat", "/stage-etudiants.html")) or "/c/" in path:
        return True
    if not path or path in {"/fr", "/en", "/jobs", "/careers", "/search", "/emploi", "/internships"}:
        return True
    if "linkedin.com" in host:
        return "/jobs/view/" not in path
    if "indeed.com" in host:
        return not ("/viewjob" in path or "/rc/clk" in path)
    if "welcometothejungle.com" in host:
        return "/jobs/" not in path
    if "stage.fr" in host:
        return "/job/" not in path
    if "apec.fr" in host:
        return "/offres-emploi/" not in path
    return any(s in path for s in ("/recherche", "/search", "/emploi-", "/jobs-in-"))


def candidate_from_result(result, query):
    try:
        url = canonical_url(result.get("url", ""))
    except ValueError:
        return None
    if is_listing(url):
        return None
    title = normalized(result.get("title", ""))
    if re.search(r"\b\d+\s+(?:jobs|emplois|offres)\b", title) or any(s in title for s in (
            "procedure des stages", "master in", "course", "internship opportunities", "career, responsibilities",
            "internships at", "jobs in ", "jobs at ", "jobs and internships", "internships summer", "actuary jobs in",
            "master actuariat", "ecole", "offres de stages", "rejoignez les differents", "stages en ", "internship programme |")):
        return None
    if title and not any(word in title for word in ("stage", "stagiaire", "intern", "pfe", "actu", "data", "analyst", "pricing", "risk")):
        return None
    return {"url": url, "title": result.get("title", "Offre a examiner"),
            "content": result.get("content", "")[:12000], "source": urlsplit(url).hostname,
            "search_query": query, "discovered_at": now(), "verification": "snippet_only",
            "offer_date": result.get("published_date", "")}


def pre_score(offer, profile):
    text = normalized(offer.get("title", "") + " " + offer.get("content", ""))
    words = profile["target_criteria"]["keywords_priority"]
    score = sum(3 for word in words if normalized(word) in text)
    score += 5 * any(word in text for word in ("stage", "internship", "stagiaire", "pfe"))
    score += 3 * ("2027" in text)
    score -= 8 * any(word in normalized(offer.get("title", "")) for word in ("senior", "head of", "manager", "directeur"))
    score += 8 * any(word in text for word in ("france", "paris", "lyon", "bruxelles", "brussels", "belgium", "suisse", "switzerland", "zurich", "geneve", "tunisie", "tunis"))
    score -= 25 * any(word in normalized(offer.get("title", "") + " " + offer.get("location", "")) for word in ("canada", "quebec", "montreal", "new york", "toronto", "united states", "london", "maroc"))
    return score


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("URL publique invalide")
    if parts.port not in {None, 80, 443}:
        raise ValueError("Port non autorise")
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal")):
        raise ValueError("Adresse locale refusee")
    addresses = socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise ValueError("Adresse privee refusee")
    return url


class JobHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.json_blocks = []
        self.scripts = []
        self.skip = 0
        self.in_json = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        if tag == "script" and "ld+json" in attrs.get("type", ""):
            self.in_json = True
            self.scripts = []
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "script" and self.in_json:
            try:
                self.json_blocks.append(json.loads("".join(self.scripts)))
            except ValueError:
                pass
            self.in_json = False
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if self.in_json:
            self.scripts.append(data)
        elif not self.skip and data.strip():
            self.text.append(data.strip())


def job_postings(value):
    if isinstance(value, list):
        for item in value:
            yield from job_postings(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if types == "JobPosting" or isinstance(types, list) and "JobPosting" in types:
            yield value
        for key in ("@graph", "mainEntity", "itemListElement", "item"):
            if key in value:
                yield from job_postings(value[key])


def clean_html(value):
    parser = JobHTML()
    parser.feed(str(value or ""))
    return unescape(" ".join(parser.text))


def fetch_offer(offer):
    result = dict(offer)
    url = offer["url"]
    try:
        with httpx.Client(timeout=15, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (compatible; LevelUpPersonalResearch/1.0)"}) as client:
            for _ in range(4):
                public_url(url)
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code in {404, 410}:
                        return {**result, "verification": "closed", "verification_note": "Page supprimee ou expiree", "verified_at": now()}
                    response.raise_for_status()
                    if "html" not in response.headers.get("content-type", ""):
                        raise ValueError("La page n'est pas une fiche HTML")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 2_000_000:
                            raise ValueError("Page trop volumineuse")
                        chunks.append(chunk)
                    html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                    break
            else:
                raise ValueError("Trop de redirections")
        parser = JobHTML()
        parser.feed(html)
        postings = list(job_postings(parser.json_blocks))
        if len(postings) == 1:
            job = postings[0]
            organization = job.get("hiringOrganization") or {}
            location = job.get("jobLocation") or {}
            location = location[0] if isinstance(location, list) and location else location
            address = location.get("address", {}) if isinstance(location, dict) else {}
            if not isinstance(address, dict):
                address = {"addressLocality": str(address)}
            result.update(title=job.get("title") or result["title"], content=clean_html(job.get("description"))[:18000],
                          company=organization.get("name", "") if isinstance(organization, dict) else str(organization),
                          location=", ".join(str(address[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k)),
                          offer_date=job.get("datePosted", ""), valid_through=job.get("validThrough", ""),
                          contract=job.get("employmentType", ""), verification="jobposting", verified_at=now())
            end = result.get("valid_through")
            if end:
                try:
                    if datetime.fromisoformat(str(end).replace("Z", "+00:00")).date() < datetime.now(timezone.utc).date():
                        result["verification"] = "closed"
                except ValueError:
                    pass
        elif len(postings) > 1:
            result.update(verification="listing", verification_note="Plusieurs offres sur cette page")
        else:
            text = " ".join(parser.text)
            if len(text) > 400 and not any(s in normalized(text[:1500]) for s in ("verify you are human", "access denied", "checking your browser", "captcha")):
                result.update(content=text[:16000], verification="page_read", verified_at=now())
            else:
                result.update(verification="snippet_only", verification_note="Lecture incomplete : verifier la fiche manuellement")
        emails = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", " ".join(parser.links), re.I)
        result["contact_candidates"] = sorted(set(emails))[:5]
    except (httpx.HTTPError, ValueError, OSError, KeyError) as error:
        result.update(verification="snippet_only", verification_note=f"Fiche inaccessible ({type(error).__name__}). Extrait seulement.")
    return result
