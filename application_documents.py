"""Evidence-only PDF rendering and unsent MIME email packages.

Runtime dependency: reportlab. Each application should have its own output_dir.
"""

from io import BytesIO
from pathlib import Path
from email.message import EmailMessage
from email.policy import SMTP
from html import escape
import os
import re
import tempfile
from threading import Lock
from urllib.parse import urlsplit

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


_FONT_LOCK = Lock()


def _fonts():
    with _FONT_LOCK:
        if "JamilRegular" in pdfmetrics.getRegisteredFontNames():
            return "JamilRegular", "JamilBold"
        roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                 Path("/usr/share/fonts/truetype/dejavu")]
        roots.extend(Path(p) for p in rl_config.TTFSearchPath)
        for regular, bold in [("arial.ttf", "arialbd.ttf"),
                              ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
                              ("Vera.ttf", "VeraBd.ttf")]:
            for root in roots:
                if (root / regular).is_file() and (root / bold).is_file():
                    pdfmetrics.registerFont(TTFont("JamilRegular", str(root / regular)))
                    pdfmetrics.registerFont(TTFont("JamilBold", str(root / bold)))
                    return "JamilRegular", "JamilBold"
        raise RuntimeError("Install an Arial or DejaVu Sans font pair for PDF export")


def _text(value):
    return "" if value is None else str(value)


def _escaped(value):
    value = _text(value)
    value = "".join(c for c in value if ord(c) >= 32 or c in "\n\t")
    return escape(value).replace("\n", "<br/>")


def _selection(records, ids, prefix):
    if not isinstance(ids, list):
        raise ValueError(f"{prefix} selection must be a list")
    selected = []
    for evidence_id in ids:
        if not isinstance(evidence_id, str) or not re.fullmatch(prefix + r":(0|[1-9][0-9]*)", evidence_id):
            raise ValueError(f"Invalid evidence id: {evidence_id!r}")
        index = int(evidence_id.split(":")[1])
        if index >= len(records):
            raise ValueError(f"Unknown evidence id: {evidence_id}")
        if index not in selected:
            selected.append(index)
    return [records[i] for i in selected] if ids else records


def _data(profile, application):
    plan = application.get("cv_plan") or {}
    if not isinstance(plan, dict):
        raise ValueError("cv_plan must be an object")
    experiences = _selection(profile.get("experience", []), plan.get("experience_ids", []), "exp")
    projects = _selection(profile.get("projects", []), plan.get("project_ids", []), "project")
    skills = profile.get("skills", {})
    keys = plan.get("skill_keys", [])
    if not isinstance(keys, list) or any(not isinstance(k, str) or k not in skills for k in keys):
        raise ValueError("Invalid skill_keys")
    return experiences, projects, {k: skills[k] for k in dict.fromkeys(keys)} if keys else skills


