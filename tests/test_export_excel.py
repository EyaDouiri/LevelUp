"""Tests for Excel export/sync (Volet 2 – suivi candidatures)."""
import tempfile
import unittest
from pathlib import Path

from export_excel import (
    COLUMNS, DB_TO_EXCEL, EXCEL_TO_DB, STATUTS, WIDTHS,
    _export_cells, _sheet_date, export_excel, sync_excel,
)
from storage import Store


def _make_store(tmp, offers=None):
    """Populate a fresh Store with optional offer dicts (no legacy CSV import)."""
    store = Store(path=tmp / "test.sqlite3")
    for offer in (offers or []):
        store.add_application(offer)
    return store


def _fake_offer(idx=0, url=None, title="Data analyst", status="discovered"):
    return {
        "url": url or f"https://example.com/job/{idx}",
        "title": title,
        "company": "Acme",
        "location": "Paris",
        "status": status,
        "qualification_score": 80,
    }


# ── Mappings ───────────────────────────────────────────────────────────────

class TestMappings(unittest.TestCase):
    def test_every_db_status_has_excel_mapping(self):
        for status in {"discovered", "shortlisted", "preparing", "review_needed",
                       "approved", "prepared", "draft_created", "applied",
                       "interview", "offer", "rejected", "archived"}:
            self.assertIn(status, DB_TO_EXCEL)
            self.assertIn(DB_TO_EXCEL[status], STATUTS)

    def test_every_excel_status_has_db_mapping(self):
        for statut in STATUTS:
            self.assertIn(statut, EXCEL_TO_DB)
            self.assertIn(EXCEL_TO_DB[statut], {"discovered", "shortlisted", "applied",
                                                  "rejected", "archived"})

    def test_roundtrip_bidirectional(self):
        for db_status, excel_val in DB_TO_EXCEL.items():
            self.assertIn(excel_val, EXCEL_TO_DB)


# ── Helpers ────────────────────────────────────────────────────────────────

class TestSheetDate(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_sheet_date(None), "")
        self.assertEqual(_sheet_date(""), "")

    def test_iso_string(self):
        self.assertEqual(_sheet_date("2027-01-15T10:30:00Z"), "2027-01-15")

    def test_already_short(self):
        self.assertEqual(_sheet_date("2027-01-15"), "2027-01-15")


class TestExportCells(unittest.TestCase):
    def test_full_offer(self):
        offer = _fake_offer()
        cells = _export_cells(offer)
        self.assertEqual(len(cells), 8)
        self.assertEqual(cells[0], "Data analyst")   # Titre
        self.assertEqual(cells[1], "Acme")           # Entreprise
        self.assertEqual(cells[2], "Paris")           # Ville
        self.assertEqual(cells[3], 80)                # Score
        self.assertEqual(cells[4], "https://example.com/job/0")  # Lien
        self.assertEqual(cells[5], "")                # Statut (managed by caller)
        self.assertIsInstance(cells[6], str)          # Date
        self.assertEqual(cells[7], "")                # Notes

    def test_missing_fields(self):
        cells = _export_cells({"url": "https://example.com/x"})
        self.assertEqual(cells[0], "")
        self.assertEqual(cells[3], "")  # Score empty when None


# ── Export ─────────────────────────────────────────────────────────────────

class TestExportExcel(unittest.TestCase):
    def test_creates_fresh_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp, [_fake_offer()])
            path, count = export_excel(store, path=tmp / "out.xlsx")
            self.assertEqual(count, 1)
            self.assertTrue(path.exists())

    def test_headers_are_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp)
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path, data_only=True)
            ws = wb.active
            headers = [cell.value for cell in ws[1]]
            self.assertEqual(headers, COLUMNS)

    def test_column_widths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp, [_fake_offer()])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            from openpyxl.utils import get_column_letter
            wb = load_workbook(path)
            ws = wb.active
            for i, width in enumerate(WIDTHS, start=1):
                col_letter = get_column_letter(i)
                self.assertAlmostEqual(ws.column_dimensions[col_letter].width, width, delta=1)

    def test_freeze_panes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp, [_fake_offer()])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path)
            self.assertEqual(wb.active.freeze_panes, "A2")

    def test_data_validation_on_statut_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp, [_fake_offer()])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path)
            ws = wb.active
            dvs = ws.data_validations.dataValidation
            self.assertTrue(len(dvs) >= 1)
            dv = dvs[0]
            self.assertIn("F2", str(dv.sqref))

    def test_conditional_formatting_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp, [_fake_offer()])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path)
            ws = wb.active
            cf_rules = list(ws.conditional_formatting)
            self.assertGreaterEqual(len(cf_rules), 1)

    def test_offer_data_appears(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            offer = _fake_offer()
            store = _make_store(tmp, [offer])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path, data_only=True)
            ws = wb.active
            row2 = [cell.value for cell in ws[2]]
            self.assertEqual(row2[0], "Data analyst")
            self.assertEqual(row2[1], "Acme")
            self.assertEqual(row2[4], "https://example.com/job/0")
            # Statut mapped from discovered -> Nouveau
            self.assertEqual(row2[5], "Nouveau")

    def test_multiple_offers_sorted_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp, [
                _fake_offer(0, title="First"),
                _fake_offer(1, title="Second"),
                _fake_offer(2, title="Third"),
            ])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path, data_only=True)
            ws = wb.active
            titles = [ws.cell(row=r, column=1).value for r in range(2, 5)]
            self.assertEqual(titles, ["First", "Second", "Third"])

    def test_preserves_existing_statuts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp, [_fake_offer(0), _fake_offer(1)])
            # First export
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            # Manually set Statut to "Rejeté" in the file for offer 0
            from openpyxl import load_workbook
            wb = load_workbook(path)
            ws = wb.active
            ws.cell(row=2, column=6, value="Rejeté")
            wb.save(path)
            # Re-export
            path2, count = export_excel(store, path=path)
            wb2 = load_workbook(path2, data_only=True)
            ws2 = wb2.active
            self.assertEqual(ws2.cell(row=2, column=6).value, "Rejeté")
            self.assertEqual(count, 2)

    def test_new_offres_appended_at_bottom(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store1 = _make_store(tmp, [_fake_offer(0, title="Old")])
            path, _ = export_excel(store1, path=tmp / "out.xlsx")
            # Add a new offer
            store1.add_application(_fake_offer(1, title="New"))
            path2, count = export_excel(store1, path=path)
            from openpyxl import load_workbook
            wb = load_workbook(path2, data_only=True)
            ws = wb.active
            titles = [ws.cell(row=r, column=1).value for r in range(2, 2 + count)]
            self.assertIn("Old", titles)
            self.assertIn("New", titles)
            # "New" should be at the bottom (last data row)
            self.assertEqual(ws.cell(row=count + 1, column=1).value, "New")

    def test_db_to_excel_status_mapping_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            offer = _fake_offer(0, status="applied")
            store = _make_store(tmp, [offer])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path, data_only=True)
            ws = wb.active
            self.assertEqual(ws.cell(row=2, column=6).value, "Candidature envoyée")

    def test_empty_store_headers_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp)
            path, count = export_excel(store, path=tmp / "out.xlsx")
            self.assertEqual(count, 0)
            from openpyxl import load_workbook
            wb = load_workbook(path, data_only=True)
            ws = wb.active
            self.assertEqual([cell.value for cell in ws[1]], COLUMNS)
            self.assertEqual(ws.max_row, 1)


