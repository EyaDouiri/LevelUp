"""Gestion des sociétés cibles de l'utilisateur (Mes sociétés)."""
import json
import os
from pathlib import Path

PATH = Path(__file__).parent / "data" / "companies.json"


def load_companies() -> list[dict]:
    if not PATH.exists():
        return []
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def save_companies(companies: list[dict]) -> None:
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(companies, ensure_ascii=False, indent=2), encoding="utf-8")


def add_company(company: dict) -> list[dict]:
    companies = load_companies()
    companies.append({
        "name": company.get("name", "").strip(),
        "website": company.get("website", "").strip(),
        "notes": company.get("notes", "").strip(),
        "priority": company.get("priority", "normal"),
    })
    save_companies(companies)
    return companies


def delete_company(index: int) -> list[dict]:
    companies = load_companies()
    if 0 <= index < len(companies):
        companies.pop(index)
        save_companies(companies)
    return companies


def suggest_companies(profile: dict, max_suggestions: int = 10) -> list[dict]:
    """Propose des sociétés cibles pertinentes à partir du profil (appel LLM)."""
    from groq import Groq

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    system = (
        "Tu es un conseiller de carriere specialise en actuariat, data science et assurance en France, "
        "Suisse et Espagne. A partir du profil fourni, propose les entreprises les plus pertinentes pour un "
        "stage de fin d'etudes (PFE). Réponds UNIQUEMENT en JSON valide, sans texte autour : une liste d'objets "
        "avec 'name' (nom de l'entreprise), 'country' (FR/CH/ES) et 'reason' (une courte phrase en francais "
        "expliquant pourquoi c'est pertinent). Vise 8 a 10 entreprises variees : assureurs, reassureurs, fintech, "
        "consulting actuarial, et grands employeurs data/assurance."
    )
    user = json.dumps({"profil": profile}, ensure_ascii=False)
    raw = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3,
    ).choices[0].message.content
    text = (raw or "").strip().strip("`")
    if text.startswith("json"):
        text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data[:max_suggestions]:
        name = (item.get("name") or "").strip()
        if name:
            out.append({"name": name, "country": item.get("country", ""), "reason": item.get("reason", "")})
    return out