def _render(profile, application, data, letter=False, size=9.5):
    regular, bold = _fonts()
    buffer = BytesIO()
    styles = {
        "body": ParagraphStyle("body", fontName=regular, fontSize=size, leading=size * 1.35,
                               spaceAfter=4, alignment=TA_LEFT, splitLongWords=True),
        "heading": ParagraphStyle("heading", fontName=bold, fontSize=size + 1.5,
                                  leading=size + 5, spaceBefore=9, spaceAfter=5, keepWithNext=True,
                                  textColor=colors.HexColor("#16665C")),
        "title": ParagraphStyle("title", fontName=bold, fontSize=21, leading=26, spaceAfter=6),
        "record": ParagraphStyle("record", fontName=bold, fontSize=size, leading=size * 1.35,
                                 spaceAfter=3, keepWithNext=True),
    }
    story = []

    def add(value, style="body"):
        if _text(value).strip():
            story.append(Paragraph(_escaped(value), styles[style]))

    def link(value):
        raw = _text(value).strip()
        if not raw:
            return
        url = raw if ":" in raw else "https://" + raw
        if urlsplit(url).scheme.lower() in ("http", "https", "mailto"):
            story.append(Paragraph(f'<link href="{escape(url, quote=True)}" color="#16665C">{_escaped(raw)}</link>', styles["body"]))
        else:
            add(raw)

    add(profile.get("name"), "title")
    add(profile.get("title"))
    add(" | ".join(_text(profile.get(k)) for k in ("email", "phone", "location") if profile.get(k)))
    for key in ("linkedin", "github", "website"):
        link(profile.get(key))
    story.append(Spacer(1, 7))
    if letter:
        add(application.get("company"), "heading")
        add(application.get("email_subject") or application.get("title"), "record")
        link(application.get("url"))
        story.append(Spacer(1, 8))
        # The supplied body is the letter; the renderer adds no claims or salutation.
        for paragraph in re.split(r"\n\s*\n", _text(application.get("email_body")).replace("\r\n", "\n")):
            add(paragraph)
            story.append(Spacer(1, 5))
    else:
        add(profile.get("profile_summary"))
        start = (profile.get("target_criteria") or {}).get("start_date")
        if start:
            add("Disponibilit\u00e9 : " + _text(start))
        experiences, projects, skills = data
        for label, records, fields in [
            ("Exp\u00e9riences", experiences, ("role", "company", "period")),
            ("Formation", profile.get("education", []), ("degree", "school", "period")),
            ("Projets", projects, ("title", "stack", "period")),
        ]:
            if records:
                add(label, "heading")
            for record in records:
                add(" | ".join(_text(record.get(k)) for k in fields if record.get(k)), "record")
                add(record.get("description"))
                for bullet in record.get("bullets", []):
                    add("\u2022 " + _text(bullet))
                link(record.get("url"))
        if skills:
            add("Comp\u00e9tences", "heading")
        for key, values in skills.items():
            add(key + " : " + (", ".join(map(_text, values)) if isinstance(values, list) else _text(values)))

    pages = []

    def decorate(canvas, doc):
        pages.append(doc.page)
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#16665C"))
        canvas.setLineWidth(1.2)
        canvas.line(42, A4[1] - 29, A4[0] - 42, A4[1] - 29)
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(42, 24, "Lettre de candidature" if letter else "Curriculum vitae")
        canvas.drawRightString(A4[0] - 42, 24, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=42, leftMargin=42,
                            topMargin=42, bottomMargin=42, title="Lettre de candidature" if letter else "CV",
                            author=_text(profile.get("name")))
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue(), len(pages)


def build_documents(profile: dict, application: dict, output_dir: Path) -> list[dict]:
    """Export exact selected evidence; empty selections mean all records.

    Try readable compact layouts for a two-page CV. Exceptionally long profiles
    retain all content and may exceed two pages rather than truncating evidence.
    Fixed filenames cannot be influenced by application text.
    """
    data = _data(profile, application)
    cv, count = _render(profile, application, data)
    for size in (9, 8.5):
        if count <= 2:
            break
        cv, count = _render(profile, application, data, size=size)
    letter, _ = _render(profile, application, data, letter=True, size=10)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = []
    for name, content in (("CV.pdf", cv), ("lettre.pdf", letter)):
        target = output_dir / name
        # Atomic replacement avoids exposing a partially written PDF to readers.
        fd, temporary = tempfile.mkstemp(prefix=".jamil-", suffix=".pdf", dir=output_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        documents.append({"name": name, "path": str(target)})
    return documents


def build_email(profile, application, documents) -> bytes:
    """Create an unsent UTF-8 .eml with the actual exported PDF attachments."""
    message = EmailMessage(policy=SMTP)
    if profile.get("email"):
        message["From"] = profile["email"]
    if application.get("recipient"):
        message["To"] = application["recipient"]
    message["Subject"] = application.get("email_subject") or ""
    message.set_content(application.get("email_body") or "", charset="utf-8")
    names = set()
    for document in documents:
        name = document["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[\w .-]+\.pdf", name, re.IGNORECASE) or name.startswith("."):
            raise ValueError("Unsafe PDF attachment name")
        if name.casefold() in names:
            raise ValueError("Duplicate attachment name")
        names.add(name.casefold())
        content = Path(document["path"]).read_bytes()
        if not content.startswith(b"%PDF-"):
            raise ValueError("Attachment is not a PDF")
        message.add_attachment(content, maintype="application", subtype="pdf", filename=name)
    return message.as_bytes()
