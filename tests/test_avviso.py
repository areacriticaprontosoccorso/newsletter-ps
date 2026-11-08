"""avviso.py: email solo se un invio e' fallito o incompleto, leggendo solo il run in corso."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import avviso  # noqa: E402

LOG = """2026-11-08 06:13:37,013 [WARNING] test: corsi.json non leggibile
2026-11-08 06:13:38,000 [ERROR] errore dei test, prima del run
2026-11-08 06:20:00,100 [INFO] === Newsletter ===
2026-11-08 06:20:01,100 [WARNING] ESearch tentativo 1/3 fallito: timeout
2026-11-08 06:20:02,100 [WARNING] Instagram: Reel non pubblicato (codice 9007)
2026-11-08 06:20:03,100 [ERROR] Post Facebook non programmato: token=abc123def&x=1 per mario@esempio.it
2026-11-08 06:20:04,100 [ERROR] Post Facebook non programmato: token=abc123def&x=1 per mario@esempio.it
2026-11-08 06:20:05,100 [WARNING] immagine 'x.png' ignorata
2026-11-08 06:20:06,100 [INFO] fine
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.log = os.path.join(self.d, "n.log")
        with open(self.log, "w", encoding="utf-8") as f:
            f.write(LOG)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)


class Righe(Base):
    def test_solo_righe_dopo_l_avvio(self):
        r = avviso.righe_problema(self.log, "2026-11-08 06:20:00")
        testo = "\n".join(r)
        self.assertNotIn("prima del run", testo)
        self.assertNotIn("test: corsi.json", testo)

    def test_error_e_warning_gravi_si_i_tentativi_no(self):
        r = avviso.righe_problema(self.log, "2026-11-08 06:20:00")
        testo = "\n".join(r)
        self.assertIn("Reel non pubblicato", testo)
        self.assertIn("Post Facebook non programmato", testo)
        self.assertNotIn("tentativo 1/3", testo)
        self.assertNotIn("x.png", testo)               # warning qualunque: rumore

    def test_righe_ripetute_una_volta_sola(self):
        r = avviso.righe_problema(self.log, "2026-11-08 06:20:00")
        self.assertEqual(sum("Post Facebook" in x for x in r), 1)

    def test_token_e_indirizzi_non_finiscono_nell_email(self):
        testo = "\n".join(avviso.righe_problema(self.log, "2026-11-08 06:20:00"))
        self.assertNotIn("abc123def", testo)
        self.assertNotIn("mario@esempio.it", testo)

    def test_senza_inizio_nessuna_riga(self):
        self.assertEqual(avviso.righe_problema(self.log, ""), [])

    def test_log_assente(self):
        self.assertEqual(avviso.righe_problema(os.path.join(self.d, "no.log"), "2026-01-01 00:00:00"), [])


class Costruisci(unittest.TestCase):
    def test_tutto_bene_nessun_avviso(self):
        self.assertIsNone(avviso.costruisci("POCUS Weekly Digest", "success", []))

    def test_job_fallito_anche_senza_righe(self):
        oggetto, testo = avviso.costruisci("POCUS Weekly Digest", "failure", [], "https://x/run/1")
        self.assertIn("ERRORE", oggetto)
        self.assertIn("https://x/run/1", testo)

    def test_job_ok_ma_problemi_nel_log(self):
        oggetto, testo = avviso.costruisci("NIV Weekly Digest", "success", ["06:20:02 [ERROR] x"])
        self.assertIn("incompleto", oggetto)
        self.assertIn("06:20:02 [ERROR] x", testo)

    def test_elenco_lungo_accorciato(self):
        righe = [f"r{i}" for i in range(40)]
        _, testo = avviso.costruisci("X", "success", righe)
        self.assertIn("e altre 15", testo)


class Invio(Base):
    def amb(self, **k):
        base = {"GMAIL_USER": "mittente@esempio.it", "GMAIL_APP_PASSWORD": "pw", "AVVISI_EMAIL": "me@esempio.it",
                "NEWSLETTER_INIZIO": "2026-11-08 06:20:00", "JOB_STATUS": "success"}
        base.update(k)
        return base

    def test_destinatario_e_smtp(self):
        with mock.patch("avviso._invia_smtp") as smtp:
            self.assertTrue(avviso.invia("o", "t", self.amb()))
        msg = smtp.call_args[0][0]
        self.assertEqual(msg["To"], "me@esempio.it")
        self.assertEqual(msg["From"], "mittente@esempio.it")

    def test_senza_avvisi_email_scrive_al_mittente(self):
        with mock.patch("avviso._invia_smtp") as smtp:
            avviso.invia("o", "t", self.amb(AVVISI_EMAIL=""))
        self.assertEqual(smtp.call_args[0][0]["To"], "mittente@esempio.it")

    def test_con_gmail_token_usa_l_api(self):
        with mock.patch("avviso._invia_gmail_api") as api, mock.patch("avviso._invia_smtp") as smtp:
            avviso.invia("o", "t", self.amb(GMAIL_TOKEN="{}"))
        self.assertTrue(api.called)
        self.assertFalse(smtp.called)

    def test_errore_di_invio_non_solleva(self):
        with mock.patch("avviso._invia_smtp", side_effect=OSError("rete")):
            self.assertFalse(avviso.invia("o", "t", self.amb()))

    def test_senza_credenziali_non_solleva(self):
        self.assertFalse(avviso.invia("o", "t", {}))


class Main(Base):
    def lancia(self, **k):
        amb = {"GMAIL_USER": "m@e.it", "GMAIL_APP_PASSWORD": "pw", "NEWSLETTER_INIZIO": "2026-11-08 06:20:00",
               "JOB_STATUS": "success", "GITHUB_RUN_ID": "7", "GITHUB_REPOSITORY": "org/repo"}
        amb.update(k)
        import config as cfg
        with mock.patch.object(cfg, "LOG_FILE", self.log), mock.patch("avviso.invia") as invia:
            avviso.main(amb)
        return invia

    def test_problemi_nel_log_inviano(self):
        invia = self.lancia()
        self.assertTrue(invia.called)
        self.assertIn("https://github.com/org/repo/actions/runs/7", invia.call_args[0][1])

    def test_nessun_problema_nessun_invio(self):
        self.assertFalse(self.lancia(NEWSLETTER_INIZIO="2026-11-08 06:21:00").called)

    def test_prove_non_inviano(self):
        for k in ("DRY_RUN", "SOLO_INSTAGRAM", "SOLO_FACEBOOK"):
            self.assertFalse(self.lancia(**{k: "1", "JOB_STATUS": "failure"}).called, k)


if __name__ == "__main__":
    unittest.main()
