"""Validated contracts exchanged by the agents and the HTTP boundary."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(Contract):
    requirement: str
    evidence_id: str
    explanation: str


class Assessment(Contract):
    score: int = Field(ge=0, le=100)
    relevant: bool
    reasons: list[str]
    missing_information: list[str]
    blockers: list[str]
    matches: list[Evidence]
    company: str
    location: str
    contract: str
    start_date: str
    language: Literal["fr", "en"]


class CVPlan(Contract):
    experience_ids: list[str]
    project_ids: list[str]
    skill_keys: list[str]


class ApplicationDraft(Contract):
    email_subject: str = Field(min_length=3, max_length=250)
    email_body: str = Field(min_length=50, max_length=6000)
    cv_angle: Literal["actuariat", "data_science", "combine"]
    cv_plan: CVPlan
    evidence_ids: list[str]


class Critique(Contract):
    passed: bool
    issues: list[str]
    suggestions: list[str]


def evidence_catalog(profile):
    return {
        **{f"exp:{i}": value for i, value in enumerate(profile.get("experience", []))},
        **{f"project:{i}": value for i, value in enumerate(profile.get("projects", []))},
        **{f"skill:{key}": value for key, value in profile.get("skills", {}).items()},
    }


def validate_profile(profile):
    if not isinstance(profile, dict) or not isinstance(profile.get("name"), str) or not profile["name"].strip():
        raise ValueError("Le profil doit contenir un nom.")
    criteria = profile.get("target_criteria")
    if not isinstance(criteria, dict):
        raise ValueError("Criteres de recherche manquants.")
    for key in ("countries", "keywords_priority", "contract_types"):
        values = criteria.get(key)
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v.strip() for v in values):
            raise ValueError(f"Critere invalide : {key}")
    if not isinstance(criteria.get("start_date"), str) or not criteria["start_date"].strip():
        raise ValueError("Date de disponibilite manquante.")
    for key in ("experience", "projects", "education"):
        if not isinstance(profile.get(key, []), list) or not all(isinstance(v, dict) for v in profile.get(key, [])):
            raise ValueError(f"Section invalide : {key}")
    if not isinstance(profile.get("skills", {}), dict):
        raise ValueError("Competences invalides.")
    return profile
