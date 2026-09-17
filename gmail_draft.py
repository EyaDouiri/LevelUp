"""
Module 5 — Crée des brouillons Gmail (pas d'envoi automatique — tu valides et
envoies toi-même depuis ta boîte).

Setup requis (une seule fois) :
1. Va sur https://console.cloud.google.com/apis/credentials
2. Crée un projet, active la "Gmail API"
3. Crée un identifiant OAuth "Application de bureau", télécharge le JSON
4. Renomme-le credentials.json et mets-le à la racine du projet
5. Au premier lancement, une page web s'ouvre pour te connecter à ton compte Google
   → un token.json est créé automatiquement, tu n'auras plus à te reconnecter après
"""
import os
import base64
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
TOKEN_PATH = Path(__file__).parent / "token.json"


def get_gmail_service():
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            cred_path = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
            if not Path(cred_path).exists():
                raise RuntimeError(
                    f"'{cred_path}' introuvable. Suis les instructions en haut de "
                    "gmail_draft.py pour créer tes identifiants Google Cloud."
                )
            flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def create_draft(to: str, subject: str, body: str, cv_path: str | None = None) -> str:
    """
    Crée un brouillon dans Gmail. Retourne l'ID du brouillon créé.
    Si cv_path est fourni, joint le fichier (CV en PDF par ex.).
    """
    service = get_gmail_service()

    if cv_path and Path(cv_path).exists():
        from email.mime.multipart import MIMEMultipart
        from email.mime.application import MIMEApplication

        message = MIMEMultipart()
        message["to"] = to
        message["subject"] = subject
        message.attach(MIMEText(body))

        with open(cv_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=Path(cv_path).name)
        part["Content-Disposition"] = f'attachment; filename="{Path(cv_path).name}"'
        message.attach(part)
    else:
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    return draft["id"]


def is_authenticated() -> bool:
    """True si un token Gmail existe déjà (OAuth déjà réalisé)."""
    return TOKEN_PATH.exists()


def list_drafts(max_results: int = 25) -> list[dict]:
    """Retourne les brouillons Gmail existants (id, subject, url)."""
    service = get_gmail_service()
    results = service.users().drafts().list(userId="me", maxResults=max_results).execute()
    drafts = results.get("drafts", [])
    out = []
    for draft in drafts:
        draft_id = draft["id"]
        subject = "(sans objet)"
        try:
            full = (
                service.users()
                .drafts()
                .get(userId="me", id=draft_id, format="metadata", metadataHeaders=["Subject"])
                .execute()
            )
            headers = full.get("message", {}).get("payload", {}).get("headers", [])
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), subject)
        except Exception:
            pass
        out.append(
            {
                "id": draft_id,
                "subject": subject,
                "url": f"https://mail.google.com/mail/u/0/#drafts/{draft_id}",
            }
        )
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    draft_id = create_draft(
        to="",  # laisse vide pour un brouillon sans destinataire à compléter à la main
        subject="Test — Candidature spontanée",
        body="Ceci est un brouillon de test.",
    )
    print(f"Brouillon créé, id={draft_id}. Va voir dans Gmail > Brouillons.")
