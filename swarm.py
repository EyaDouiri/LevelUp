"""Swarm local d'agents pour la recherche et le suivi des candidatures.

Chaque agent reçoit et retourne un contexte dictionnaire partagé. L'orchestrateur
reste déterministe : aucun agent ne peut envoyer un email automatiquement.
"""
from dataclasses import dataclass, field
import time
from dedup import filter_new_offers, init_db, record_application
from save_output import save_to_csv
from search_offers import search_offers, search_source_with_llm, source_name
from companies import load_companies
from tailor_application import qualify_offer, tailor_application


@dataclass
class SwarmContext:
    profile: dict
    max_offers: int | None = None
    use_gmail: bool = False
    offers: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Agent:
    name = "agent"

    def run(self, context: SwarmContext) -> SwarmContext:
        raise NotImplementedError


class SourceSearchAgent(Agent):
    def __init__(self, source: str, domain: str):
        self.source = source
        self.domain = domain
        self.name = f"{source.lower().replace(' ', '-')}-agent"

    def run(self, context: SwarmContext) -> SwarmContext:
        print(f"[{self.name}] Agent LLM + tool Tavily sur {self.source}...")
        offers = search_source_with_llm(context.profile, self.source, self.domain, max_results=5)
        from urllib.parse import urlparse

        for offer in offers:
            offer["company"] = offer.get("company") or urlparse(offer["url"]).netloc.replace("www.", "")
        context.offers.extend(offers)
        print(f"[{self.name}] {len(offers)} offre(s) trouvée(s).")
        return context


class MultiSourceSearchAgent(Agent):
    name = "multi-source-search-agent"

    def run(self, context: SwarmContext) -> SwarmContext:
        sources = [
            ("LinkedIn", "linkedin.com/jobs/view"),
            ("Indeed", "fr.indeed.com"),
            ("Welcome to the Jungle", "welcometothejungle.com"),
            ("France Travail", "candidat.francetravail.fr"),
            ("Stage.fr", "stage.fr/job"),
            ("Apec", "apec.fr/candidat/recherche-emploi.html"),
        ]
        for source, domain in sources:
            SourceSearchAgent(source, domain).run(context)
        return context


class TargetCompaniesAgent(Agent):
    name = "target-companies-agent"

    def run(self, context: SwarmContext) -> SwarmContext:
        companies = load_companies()
        if not companies:
            return context
        for company in companies:
            name = (company.get("name") or "").strip()
            if not name:
                continue
            print(f"[{self.name}] Recherche d'offres chez {name}...")
            offers = search_offers(context.profile, max_results_per_query=5, max_age_days=60, company=name)
            for offer in offers:
                offer["company"] = name
                offer["source"] = offer.get("source") or source_name(offer["url"])
            context.offers.extend(offers)
            print(f"[{self.name}] {len(offers)} offre(s) pour {name}.")
        return context


class DeduplicationAgent(Agent):
    name = "dedup-agent"

    def run(self, context: SwarmContext) -> SwarmContext:
        context.offers = filter_new_offers(context.offers)
        if context.max_offers:
            context.offers = context.offers[: context.max_offers]
        print(f"[dedup-agent] {len(context.offers)} offres à traiter.")
        return context


class QualificationAgent(Agent):
    name = "qualification-agent"

    def run(self, context: SwarmContext) -> SwarmContext:
        qualified = []
        for offer in context.offers:
            try:
                decision = qualify_offer(context.profile, offer)
                offer["qualification_score"] = decision.get("score", 0)
                offer["qualification_reasons"] = decision.get("reasons", [])
                if decision.get("relevant", True) and float(decision.get("score", 0)) >= 45:
                    qualified.append(offer)
            except Exception as error:
                context.errors.append(f"Qualification {offer.get('title', '')}: {error}")
        context.offers = qualified
        print(f"[qualification-agent] {len(qualified)} offre(s) pertinentes.")
        return context


class TailoringAgent(Agent):
    name = "tailoring-agent"

    def run(self, context: SwarmContext) -> SwarmContext:
        for index, offer in enumerate(context.offers, 1):
            print(f"[tailoring-agent] [{index}/{len(context.offers)}] {offer['title'][:60]}")
            try:
                tailored = tailor_application(context.profile, offer)
                context.rows.append({**offer, **tailored, "status": "prepared"})
            except Exception as error:
                if "429" in str(error) or "rate_limit" in str(error):
                    print("[tailoring-agent] Limite Groq atteinte, nouvelle tentative dans 10 secondes...")
                    time.sleep(10)
                    try:
                        tailored = tailor_application(context.profile, offer)
                        context.rows.append({**offer, **tailored, "status": "prepared"})
                        continue
                    except Exception as retry_error:
                        error = retry_error
                message = f"{offer['title']}: {error}"
                context.errors.append(message)
                print(f"[tailoring-agent] Erreur: {error}")
        return context


class GmailAgent(Agent):
    name = "gmail-agent"

    def run(self, context: SwarmContext) -> SwarmContext:
        if not context.use_gmail:
            return context
        from gmail_draft import create_draft

        for row in context.rows:
            try:
                row["gmail_draft_id"] = create_draft(
                    to="",
                    subject=row.get("email_subject", ""),
                    body=row.get("email_body", ""),
                )
                row["status"] = "draft_created"
                print(f"[gmail-agent] Brouillon créé : {row['gmail_draft_id']}")
            except Exception as error:
                context.errors.append(f"Gmail {row.get('title', '')}: {error}")
                print(f"[gmail-agent] Erreur: {error}")
        return context


class PersistenceAgent(Agent):
    name = "persistence-agent"

    def run(self, context: SwarmContext) -> SwarmContext:
        for row in context.rows:
            record_application(
                url=row["url"],
                company=row.get("company", ""),
                title=row.get("title", ""),
                cv_variant=row.get("cv_angle", ""),
                email_subject=row.get("email_subject", ""),
                email_body=row.get("email_body", ""),
                gmail_draft_id=row.get("gmail_draft_id", ""),
                offer_date=row.get("published_date", ""),
                source=row.get("source", ""),
                qualification_score=row.get("qualification_score", ""),
                qualification_reasons="; ".join(row.get("qualification_reasons", [])) if isinstance(row.get("qualification_reasons", []), list) else row.get("qualification_reasons", ""),
                status=row.get("status", "prepared"),
            )
        path = save_to_csv(context.rows)
        print(f"[persistence-agent] CSV : {path}")
        return context


class JobSwarm:
    def __init__(self, agents: list[Agent] | None = None):
        self.agents = agents or [
            MultiSourceSearchAgent(),
            TargetCompaniesAgent(),
            DeduplicationAgent(),
            QualificationAgent(),
            TailoringAgent(),
            GmailAgent(),
            PersistenceAgent(),
        ]

    def run(self, context: SwarmContext) -> SwarmContext:
        init_db()
        for agent in self.agents:
            print(f"\n== {agent.name} ==")
            context = agent.run(context)
        return context


def run_swarm(profile: dict, max_offers: int | None = None, use_gmail: bool = False):
    return JobSwarm().run(SwarmContext(profile, max_offers, use_gmail))
