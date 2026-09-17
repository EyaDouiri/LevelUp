"""
Module 3 — Génère une lettre de motivation ciblée + choix de mise en avant du CV
(actuariat / data science / combiné) pour une offre donnée.

Supporte Groq (gratuit/rapide) ou l'API Anthropic,
au choix via LLM_PROVIDER dans .env.
"""
import os
import json

SYSTEM_PROMPT = """Tu es un assistant qui aide une étudiante ingénieure (data science + \
actuariat, cursus franco-tunisien) à candidater à des stages de fin d'études en Europe.

À partir de son profil (JSON) et d'une offre (titre + extrait), tu dois produire :
1. "cv_angle": quel angle du CV mettre en avant — "actuariat", "data_science", ou "combine"
2. "matched_projects": liste des 2-3 projets/expériences de son profil les plus pertinents pour CETTE offre
3. "email_subject": objet d'email de candidature spontanée, court et professionnel
4. "email_body": corps de l'email en français, professionnel, PERSONNALISÉ à l'offre \
   (mentionne l'entreprise et le type de poste), 150-200 mots, qui:
   - se présente brièvement (étudiante Esprit + IRA, double compétence data/actuariat)
   - relie 2-3 expériences concrètes de son profil à ce que l'offre semble demander
   - précise sa disponibilité à partir de janvier 2027
   - demande un échange ou indique le CV en pièce jointe
   - reste honnête : ne JAMAIS inventer d'expérience, de chiffres ou de résultats qui ne sont \
     pas dans le profil fourni

Réponds UNIQUEMENT en JSON valide, sans texte avant/après, sans balises markdown."""


def _build_user_prompt(profile: dict, offer: dict) -> str:
    return json.dumps(
        {
            "profil": profile,
            "offre": {
                "titre": offer.get("title"),
                "extrait": offer.get("content"),
                "url": offer.get("url"),
            },
        },
        ensure_ascii=False,
    )


def _call_groq(system: str, user: str) -> str:
    from groq import Groq

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    resp = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.4,
    )
    return resp.choices[0].message.content


def qualify_offer(profile: dict, offer: dict) -> dict:
    """Évalue la pertinence d'une offre avant de lancer le tailoring."""
    system = (
        "Tu es l'agent de qualification d'une candidature. Analyse l'offre par rapport au profil COMPLET "
        "(éducation, expériences, compétences, critères de recherche). "
        "Réponds uniquement en JSON valide avec les clés relevant (booléen), score (0 à 100), "
        "reasons (liste courte), missing_information (liste courte). "
        "Un score élevé nécessite une vraie adéquation entre le profil et l'offre. "
        "Ne considère pas une information absente comme une preuve négative. "
        "Une offre senior, hors secteur ou hors zone géographique sans flexibilité doit obtenir un score bas."
    )
    slim_profile = {
        "target_criteria": profile.get("target_criteria"),
        "education": profile.get("education", []),
        "experience": profile.get("experience", []),
        "projects": profile.get("projects", []),
        "skills": profile.get("skills", {}),
    }
    user = json.dumps({"profil": slim_profile, "offre": {
        "titre": offer.get("title"), "url": offer.get("url"),
        "extrait": str(offer.get("content", ""))[:3000],
        "entreprise": offer.get("company", ""),
        "localisation": offer.get("location", ""),
    }}, ensure_ascii=False)
    raw = _call_groq(system, user).strip().strip("`")
    if raw.startswith("json"):
        raw = raw[4:].strip()
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Reponse non-dictionnaire")
        for key in ("relevant", "score", "reasons", "missing_information"):
            if key not in result:
                raise ValueError(f"Champ manquant : {key}")
        result.setdefault("reasons", [])
        result.setdefault("missing_information", [])
        return result
    except (json.JSONDecodeError, ValueError, KeyError):
        return {"relevant": False, "score": 0, "reasons": ["Reponse LLM invalide lors de la qualification"],
                "missing_information": [], "uncertain": True}


def _call_anthropic(system: str, user: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def tailor_application(profile: dict, offer: dict) -> dict:
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    user_prompt = _build_user_prompt(profile, offer)

    if provider == "anthropic":
        raw = _call_anthropic(SYSTEM_PROMPT, user_prompt)
    else:
        raw = _call_groq(SYSTEM_PROMPT, user_prompt)

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "cv_angle": "combine",
            "matched_projects": [],
            "email_subject": f"Candidature spontanée — {offer.get('title', '')}",
            "email_body": raw,  # fallback: renvoie le texte brut pour relecture manuelle
        }


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    with open("profile.json", encoding="utf-8") as f:
        profile = json.load(f)

    fake_offer = {
        "title": "Stage PFE — Data Scientist Actuariat",
        "content": "Nous recherchons un(e) stagiaire pour travailler sur des modèles de "
        "provisionnement IBNR et de pricing GLM.",
        "url": "https://example.com/offre-test",
    }
    result = tailor_application(profile, fake_offer)
    print(json.dumps(result, indent=2, ensure_ascii=False))
