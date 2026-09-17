"""OAuth and draft upsert only. There is deliberately no send operation."""
import base64
import os
from pathlib import Path

from storage import ROOT

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
TOKEN = ROOT / "token.json"


def service(interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    credentials = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        if not interactive:
            raise ValueError("Connecte Gmail depuis les parametres avant de creer le brouillon.")
        path = Path(os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json"))
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise ValueError("credentials.json OAuth Google absent du projet.")
        flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
        credentials = flow.run_local_server(port=0, timeout_seconds=180)
    temporary = TOKEN.with_suffix(".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    temporary.replace(TOKEN)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def draft_email(raw, existing_id=None):
    drafts = service().users().drafts()
    body = {"message": {"raw": base64.urlsafe_b64encode(raw).decode("ascii")}}
    if existing_id:
        return drafts.update(userId="me", id=existing_id, body=body).execute()
    return drafts.create(userId="me", body=body).execute()
