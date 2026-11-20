"""LinkedIn: caricamento del video a parti, post sul profilo, testo «little», scadenza del token."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import instagram as ig  # noqa: E402
import linkedin as li  # noqa: E402

ENV = {"LINKEDIN_TOKEN": "TOK", "LINKEDIN_AUTORE": "urn:li:person:abc", "LINKEDIN_SCADENZA": "2099-01-01"}


class Testo(unittest.TestCase):
    def test_caratteri_riservati_preceduti_dalla_barra(self):
        t = li.testo_little("Studio (RCT) su #NIV [n=10] @PS <b> a_b *c* ~d {e} |f| \\g")
        for c in "()#[]@<>_*~{}|":
            self.assertIn("\\" + c, t)
        self.assertIn("\\\\g", t)

    def test_lunghezza(self):
        self.assertEqual(len(li.testo_little("a" * 5000)), li.TESTO_MAX)


class Scadenza(unittest.TestCase):
    def test_avviso_dieci_giorni_prima(self):
        with mock.patch.dict(os.environ, {"LINKEDIN_SCADENZA": "2026-12-01"}):
            with self.assertLogs("linkedin", "ERROR") as lg:
                self.assertTrue(li.controlla_scadenza(date(2026, 11, 25)))
            self.assertIn("scade tra 6 giorni", lg.output[0])
            with self.assertNoLogs("linkedin", "ERROR"):
                self.assertTrue(li.controlla_scadenza(date(2026, 11, 1)))

    def test_scaduto_non_pubblica(self):
        with mock.patch.dict(os.environ, {"LINKEDIN_SCADENZA": "2026-11-01"}):
            with self.assertLogs("linkedin", "ERROR") as lg:
                self.assertFalse(li.controlla_scadenza(date(2026, 11, 20)))
        self.assertIn("scaduto", lg.output[0])

    def test_scadenza_assente_o_sbagliata(self):
        with mock.patch.dict(os.environ, {"LINKEDIN_SCADENZA": ""}):
            self.assertTrue(li.controlla_scadenza())
        with mock.patch.dict(os.environ, {"LINKEDIN_SCADENZA": "boh"}):
            self.assertTrue(li.controlla_scadenza())


class Pubblicazione(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self.f.write(b"0123456789" * 10)
        self.f.close()
        self.chiamate = []

    def tearDown(self):
        os.unlink(self.f.name)

    def finto(self, metodo, url, intest=None, dati=None, timeout=0):
        self.chiamate.append((metodo, url, intest, dati))
        if "initializeUpload" in url:
            return 200, {}, json.dumps({"value": {"video": "urn:li:video:V1", "uploadToken": "",
                                                  "uploadInstructions": [
                                                      {"uploadUrl": "https://up/1", "firstByte": 0, "lastByte": 59},
                                                      {"uploadUrl": "https://up/2", "firstByte": 60, "lastByte": 99}]}})
        if url.startswith("https://up/"):
            return 200, {"ETag": "e" + url[-1]}, ""
        if "finalizeUpload" in url:
            return 200, {}, ""
        if "/videos/" in url:
            return 200, {}, json.dumps({"status": "AVAILABLE"})
        if url.endswith("/posts"):
            return 201, {"x-restli-id": "urn:li:share:9"}, ""
        raise AssertionError(url)

    def test_video_a_parti_poi_post_sul_profilo(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(li, "_http", self.finto):
            self.assertEqual(li.pubblica_video(self.f.name, "Titolo", "Testo (con) #tag"), "urn:li:share:9")
        put = [c for c in self.chiamate if c[0] == "PUT"]
        self.assertEqual([len(c[3]) for c in put], [60, 40])
        fine = json.loads([c for c in self.chiamate if "finalizeUpload" in c[1]][0][3])
        self.assertEqual(fine["finalizeUploadRequest"]["uploadedPartIds"], ["e1", "e2"])
        post = json.loads(self.chiamate[-1][3])
        self.assertEqual(post["author"], "urn:li:person:abc")
        self.assertEqual(post["visibility"], "PUBLIC")
        self.assertEqual(post["content"]["media"]["id"], "urn:li:video:V1")
        self.assertEqual(post["commentary"], "Testo \\(con\\) \\#tag")
        self.assertEqual(self.chiamate[-1][2]["LinkedIn-Version"], li.VERSIONE)

    def test_token_scaduto_401_dice_cosa_fare(self):
        def f(*a, **k):
            raise li.ErroreLinkedIn("HTTP 401: Invalid access token")
        with mock.patch.dict(os.environ, ENV), mock.patch.object(li, "_http", f):
            with self.assertLogs("linkedin", "ERROR") as lg:
                self.assertIsNone(li.pubblica_video(self.f.name, "t", "x"))
        self.assertIn("linkedin_autorizza.py", lg.output[0])

    def test_video_non_elaborato(self):
        def f(metodo, url, *a, **k):
            if "/videos/" in url and metodo == "GET":
                return 200, {}, json.dumps({"status": "PROCESSING_FAILED"})
            return self.finto(metodo, url, *a, **k)
        with mock.patch.dict(os.environ, ENV), mock.patch.object(li, "_http", f), \
                self.assertLogs("linkedin", "ERROR"):
            self.assertIsNone(li.pubblica_video(self.f.name, "t", "x"))
        self.assertFalse([c for c in self.chiamate if c[1].endswith("/posts")])

    def test_senza_secret_non_fa_nulla(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(li, "_http") as h:
            self.assertIsNone(li.pubblica_video(self.f.name, "t", "x"))
        h.assert_not_called()


class Cfg:
    IG_DIR = tempfile.mkdtemp()
    LI_ABILITATO = True
    YT_TITOLI = {"completo": "EM Weekly Digest · il podcast della settimana"}
    FB_REEL_INVITO = ""
    SOLO_REEL = None


class Aggancio(unittest.TestCase):
    def test_testo_con_dichiarazione_ia_e_titolo_con_data(self):
        with mock.patch.dict(os.environ, ENV), mock.patch.object(li, "pubblica_video", return_value="P") as p:
            self.assertEqual(ig._linkedin("p.mp4", "Testo dal link in bio.", Cfg, False), "P")
        video, titolo, testo = p.call_args[0]
        self.assertTrue(titolo.startswith("EM Weekly Digest · il podcast della settimana del "))
        self.assertIn("intelligenza artificiale", testo)
        self.assertNotIn("link in bio", testo)

    def test_prova_a_vuoto_spento_solo_instagram(self):
        with mock.patch.object(li, "pubblica_video") as p:
            self.assertEqual(ig._linkedin("p.mp4", "T", Cfg, True), "dry-run")
            class Spento(Cfg):
                LI_ABILITATO = False
            class SoloIG(Cfg):
                SOLO_INSTAGRAM = True
            self.assertIsNone(ig._linkedin("p.mp4", "T", Spento, False))
            self.assertIsNone(ig._linkedin("p.mp4", "T", SoloIG, False))
        p.assert_not_called()
        self.assertTrue(os.path.exists(os.path.join(Cfg.IG_DIR, "testo-linkedin.txt")))

    def test_ripubblicazione_puo_scegliere_li_podcast(self):
        class Solo(Cfg):
            SOLO_REEL = {"ig_podcast"}
        self.assertFalse(ig._fa(Solo, "li_podcast"))
        self.assertTrue(ig._fa(Cfg, "li_podcast"))


if __name__ == "__main__":
    unittest.main()