# ── Sync ───────────────────────────────────────────────────────────────────

class TestSyncExcel(unittest.TestCase):
    def test_sync_updates_db_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            offer = _fake_offer(0)
            store = _make_store(tmp, [offer])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            # Change Statut in the Excel file
            from openpyxl import load_workbook
            wb = load_workbook(path)
            ws = wb.active
            ws.cell(row=2, column=6, value="Rejeté")
            wb.save(path)
            # Sync
            changes = sync_excel(store, path=path)
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0]["to"], "rejected")
            # Verify DB
            updated = store.applications()
            self.assertEqual(updated[0]["status"], "rejected")

    def test_sync_no_change_when_same_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            offer = _fake_offer(0, status="discovered")
            store = _make_store(tmp, [offer])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            # Statut is already "Nouveau" (discovered), no manual change
            changes = sync_excel(store, path=path)
            self.assertEqual(len(changes), 0)

    def test_sync_maps_cloture_to_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            offer = _fake_offer(0)
            store = _make_store(tmp, [offer])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path)
            ws = wb.active
            ws.cell(row=2, column=6, value="Clôturé")
            wb.save(path)
            changes = sync_excel(store, path=path)
            self.assertEqual(changes[0]["to"], "archived")

    def test_sync_file_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp)
            with self.assertRaises(FileNotFoundError):
                sync_excel(store, path=tmp / "nonexistent.xlsx")

    def test_sync_ignores_empty_or_unknown_statut(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            offer = _fake_offer(0)
            store = _make_store(tmp, [offer])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path)
            ws = wb.active
            ws.cell(row=2, column=6, value="Statut bidon")
            wb.save(path)
            changes = sync_excel(store, path=path)
            self.assertEqual(len(changes), 0)

    def test_sync_preserves_offers_not_in_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = _make_store(tmp, [_fake_offer(0)])
            path, _ = export_excel(store, path=tmp / "out.xlsx")
            from openpyxl import load_workbook
            wb = load_workbook(path)
            ws = wb.active
            # Add a manual row with unknown URL
            ws.append(["Manual job", "", "", "", "https://unknown.com/x", "Nouveau", "", ""])
            wb.save(path)
            changes = sync_excel(store, path=path)
            self.assertEqual(len(changes), 0)


# ── CLI ────────────────────────────────────────────────────────────────────

class TestMain(unittest.TestCase):
    def test_export_command(self):
        from export_excel import main
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.xlsx"
            # Create a minimal DB first (no legacy CSV import)
            Store(path=Path(tmp) / "test.sqlite3")
            import os
            old_db = os.environ.get("JAMIL_DB")
            os.environ["JAMIL_DB"] = str(Path(tmp) / "test.sqlite3")
            try:
                ret = main(["export", str(path)])
                self.assertEqual(ret, 0)
                self.assertTrue(path.exists())
            finally:
                if old_db is not None:
                    os.environ["JAMIL_DB"] = old_db
                else:
                    os.environ.pop("JAMIL_DB", None)

    def test_sync_command_no_file(self):
        from export_excel import main
        with tempfile.TemporaryDirectory() as tmp:
            ret = main(["sync", str(Path(tmp) / "nonexistent.xlsx")])
            self.assertEqual(ret, 1)  # quiet error path returns a non-zero exit


if __name__ == "__main__":
    unittest.main()
