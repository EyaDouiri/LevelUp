import copy
from email import policy
from email.parser import BytesParser
from pathlib import Path
import tempfile
import unittest

from application_documents import build_documents, build_email

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


PROFILE = {
    "name": "Zo\u00e9 Test", "title": "\u00c9l\u00e8ve ing\u00e9nieure", "email": "zoe@example.org",
    "phone": "+216 00 000 000", "linkedin": "https://example.org/zoe?a=1&b=2",
    "profile_summary": "Analyse <fiable> & donn\u00e9es, sans invention.",
    "target_criteria": {"start_date": "Fin janvier 2027"},
    "experience": [
        {"role": "Analyste preuve A", "company": "Assurance", "period": "Ao\u00fbt 2026 (en cours)",
         "bullets": ["Calcul exact < 5 & contr\u00f4le > 2"]},
        {"role": "Analyste preuve B", "bullets": ["Autre preuve"]}],
    "education": [{"degree": "Double dipl\u00f4me", "school": "Universit\u00e9", "period": "2027"}],
    "projects": [{"title": "Projet preuve A", "description": "GLM"},
                 {"title": "Projet preuve B", "description": "RAG"}],
    "skills": {"data": ["Python", "SQL"], "langues": ["Fran\u00e7ais"]},
}
APPLICATION = {"title": "PFE", "company": "Entreprise <test>", "url": "https://example.org/job",
               "email_subject": "Candidature Zo\u00e9", "email_body": "Madame, Monsieur,\n\n\u00c9tude <exacte> & motiv\u00e9e.",
               "cv_plan": {"experience_ids": ["exp:0"], "project_ids": ["project:1"], "skill_keys": ["data"]}}


class DocumentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_exports_and_mime_integrity(self):
        before = copy.deepcopy(PROFILE)
        docs = build_documents(PROFILE, APPLICATION, self.root)
        self.assertEqual([d["name"] for d in docs], ["CV.pdf", "lettre.pdf"])
        message = BytesParser(policy=policy.default).parsebytes(build_email(PROFILE, APPLICATION, docs))
        self.assertIsNone(message["To"])
        self.assertEqual(str(message["Subject"]), APPLICATION["email_subject"])
        self.assertEqual(message.get_body().get_content().replace("\r\n", "\n").rstrip(), APPLICATION["email_body"])
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 2)
        for attachment, doc in zip(attachments, docs):
            self.assertEqual(attachment.get_filename(), doc["name"])
            self.assertEqual(attachment.get_content_type(), "application/pdf")
            self.assertEqual(attachment.get_payload(decode=True), Path(doc["path"]).read_bytes())
        self.assertEqual(PROFILE, before)

    @unittest.skipIf(PdfReader is None, "pypdf not available")
    def test_selected_evidence_and_literal_french(self):
        docs = build_documents(PROFILE, APPLICATION, self.root)
        reader = PdfReader(docs[0]["path"])
        self.assertLessEqual(len(reader.pages), 2)
        text = "\n".join(p.extract_text() for p in reader.pages)
        for expected in ["Zo\u00e9", "Analyste preuve A", "Projet preuve B", "Double dipl\u00f4me",
                         "<fiable> &", "(en cours)", "Fin janvier 2027", "Calcul exact < 5 &"]:
            self.assertIn(expected, text)
        for excluded in ["Analyste preuve B", "Projet preuve A", "Fran\u00e7ais"]:
            self.assertNotIn(excluded, text)
        self.assertTrue(any(p.get("/Annots") for p in reader.pages))
        letter = "\n".join(p.extract_text() for p in PdfReader(docs[1]["path"]).pages)
        self.assertIn("\u00c9tude <exacte> & motiv\u00e9e.", letter)

    @unittest.skipIf(PdfReader is None, "pypdf not available")
    def test_empty_plan_includes_all(self):
        docs = build_documents(PROFILE, {**APPLICATION, "cv_plan": {}}, self.root)
        text = "\n".join(p.extract_text() for p in PdfReader(docs[0]["path"]).pages)
        for expected in ["Analyste preuve A", "Analyste preuve B", "Projet preuve A", "Projet preuve B", "Fran\u00e7ais"]:
            self.assertIn(expected, text)

    def test_invalid_ids_fail_before_writing(self):
        for field, value in [("experience_ids", ["exp:99"]), ("experience_ids", ["exp:-1"]),
                             ("experience_ids", ["project:0"]), ("experience_ids", "exp:0"),
                             ("project_ids", ["project:01"]), ("skill_keys", ["invented"]),
                             ("project_ids", [None])]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build_documents(PROFILE, {"cv_plan": {field: value}}, self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_email_recipient_and_header_injection(self):
        message = BytesParser(policy=policy.default).parsebytes(build_email(PROFILE, {**APPLICATION, "recipient": "hr@example.org"}, []))
        self.assertEqual(message["To"], "hr@example.org")
        for field in ["recipient", "email_subject"]:
            with self.assertRaises(ValueError):
                build_email(PROFILE, {**APPLICATION, field: "a\r\nBcc: other@example.org"}, [])

    def test_attachment_validation(self):
        docs = build_documents(PROFILE, APPLICATION, self.root)
        for bad in ["../CV.pdf", "..\\CV.pdf", "CV.pdf\r\nX: y", "/CV.pdf"]:
            with self.assertRaises(ValueError):
                build_email(PROFILE, APPLICATION, [{**docs[0], "name": bad}])
        with self.assertRaises(ValueError):
            build_email(PROFILE, APPLICATION, [docs[0], docs[0]])
        invalid = self.root / "invalid.pdf"
        invalid.write_bytes(b"not a pdf")
        with self.assertRaises(ValueError):
            build_email(PROFILE, APPLICATION, [{"name": "invalid.pdf", "path": invalid}])

    @unittest.skipIf(PdfReader is None, "pypdf not available")
    def test_long_content_is_preserved(self):
        profile = copy.deepcopy(PROFILE)
        profile["experience"][0]["bullets"] = ["Evidence %03d : " % i + "analyse exacte " * 20 for i in range(70)]
        docs = build_documents(profile, APPLICATION, self.root)
        text = "\n".join(p.extract_text() for p in PdfReader(docs[0]["path"]).pages)
        self.assertIn("Evidence 069", text)


if __name__ == "__main__":
    unittest.main()
