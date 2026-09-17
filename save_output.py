"""
Module 4 — Sauvegarde des résultats en CSV (ouvrable direct dans Excel/Google Sheets).
"""
import csv
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent / "data"


def save_to_csv(rows: list[dict], filename: str | None = None) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    if filename is None:
        filename = f"candidatures_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    path = OUTPUT_DIR / filename

    fieldnames = [
        "titre_offre",
        "entreprise",
        "url",
        "date_offre",
        "date_creation",
        "source",
        "score_qualification",
        "raisons_qualification",
        "cv_angle",
        "projets_mis_en_avant",
        "objet_email",
        "corps_email",
        "statut",
        "brouillon_gmail_cree",
        "gmail_draft_id",
        "notes",
    ]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "titre_offre": row.get("title", ""),
                    "entreprise": row.get("company", ""),
                    "url": row.get("url", ""),
                    "date_offre": row.get("published_date", ""),
                    "date_creation": row.get("created_at", datetime.now().isoformat()),
                    "source": row.get("source", ""),
                    "score_qualification": row.get("qualification_score", ""),
                    "raisons_qualification": "; ".join(row.get("qualification_reasons", [])) if isinstance(row.get("qualification_reasons", []), list) else row.get("qualification_reasons", ""),
                    "cv_angle": row.get("cv_angle", ""),
                    "projets_mis_en_avant": ", ".join(row.get("matched_projects", [])),
                    "objet_email": row.get("email_subject", ""),
                    "corps_email": row.get("email_body", ""),
                    "statut": row.get("status", "draft_created"),
                    "brouillon_gmail_cree": "oui" if row.get("gmail_draft_id") else "non",
                    "gmail_draft_id": row.get("gmail_draft_id", ""),
                    "notes": row.get("notes", ""),
                }
            )

    print(f"[save_output] {len(rows)} lignes écrites dans {path}")
    return path
