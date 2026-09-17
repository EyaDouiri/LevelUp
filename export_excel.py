"""Excel working view over the SQLite registry.

SQLite stays the source of truth; suivi_candidatures.xlsx is a human-friendly
work sheet: one row per offer, a Statut dropdown, and row colors driven ONLY by
conditional formatting so every re-export keeps the same look.

Commands:
    python export_excel.py export [path]   -- (re)build the workbook, preserving
                                              user-defined Statut cells and appending
                                              brand-new offers at the bottom.
    python export_excel.py sync  [path]    -- read Statut changes back into the
                                              database (Rejeté -> rejected, etc.).
"""
import os
import sys
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from storage import ROOT, Store, canonical_url

EXCEL_PATH = Path(os.getenv("JAMIL_EXCEL_PATH", str(ROOT / "data" / "suivi_candidatures.xlsx")))

COLUMNS = ["Titre", "Entreprise", "Ville", "Score", "Lien", "Statut", "Date de détection", "Notes"]
STATUT_COLUMN = "F"

STATUTS = ["Nouveau", "Intéressant", "Candidature envoyée", "Rejeté", "Clôturé"]

EXCEL_TO_DB = {
    "Nouveau": "discovered",
    "Intéressant": "shortlisted",
    "Candidature envoyée": "applied",
    "Rejeté": "rejected",
    "Clôturé": "archived",
}

DB_TO_EXCEL = {
    "discovered": "Nouveau",
    "shortlisted": "Intéressant",
    "preparing": "Intéressant",
    "review_needed": "Intéressant",
    "prepared": "Intéressant",
    "approved": "Intéressant",
    "draft_created": "Intéressant",
    "applied": "Candidature envoyée",
    "interview": "Candidature envoyée",
    "offer": "Candidature envoyée",
    "rejected": "Rejeté",
    "archived": "Clôturé",
}

# Statut value -> light background. Applied only as conditional-formatting rules.
ROW_FILLS = {
    "Rejeté": "FFC7CE",
    "Clôturé": "D9D9D9",
    "Candidature envoyée": "C6EFCE",
}

WIDTHS = [42, 24, 22, 8, 60, 20, 14, 36]


def _offer_rows(store):
    """Rows in registry order (oldest detection first stays appendable)."""
    return sorted(store.applications(), key=lambda r: r.get("created_at", ""))


def _by_url(store):
    return {offer["url"]: offer for offer in store.applications()}


def _sheet_date(value):
    if not value:
        return ""
    return str(value)[:10]


def _export_cells(offer):
    return [
        offer.get("title") or "",
        offer.get("company") or "",
        offer.get("location") or "",
        offer.get("qualification_score") if offer.get("qualification_score") is not None else "",
        offer.get("url") or "",
        "",  # Statut: decided separately (preserved or mapped) and managed by caller
        _sheet_date(offer.get("discovered_at") or offer.get("created_at")),
        offer.get("notes") or "",
    ]


def _apply_formatting(ws, last_row):
    """Data validation dropdown + conditional-format row colors. No hard paint."""
    ws.freeze_panes = "A2"
    for column, width in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(column)].width = width
    if last_row < 2:
        return
    options = ",".join(STATUTS)
    validation = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True, showDropDown=False)
    validation.error = "Choisis une valeur dans la liste."
    validation.errorTitle = "Statut invalide"
    ws.add_data_validation(validation)
    validation.add(f"{STATUT_COLUMN}2:{STATUT_COLUMN}{last_row}")
    for value, color in ROW_FILLS.items():
        rule = FormulaRule(
            formula=[f'${STATUT_COLUMN}2="{value}"'],
            fill=PatternFill(start_color=color, end_color=color, fill_type="solid"),
        )
        ws.conditional_formatting.add(f"A2:H{last_row}", rule)


def export_excel(store, path=None):
    """(Re)build the workbook from the database.

    Statut cells already present in the file are preserved (they may have been
    chosen by hand in Excel); only offers never seen before are appended, at the
    bottom, with their auto-mapped Statut. Nothing is ever over-painted.
    """
    path = Path(path or EXCEL_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    preserved = {}
    if path.is_file():
        try:
            existing = load_workbook(path, data_only=False)
            sheet = existing.active
            for row in sheet.iter_rows(min_row=2, values_only=True):
                link = row[4] if len(row) > 4 else None
                statut = row[5] if len(row) > 5 else None
                if link and statut:
                    preserved[str(link)] = str(statut).strip()
        except Exception:
            preserved = {}

    wb = Workbook()
    ws = wb.active
    ws.title = "Suivi candidatures"
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="center")

    registry = _by_url(store)
    known = set()
    rows = 0

    def append(offer):
        nonlocal rows
        cells = _export_cells(offer)
        statut = preserved.get(offer["url"])
        if not statut:
            statut = DB_TO_EXCEL.get(offer.get("status"), "Nouveau")
        cells[5] = statut
        ws.append(cells)
        rows += 1
        known.add(offer["url"])

    # Preserve the existing file's row order first (refreshing the other columns).
    if preserved:
        ordered = [offer for offer in _offer_rows(store) if offer["url"] in preserved]
        for offer in ordered:
            append(offer)
    # Brand-new offers are appended at the bottom, still in paired order.
    for offer in _offer_rows(store):
        if offer["url"] not in known:
            append(offer)

    _apply_formatting(ws, 1 + rows)
    wb.save(path)
    return path, rows


def sync_excel(store, path=None):
    """Read user-edited Statut cells back into SQLite.

    Rejeté and Clôturé map to rejected/archived which the hunt shortlisting
    already excludes, so those offers are never re-proposed.
    """
    path = Path(path or EXCEL_PATH)
    if not path.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {path} — lance d'abord export_excel export")
    wb = load_workbook(path, data_only=False)
    ws = wb.active
    registry = _by_url(store)
    changes = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        link = str(row[4]).strip() if len(row) > 4 and row[4] else ""
        statut = str(row[5]).strip() if len(row) > 5 and row[5] else ""
        if not link or statut not in EXCEL_TO_DB:
            continue
        offer = registry.get(canonical_url(link))
        if not offer:
            continue
        target = EXCEL_TO_DB[statut]
        if offer.get("status") == target:
            continue
        store.update_application(offer["id"], {"status": target})
        changes.append({"url": link, "from": offer.get("status"), "to": target})
    return changes


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in {"export", "sync"}:
        print("Usage : python export_excel.py export | sync [fichier.xlsx]")
        return 2
    command, *rest = args
    path = Path(rest[0]) if rest else None
    store = Store()
    if command == "export":
        path, count = export_excel(store, path)
        print(f"{count} offre(s) exportée(s) vers {path}")
    else:
        try:
            changes = sync_excel(store, path)
        except FileNotFoundError as exc:
            print(exc)
            return 1
        print(f"{len(changes)} ligne(s) synchronisée(s)")
        for change in changes:
            print(f"- {change['url'][:70]} : {change['from']} -> {change['to']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())