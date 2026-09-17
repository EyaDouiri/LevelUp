"""
Module 1 — Recherche d'offres via Tavily.

Tavily est une API de recherche pensée pour les agents LLM (contrairement à
une recherche Google classique, elle renvoie des résultats déjà nettoyés,
avec extraits de contenu). Gratuit jusqu'à 1000 requêtes/mois.
"""
import os
import json
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from tavily import TavilyClient


def get_client():
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY manquante dans .env")
    return TavilyClient(api_key=api_key)


class _JobLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current_href = ""
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current_href = dict(attrs).get("href", "")
            self.current_text = []

    def handle_data(self, data):
        if self.current_href:
            self.current_text.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "a" and self.current_href:
            self.links.append((self.current_href, " ".join(self.current_text)))
            self.current_href = ""


def _expand_listing(url: str) -> list[dict]:
    """Extracte les offres individuelles d'une page Stage.fr connue comme liste."""
    parsed = urlparse(url)
    if parsed.netloc not in {"stage.fr", "www.stage.fr"} or "/job/" in parsed.path:
        return []
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=8) as response:
            html = response.read().decode("utf-8", errors="ignore")
        parser = _JobLinkParser()
        parser.feed(html)
        return [
            {"url": urljoin(url, href), "title": title}
            for href, title in parser.links
            if "/job/" in href and title
        ]
    except Exception as error:
        print(f"[search_offers] Impossible d'ouvrir la liste {url}: {error}")
        return []


def _is_listing_url(url: str) -> bool:
    """Détecte les pages liste / recherche / accueil (à ne PAS enregistrer comme offre)."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/").lower()
    if "linkedin.com" in host:
        return path.startswith("/jobs") and "/jobs/view/" not in path
    if "indeed.com" in host:
        return path.startswith("/jobs") and "/viewjob" not in path and "/rc/clk" not in path
    if "iagora.com" in host:
        return "stages" in path or "emplois" in path
    if "jooble.org" in host:
        return path == "/emploi" or (path.startswith("/emploi-") and not path.rstrip("/").split("-")[-1].isdigit())
    if "efinancialcareers" in host:
        return path.startswith("/jobs")
    if "stage.fr" in host:
        return path.startswith("/jobs") and "/job/" not in path
    if "hi-interns.com" in host:
        return path in ("/internships", "", "/jobs", "/")
    if "apec.fr" in host:
        return "recherche" in path
    if "francetravail.fr" in host:
        return "recherche" in path and "/detail/" not in path
    if "welcometothejungle.com" in host:
        return path in ("", "/") or "/jobs/" not in path
    return path in {"/jobs", "/internships", "/search", "/job-search"} or ("/jobs/" in path and "/job/" not in path)


def _is_offer_url(url: str) -> bool:
    """Évite d'enregistrer une page d'accueil ou une recherche comme une offre."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "linkedin.com" in host:
        return "/jobs/view/" in path
    if "indeed.com" in host:
        return "/viewjob" in path or "/rc/clk" in path
    if "welcometothejungle.com" in host:
        return "/jobs/" in path
    if "francetravail.fr" in host:
        return "/offres/recherche/detail/" in path or "/offre/" in path
    if "stage.fr" in host:
        return "/job/" in path
    if "apec.fr" in host:
        return "/offres-emploi/" in path
    return not _is_listing_url(url)


