"""Déduplication et registre local des candidatures via CSV uniquement."""
import csv
from pathlib import Path
from datetime import datetime

REGISTRY_PATH = Path(__file__).parent / "data" / "applications.csv"
FIELDNAMES = ["url", "company", "title", "status", "cv_variant", "created_at",
              "email_subject", "email_body", "gmail_draft_id", "offer_date", "source",
              "qualification_score", "qualification_reasons", "notes"]


def init_db():
    REGISTRY_PATH.parent.mkdir(exist_ok=True)
    if not REGISTRY_PATH.exists():
        with REGISTRY_PATH.open("w", encoding="utf-8-sig", newline="") as file:
            csv.DictWriter(file, fieldnames=FIELDNAMES).writeheader()
        return
    with REGISTRY_PATH.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        existing_fields = reader.fieldnames or []
    if existing_fields != FIELDNAMES:
        with REGISTRY_PATH.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows({field: row.get(field, "") for field in FIELDNAMES} for row in rows)


def already_applied(url: str, company: str | None = None) -> bool:
    """Vérifie si cette URL d'offre a déjà été traitée."""
    init_db()
    with REGISTRY_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    return any(row.get("url") == url for row in rows)


def record_application(url: str, company: str, title: str, cv_variant: str = "", **details):
    init_db()
    fieldnames = FIELDNAMES
    if already_applied(url):
        return
    row = {field: "" for field in fieldnames}
    row.update({"url": url, "company": company, "title": title,
                "status": details.get("status", "prepared"), "cv_variant": cv_variant,
                "created_at": datetime.now().isoformat(),
                "email_subject": details.get("email_subject", ""),
                "email_body": details.get("email_body", ""),
                "gmail_draft_id": details.get("gmail_draft_id", ""),
                "offer_date": details.get("offer_date", ""),
                "source": details.get("source", ""),
                "qualification_score": details.get("qualification_score", ""),
                "qualification_reasons": details.get("qualification_reasons", ""),
                "notes": details.get("notes", "")})
    with REGISTRY_PATH.open("a", encoding="utf-8-sig", newline="") as file:
        csv.DictWriter(file, fieldnames=fieldnames).writerow(row)


def filter_new_offers(offers: list[dict]) -> list[dict]:
    """Retire de la liste les offres déjà traitées."""
    init_db()
    new = []
    for o in offers:
        if not already_applied(o["url"], o.get("company")):
            new.append(o)
    return new


def clean_registry():
    """Retire les lignes dont l'URL est une page listing/recherche, pas une offre.

    Renvoie le nombre de lignes supprimées. Un backup .csv.bak est créé avant.
    """
    from search_offers import _is_offer_url

    init_db()
    with REGISTRY_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    kept = [row for row in rows if _is_offer_url(row.get("url", ""))]
    removed = len(rows) - len(kept)
    if removed:
        REGISTRY_PATH.with_suffix(".csv.bak").write_text(
            REGISTRY_PATH.read_text(encoding="utf-8-sig"), encoding="utf-8-sig"
        )
        with REGISTRY_PATH.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(kept)
    return removed


if __name__ == "__main__":
    init_db()
    print(f"Registre initialisé : {REGISTRY_PATH}")
