"""Pubblicazione su X: firma OAuth 1.0a, testo del post, caricamento del video a pezzi."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import instagram as ig  # noqa: E402
import x_social as x  # noqa: E402

CHIAVI = {"X_API_KEY": "k", "X_API_SECRET": "s", "X_ACCESS_TOKEN": "t", "X_ACCESS_SECRET": "ts"}


class Cfg:
    IG_DIR = "x"
    X_REEL = True
    X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET = "k", "s", "t", "ts"
    IG_HASHTAG = "#POCUS #EcografiaClinica #MedicinaDUrgenza #ProntoSoccorso #EBM"


class Firma(unittest.TestCase):
    def test_esempio_ufficiale_di_x(self):
        # Esempio della documentazione di X («Authorizing a request»).
        f = x.firma_oauth1(
            "POST", "https://api.twitter.com/1.1/statuses/update.json",
            {"status": "Hello Ladies + Gentlemen, a signed OAuth request!", "include_entities": "true"},
            "xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
            "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
            "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", 1318622958)
        self.assertEqual(f, "hCtSmYh+iHYCEqBWrE7C7hYmtUk=")

    def test_intestazione(self):
        h = x.intestazione_auth("POST", "https://api.x.com/2/tweets", {}, CHIAVI, nonce="n", timestamp=1)
        self.assertTrue(h.startswith("OAuth "))
        for c in ("oauth_consumer_key=\"k\"", "oauth_token=\"t\"", "oauth_signature_method=\"HMAC-SHA1\"",
                  "oauth_signature="):
            self.assertIn(c, h)
        self.assertNotIn("ts", h.replace("oauth_timestamp", ""))     # il segreto non e' mai nell'intestazione


class Testo(unittest.TestCase):
    def test_titolo_e_tre_hashtag(self):
        t = x.testo_post("POCUS Weekly Digest — Settimana 45/2026\n\nResto della didascalia", Cfg)
        self.assertEqual(t, "POCUS Weekly Digest — Settimana 45/2026\n\n#POCUS #EcografiaClinica #MedicinaDUrgenza")

    def test_mai_oltre_280_caratteri(self):
        t = x.testo_post("T" * 500, Cfg)
        self.assertLessEqual(len(t), 280)
        self.assertIn("…", t)
        self.assertTrue(t.endswith("#MedicinaDUrgenza"))

    def test_hashtag_e_invito_personalizzati(self):
        class C(Cfg):
            X_HASHTAG = "#NIV"
            X_INVITO = "Iscrizione: link nel profilo"
        self.assertEqual(x.testo_post("Titolo", C), "Titolo\n\nIscrizione: link nel profilo\n\n#NIV")


class Caricamento(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.video = os.path.join(self.d, "r.mp4")
        with open(self.video, "wb") as f:
            f.write(b"\x01" * (x.CHUNK + 10))                  # due pezzi

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def lancia(self, finalize=None, stati=()):
        chiamate = []
        stati = list(stati)

        def chiama(metodo, percorso, chiavi, query=None, corpo_json=None, multipart=None, timeout=120):
            chiamate.append((metodo, percorso, corpo_json, query))
            if percorso.endswith("/initialize"):
                return {"data": {"id": "M1"}}
            if percorso.endswith("/finalize"):
                return finalize if finalize is not None else {"data": {"id": "M1"}}
            if percorso == "/2/media/upload":
                return {"data": {"processing_info": stati.pop(0)}}
            if percorso == "/2/tweets":
                return {"data": {"id": "T1"}}
            return {}
        with mock.patch.object(x, "_chiama", side_effect=chiama), mock.patch("time.sleep"):
            r = x.pubblica_video(self.video, "testo", CHIAVI)
        return r, chiamate

    def test_flusso_completo(self):
        r, ch = self.lancia()
        self.assertEqual(r, "T1")
        percorsi = [c[1] for c in ch]
        self.assertEqual(percorsi, ["/2/media/upload/initialize", "/2/media/upload/M1/append",
                                    "/2/media/upload/M1/append", "/2/media/upload/M1/finalize", "/2/tweets"])
        self.assertEqual(ch[0][2], {"media_type": "video/mp4", "total_bytes": x.CHUNK + 10,
                                    "media_category": "tweet_video"})
        self.assertEqual(ch[-1][2], {"text": "testo", "media": {"media_ids": ["M1"]}})

    def test_aspetta_l_elaborazione(self):
        r, ch = self.lancia(finalize={"data": {"processing_info": {"state": "pending", "check_after_secs": 1}}},
                            stati=[{"state": "in_progress", "check_after_secs": 1}, {"state": "succeeded"}])
        self.assertEqual(r, "T1")
        self.assertEqual(sum(1 for c in ch if c[1] == "/2/media/upload"), 2)

    def test_video_rifiutato(self):
        r, ch = self.lancia(finalize={"data": {"processing_info": {"state": "failed", "error": {"code": 1}}}})
        self.assertIsNone(r)
        self.assertNotIn("/2/tweets", [c[1] for c in ch])

    def test_errore_dell_api_non_solleva(self):
        with mock.patch.object(x, "_chiama", side_effect=RuntimeError("HTTP 402: CreditsDepleted")):
            self.assertIsNone(x.pubblica_video(self.video, "t", CHIAVI))


class Instradamento(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = type("C", (Cfg,), {"IG_DIR": self.d})

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_spento_o_prova_solo_instagram(self):
        for k, v in (("X_REEL", False), ("SOLO_INSTAGRAM", True)):
            cfg = type("C", (self.cfg,), {k: v})
            with mock.patch.object(x, "pubblica_video") as pub:
                self.assertIsNone(x.pubblica_reel("v.mp4", "T", cfg))
            self.assertFalse(pub.called, k)

    def test_senza_chiavi_non_pubblica(self):
        cfg = type("C", (self.cfg,), {"X_ACCESS_SECRET": ""})
        with mock.patch.object(x, "pubblica_video") as pub:
            self.assertIsNone(x.pubblica_reel("v.mp4", "T", cfg))
        self.assertFalse(pub.called)

    def test_con_le_chiavi_pubblica(self):
        with mock.patch.object(x, "pubblica_video", return_value="T9") as pub:
            self.assertEqual(x.pubblica_reel("v.mp4", "Titolo", self.cfg), "T9")
        self.assertEqual(pub.call_args[0][1].split("\n")[0], "Titolo")

    def test_prova_a_vuoto_salva_il_testo(self):
        with mock.patch.object(x, "pubblica_video") as pub:
            self.assertEqual(x.pubblica_reel("v.mp4", "Titolo", self.cfg, dry_run=True), "dry-run")
        self.assertFalse(pub.called)
        with open(os.path.join(self.d, "testo-x.txt"), encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("Titolo"))

    def test_chiavi_da_config(self):
        self.assertEqual(x.chiavi_da_config(Cfg), CHIAVI)

    def test_dal_reel_delle_schede(self):
        with mock.patch("x_social.pubblica_reel", return_value="T") as pub:
            self.assertEqual(ig._reel_x("v.mp4", "D", self.cfg, True), "T")
        pub.assert_called_once_with("v.mp4", "D", self.cfg, True)

    def test_un_errore_di_x_non_ferma_il_reel(self):
        with mock.patch("x_social.pubblica_reel", side_effect=RuntimeError("boom")):
            self.assertIsNone(ig._reel_x("v.mp4", "D", self.cfg, False))


if __name__ == "__main__":
    unittest.main()