def source_name(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    names = {
        "linkedin.com": "LinkedIn",
        "indeed.com": "Indeed",
        "welcometothejungle.com": "Welcome to the Jungle",
        "francetravail.fr": "France Travail",
        "stage.fr": "Stage.fr",
        "apec.fr": "Apec",
        "iagora.com": "iAgora",
        "jooble.org": "Jooble",
        "hi-interns.com": "HiInterns",
        "efinancialcareers.fr": "eFinancialCareers",
    }
    return next((name for domain, name in names.items() if host.endswith(domain)), host)


def search_source_with_llm(profile: dict, source: str, domain: str, max_results: int = 5) -> list[dict]:
    """Fait chercher un agent LLM via le tool Tavily, puis valide ses URLs."""
    from groq import Groq

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    tool = {
        "type": "function",
        "function": {
            "name": "search_tavily",
            "description": "Recherche des pages web publiques correspondant à une requête.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
    system = (
        f"Tu es l'agent LLM de recherche {source}. Utilise obligatoirement l'outil Tavily. "
        f"Cherche uniquement sur {domain}. Retourne ensuite uniquement un JSON valide, "
        "une liste d'offres individuelles avec title, url, content et published_date. "
        "Ne fabrique jamais d'URL : utilise uniquement les URLs reçues de Tavily. "
        "Ignore les pages de recherche, listes, accueil et résultats génériques."
    )
    user = json.dumps({
        "profil": profile["target_criteria"],
        "mission": f"Trouver au maximum {max_results} offres de stage pertinentes, publiées depuis 14 jours, en France/Suisse/Espagne.",
    }, ensure_ascii=False)
    first = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "search_tavily"}},
        temperature=0,
    )
    message = first.choices[0].message
    if not message.tool_calls:
        return []
    tool_call = message.tool_calls[0]
    arguments = json.loads(tool_call.function.arguments)
    raw = get_client().search(query=arguments["query"], search_depth="advanced", max_results=max_results, include_answer=False)
    raw_results = [item for item in raw.get("results", []) if item.get("url")]
    if not raw_results:
        return []
    select_system = (
        "Tu es un sélecteur d'offres. À partir de la liste de résultats fournie, "
        "retourne UNIQUEMENT un JSON valide : une liste d'offres individuelles avec "
        "title, url, content et published_date. Ignore les pages de recherche, listes "
        "et accueil. Ne fabrique jamais d'URL : utilise uniquement celles fournies."
    )
    follow_up = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        messages=[
            {"role": "system", "content": select_system},
            {"role": "user", "content": json.dumps(
                {"profil": profile["target_criteria"], "resultats": raw_results}, ensure_ascii=False)},
        ],
        temperature=0,
    )
    text = (follow_up.choices[0].message.content or "").strip().strip("`")
    if text.startswith("json"):
        text = text[4:].strip()
    try:
        selected = json.loads(text)
    except json.JSONDecodeError:
        selected = raw_results
    allowed = {item["url"]: item for item in raw_results}
    offers = []
    for item in selected if isinstance(selected, list) else []:
        url = item.get("url")
        if url not in allowed or not _is_offer_url(url):
            continue
        original = allowed[url]
        offers.append({"title": item.get("title") or original.get("title", ""), "url": url, "content": item.get("content") or original.get("content", "")[:800], "query_used": arguments["query"], "published_date": item.get("published_date") or original.get("published_date", ""), "source": source})
    return offers


def search_offers(profile: dict, max_results_per_query: int = 8, max_age_days: int = 14, source_domain: str | None = None, company: str | None = None) -> list[dict]:
    """
    Construit plusieurs requêtes ciblées à partir du profil (pays x mots-clés)
    et retourne une liste dédupliquée d'offres candidates.

    Si `company` est fourni, les requêtes sont centrées sur cette société
    (utilisé par l'agent "Mes sociétés cibles").
    Chaque offre : {title, url, content (extrait), query_used}
    """
    client = get_client()
    criteria = profile["target_criteria"]
    countries = criteria["countries"]
    keywords = criteria["keywords_priority"]

    queries = []
    if company:
        queries.append(f"{company} stage PFE actuariat data science assurance France 2026")
        queries.append(f"{company} internship actuarial insurance data science 2026 France")
    else:
        for country in countries:
            # Une requête large par pays, orientée PFE/stage actuariat/data insurance
            queries.append(
                f"stage PFE actuariat OR data science assurance janvier 2027 {country}"
            )
            queries.append(
                f"internship actuarial OR insurance data science 2027 {country}"
            )
    if source_domain:
        queries = [f"site:{source_domain} stage actuariat data science assurance janvier 2027 France"]

    seen_urls = set()
    results = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    for q in queries:
        try:
            resp = client.search(
                query=q,
                search_depth="advanced",
                max_results=max_results_per_query,
                include_answer=False,
            )
        except Exception as e:
            print(f"[search_offers] Erreur sur la requête '{q}': {e}")
            continue

        for item in resp.get("results", []):
            url = item.get("url")
            if not url or url in seen_urls:
                continue
            if _is_listing_url(url):
                for expanded in _expand_listing(url):
                    if expanded["url"] not in seen_urls:
                        seen_urls.add(expanded["url"])
                        results.append({"title": expanded["title"], "url": expanded["url"], "content": item.get("content", "")[:800], "query_used": q, "published_date": "", "source": source_name(expanded["url"])})
                continue
            if source_domain and not _is_offer_url(url):
                continue
            published_date = item.get("published_date") or item.get("date") or ""
            if published_date:
                try:
                    parsed_date = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
                    if parsed_date.tzinfo is None:
                        parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                    if parsed_date < cutoff:
                        continue
                except ValueError:
                    pass
            seen_urls.add(url)
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": url,
                    "content": item.get("content", "")[:800],
                    "query_used": q,
                    "published_date": published_date,
                    "source": source_name(url),
                }
            )

    return results


if __name__ == "__main__":
    import json
    from dotenv import load_dotenv

    load_dotenv()
    with open("profile.json", encoding="utf-8") as f:
        profile = json.load(f)

    offers = search_offers(profile)
    print(f"{len(offers)} offres trouvées.")
    for o in offers[:5]:
        print("-", o["title"], "|", o["url"])
