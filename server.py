"""Local-only application server, durable API and optional in-process daily watch."""
import argparse
import json
import mimetypes
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote

from dotenv import load_dotenv

from application_documents import build_documents, build_email
from contracts import validate_profile
from engine import Team, profile_signature
from export_excel import export_excel, sync_excel
from gmail_gateway import TOKEN, draft_email, service
from providers import ProviderError, Providers
from research import public_url
from storage import ROOT, Store, STATUSES, now

ACTIVE = {"queued", "running", "cancelling"}


class Application:
    def __init__(self, store=None):
        self.store = store or Store()
        self.store.migrate()
        self.store.recover()
        self.team = Team(self.store)
        self.lock = threading.RLock()
        self.gmail_connecting = False
        self.gmail_error = ""
        # Apply the user's updated target once, preserving all other profile edits.
        if not self.store.get("target_2027_v2"):
            profile = self.store.get("profile", {})
            if profile:
                profile["target_criteria"].update(start_date="Fin janvier 2027", countries=["France", "Bruxelles (Belgique)", "Suisse", "Tunisie"])
                self.store.set("profile", profile)
            self.store.set("target_2027_v2", True)

    def voice_summary(self):
        """Short spoken French briefing built from real registry data.

        Facts first, LLM only to phrase it; falls back to a plain sentence when
        Groq is out of budget or missing, so the demo never silences.
        """
        applications = self.store.applications()
        visible = [a for a in applications if a.get("status") not in {"rejected", "archived"}]
        top = sorted(visible, key=lambda a: a.get("qualification_score") or 0, reverse=True)[:3]
        runs = self.store.runs()
        last = runs[0] if runs else {}
        facts = {
            "total_offres": len(visible),
            "top_offres": [{"titre": o.get("title") or "Offre sans titre",
                            "entreprise": o.get("company") or "",
                            "ville": o.get("location") or "",
                            "score": o.get("qualification_score")} for o in top],
            "derniere_mission": {"statut": last.get("status", "aucune"), "resume": last.get("summary", "")},
        }
        system = (
            "Tu es la voix de LevelUp, assistant de recherche de stage d'une eleve ingenieure data. "
            "Fais-leur un point oral court et chaleureux en francais, a la deuxieme personne. "
            "Annonce le nombre d'offres, les trois meilleures avec leur score, puis le statut de la derniere mission. "
            "Maximum 90 mots. Parle naturellement, sans markdown, sans listes, sans ponctuation lourde."
        )
        try:
            spoken = Providers(self.store).text(system, json.dumps(facts, ensure_ascii=False))
        except ProviderError:
            spoken = self._plain_summary(facts)
        return {"text": spoken, "facts": facts}

    def _plain_summary(self, facts):
        lines = [f"Tu as {facts['total_offres']} offre(s) en cours."]
        for o in facts["top_offres"]:
            lines.append(f"{o['titre']} chez {o['entreprise'] or 'une entreprise'}, score {o['score'] or 'non note'}.")
        mission = facts["derniere_mission"]
        lines.append(f"Derniere mission : {mission['statut']}.")
        return " ".join(lines)

    def overview(self):
        applications = []
        for row in self.store.applications():
            applications.append({k: v for k, v in row.items() if k not in {"document_paths", "document_profile"}})
        return {"applications": applications, "runs": self.store.runs(), "events": self.store.events(),
                "settings": self.store.settings(), "usage": self.store.usage(),
                "connections": {"groq": bool(os.getenv("GROQ_API_KEY")), "tavily": bool(os.getenv("TAVILY_API_KEY")),
                                "gmail": TOKEN.exists(), "gmail_connecting": self.gmail_connecting, "gmail_error": self.gmail_error},
                "profile": self.store.get("profile", {}), "companies": self.store.get("companies", [])}

    def documents(self, row):
        paths = row.get("document_paths", [])
        if len(paths) != 2 or any(not Path(p).is_file() for p in paths):
            raise ValueError("Prepare d'abord le CV et la lettre de candidature.")
        return [{"name": Path(p).name, "path": p} for p in paths]

    def patch_application(self, id, payload):
        allowed = {"status", "notes", "email_subject", "email_body", "recipient"}
        if set(payload) - allowed:
            raise ValueError("Champ non modifiable")
        row = self.store.application(id)
        if any(run["status"] in ACTIVE and run["payload"].get("application_id") == id for run in self.store.runs()):
            raise ValueError("Preparation en cours. Attends la fin avant de modifier ce dossier.")
        for key, value in payload.items():
            if not isinstance(value, str) or len(value) > (10000 if key in {"email_body", "notes"} else 300):
                raise ValueError("Valeur invalide")
        if "status" in payload:
            if payload["status"] not in STATUSES or payload["status"] in {"approved", "draft_created", "preparing"}:
                raise ValueError("Utilise l'action de validation ou de preparation correspondante.")
            if payload["status"] == "applied" and row.get("status") != "applied":
                payload["applied_at"] = now()
        if "recipient" in payload and payload["recipient"] and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", payload["recipient"]):
            raise ValueError("Adresse email invalide")
        if any("\n" in payload.get(key, "") or "\r" in payload.get(key, "") for key in ("recipient", "email_subject")):
            raise ValueError("Entete email invalide")
        edited = any(key in payload and payload[key] != row.get(key, "") for key in ("email_body", "email_subject", "recipient"))
        if edited:
            payload.update(status="review_needed", approved_at=None, review={"passed": False, "issues": ["Dossier modifie manuellement : relire avant validation"], "suggestions": []})
            if row.get("document_paths"):
                version = uuid.uuid4().hex[:12]
                docs = build_documents(row.get("document_profile", self.store.get("profile")), {**row, **payload}, ROOT / "data" / "documents" / id / version)
                payload["document_paths"] = [d["path"] for d in docs]
                payload["artifacts"] = [{"name": d["name"], "url": f"/api/artifacts/{id}/{version}/{Path(d['path']).name}"} for d in docs]
        return self.store.update_application(id, payload)

    def approve(self, id):
        row = self.store.application(id)
        self.documents(row)
        if row["status"] not in {"review_needed", "approved"}:
            raise ValueError("Le dossier doit etre prepare puis relu.")
        if not row.get("email_body", "").strip() or not row.get("email_subject", "").strip():
            raise ValueError("L'objet et le corps de la candidature sont requis.")
        if row.get("prepared_profile") != profile_signature(self.store.get("profile")):
            raise ValueError("Ton profil a change : prepare une nouvelle version du dossier.")
        self.store.event(None, "user", "archivist", f"Dossier valide : {row['title']}", "approval", {"application_id": id})
        return self.store.update_application(id, {"status": "approved", "approved_at": now()})

    def make_draft(self, id):
        row = self.store.application(id)
        if row.get("gmail_draft_id") and row["status"] == "draft_created":
            return row
        if row.get("draft_pending"):
            raise ValueError("Creation Gmail au resultat incertain. Verifie les brouillons Gmail avant toute nouvelle tentative.")
        if row["status"] != "approved" or not row.get("approved_at"):
            raise ValueError("Valide le dossier avant de creer le brouillon Gmail.")
        if row.get("prepared_profile") != profile_signature(self.store.get("profile")):
            raise ValueError("Le profil a change depuis la preparation.")
        if not TOKEN.exists():
            raise ValueError("Gmail n'est pas connecte. L'export email reste disponible.")
        raw = build_email(row["document_profile"], row, self.documents(row))
        self.store.update_application(id, {"draft_pending": True})
        try:
            draft = draft_email(raw, row.get("gmail_draft_id"))
        except ValueError:
            self.store.update_application(id, {"draft_pending": False})
            raise
        except Exception as error:
            raise ValueError("Gmail n'a pas confirme la creation. Verifie ta boite; aucune nouvelle tentative automatique.") from error
        return self.store.update_application(id, {"gmail_draft_id": draft["id"], "status": "draft_created", "draft_pending": False})

    def connect_gmail(self):
        if self.gmail_connecting:
            return
        self.gmail_connecting, self.gmail_error = True, ""
        def connect():
            try:
                service(interactive=True)
            except Exception:
                self.gmail_error = "Connexion Gmail incomplete. Verifie credentials.json, le compte test OAuth et reessaie."
            finally:
                self.gmail_connecting = False
        threading.Thread(target=connect, daemon=True).start()


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, value, status=200, mime="application/json; charset=utf-8", filename=None):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8") if mime.startswith("application/json") else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            origin = self.headers.get("Origin")
            if origin and self.allowed_origin(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def allowed_origin(self, origin):
            parts = urlsplit(origin)
            return parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"} and parts.port in {self.server.server_port, *range(5173, 5191)}

        def check_origin(self):
            host = urlsplit("http://" + self.headers.get("Host", "")).hostname
            if host not in {"127.0.0.1", "localhost"}:
                raise PermissionError("Serveur local uniquement")
            origin = self.headers.get("Origin")
            if origin and not self.allowed_origin(origin):
                raise PermissionError("Origine refusee")

        def payload(self):
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ValueError("Content-Type application/json requis")
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 500_000:
                raise ValueError("Requete trop volumineuse")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Objet JSON requis")
            return data

        def route(self, method):
            try:
                self.check_origin()
                path = urlsplit(self.path).path
                parts = path.strip("/").split("/")
                if method == "GET":
                    return self.get(path, parts)
                data = self.payload()
                with app.lock:
                    return self.mutate(method, path, parts, data)
            except PermissionError as error:
                self.respond({"error": str(error)}, 403)
            except KeyError as error:
                self.respond({"error": str(error)}, 404)
            except (ValueError, TypeError, sqlite3.IntegrityError) as error:
                self.respond({"error": str(error)}, 400)
            except Exception as error:
                print(f"HTTP {method}: {type(error).__name__}")
                self.respond({"error": "Operation interrompue. Verifie le journal et reessaie."}, 500)

        def get(self, path, parts):
            if path == "/api/health":
                return self.respond({"status": "ok", "service": "jamil", "version": 2})
            if path == "/api/overview":
                return self.respond(app.overview())
            if path == "/api/applications":
                return self.respond({"applications": app.overview()["applications"]})
            if path == "/api/status":
                runs = app.store.runs()
                run = runs[0] if runs else {}
                return self.respond({"running": run.get("status") in ACTIVE, "message": run.get("summary", "Pret"), "errors": run.get("errors", [])})
            if len(parts) == 4 and parts[:2] == ["api", "applications"] and parts[3] == "export.eml":
                row = app.store.application(parts[2])
                raw = build_email(row.get("document_profile", app.store.get("profile")), row, app.documents(row))
                return self.respond(raw, mime="message/rfc822", filename="candidature.eml")
            if len(parts) == 5 and parts[:2] == ["api", "artifacts"]:
                base = (ROOT / "data" / "documents").resolve()
                target = (base / unquote("/".join(parts[2:]))).resolve()
                if not target.is_relative_to(base) or target.suffix != ".pdf" or not target.is_file():
                    raise KeyError("Document introuvable")
                return self.respond(target.read_bytes(), mime="application/pdf", filename=target.name)
            if path == "/api/voice":
                return self.respond(app.voice_summary())
            if path.startswith("/api/"):
                raise KeyError("Route inconnue")
            root = (ROOT / "frontend" / "dist").resolve()
            target = (root / unquote(path).lstrip("/")).resolve()
            if not target.is_relative_to(root):
                raise PermissionError("Chemin refuse")
            if not target.is_file():
                target = root / "index.html"
            if not target.exists():
                return self.respond(b"Frontend absent. Run npm run build in frontend.", 503, "text/plain")
            return self.respond(target.read_bytes(), mime=mimetypes.guess_type(target.name)[0] or "application/octet-stream")

        def mutate(self, method, path, parts, data):
            if method == "POST" and path == "/api/run":
                maximum = data.get("max", 8)
                if type(maximum) is not int or not 1 <= maximum <= 32:
                    raise ValueError("Choisis entre 1 et 32 offres prioritaires")
                return self.respond(app.team.start("hunt", {"max": maximum}), 202)
            if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "runs"]:
                if parts[3] == "cancel":
                    app.team.cancel(parts[2])
                elif parts[3] == "resume":
                    app.team.resume(parts[2])
                else:
                    raise KeyError("Action inconnue")
                return self.respond(app.store.run(parts[2]), 202)
            if path == "/api/applications" and method == "POST":
                allowed = {"url", "title", "company", "content", "location"}
                if set(data) - allowed or any(not isinstance(v, str) for v in data.values()):
                    raise ValueError("Champs de l'offre invalides")
                public_url(data.get("url", ""))
                row, added = app.store.add_application({**data, "source": "Import manuel", "verification": "user_provided"})
                return self.respond({"application": row, "created": added}, 201 if added else 200)
            if len(parts) >= 3 and parts[:2] == ["api", "applications"]:
                id = parts[2]
                if method == "PATCH" and len(parts) == 3:
                    return self.respond({"application": app.patch_application(id, data)})
                if method == "POST" and len(parts) == 4:
                    app.store.application(id)
                    if parts[3] == "prepare":
                        return self.respond(app.team.start("prepare", {"application_id": id}), 202)
                    if parts[3] == "approve":
                        return self.respond({"application": app.approve(id)})
                    if parts[3] == "draft":
                        return self.respond({"application": app.make_draft(id)})
            if method == "PUT" and path == "/api/profile":
                app.store.set("profile", validate_profile(data))
                return self.respond({"profile": data})
            if method == "PATCH" and path == "/api/settings":
                settings = app.store.settings()
                if set(data) - set(settings):
                    raise ValueError("Parametre inconnu")
                for key, value in data.items():
                    if key == "auto_watch":
                        if type(value) is not bool:
                            raise ValueError("Valeur booleenne requise")
                    else:
                        low, high = {"watch_hour": (0, 23), "daily_search_limit": (1, 64), "monthly_search_limit": (1, 800), "daily_llm_limit": (1, 80)}[key]
                        if type(value) is not int or not low <= value <= high:
                            raise ValueError(f"{key} doit etre compris entre {low} et {high}")
                    settings[key] = value
                app.store.set("settings", settings)
                return self.respond({"settings": settings})
            if method == "POST" and path == "/api/gmail/connect":
                app.connect_gmail()
                return self.respond({"message": "Connexion Google en cours"}, 202)
            if method == "POST" and path == "/api/companies":
                if not isinstance(data.get("name"), str) or not data["name"].strip():
                    raise ValueError("Nom de societe requis")
                companies = app.store.get("companies", [])
                if not any(c["name"].casefold() == data["name"].strip().casefold() for c in companies):
                    companies.append({"id": uuid.uuid4().hex, "name": data["name"].strip(), "website": str(data.get("website", "")), "notes": str(data.get("notes", "")), "priority": "high" if data.get("priority") == "high" else "normal"})
                    app.store.set("companies", companies)
                return self.respond({"companies": companies}, 201)
            if method == "DELETE" and len(parts) == 3 and parts[:2] == ["api", "companies"]:
                companies = [c for c in app.store.get("companies", []) if c["id"] != parts[2]]
                app.store.set("companies", companies)
                return self.respond({"companies": companies})
            if method == "POST" and path == "/api/excel/export":
                excel_path, count = export_excel(app.store, app.store.path.parent / "suivi_candidatures.xlsx")
                return self.respond({"path": str(excel_path), "count": count})
            if method == "POST" and path == "/api/excel/sync":
                updated = sync_excel(app.store, app.store.path.parent / "suivi_candidatures.xlsx")
                return self.respond({"updated": updated})
            raise KeyError("Route inconnue")

        def do_GET(self): self.route("GET")
        def do_POST(self): self.route("POST")
        def do_PATCH(self): self.route("PATCH")
        def do_PUT(self): self.route("PUT")
        def do_DELETE(self): self.route("DELETE")

        def do_OPTIONS(self):
            try:
                self.check_origin()
            except PermissionError as error:
                return self.respond({"error": str(error)}, 403)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "http://127.0.0.1"))
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, PUT, DELETE, OPTIONS")
            self.end_headers()

        def log_message(self, format, *args):
            pass
    return Handler


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.getenv("JAMIL_PORT", "8000")))
    args = parser.parse_args()
    app = Application()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(app))
    threading.Thread(target=app.team.watch, daemon=True).start()
    print(f"LevelUp : http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.team.stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
