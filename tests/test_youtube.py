"""YouTube: caricamento (resumable), titoli, errori, e i punti in cui instagram.py lo chiama."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import instagram as ig  # noqa: E402
import youtube as yt  # noqa: E402

ENV = {"YT_CLIENT_ID": "id", "YT_CLIENT_SECRET": "sec", "YT_REFRESH_TOKEN": "rt"}


class Titoli(unittest.TestCase):
    def test_titolo_entro_100_e_senza_hashtag_in_coda(self):
        t = yt.pulisci_titolo("A" * 150 + " #Shorts #EM")
        self.assertLessEqual(len(t), 100)
        self.assertNotIn("#", t)
        self.assertNotIn("<", yt.pulisci_titolo("a <b> c"))

    def test_descrizione_entro_il_limite_in_byte(self):
        d = yt.pulisci_descrizione("è" * 6000)
        self.assertLessEqual(len(d.encode("utf-8")), yt.DESCRIZIONE_MAX)


class Caricamento(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self.f.write(b"x" * 1000)
        self.f.close()
        self.chiamate = []

    def tearDown(self):
        os.unlink(self.f.name)

    def finto(self, metodo, url, intest=None, dati=None, timeout=0):
        self.chiamate.append((metodo, url, intest, dati))
        if "oauth2" in url:
            return 200, {}, json.dumps({"access_token": "AT"})
        if "uploadType=resumable" in url:
            return 200, {"Location": "https://upload.example/abc"}, ""
        if url.startswith("https://upload.example"):
            return 200, {}, json.dumps({"id": "VID1"})
        if "part=status" in url:
            return 200, {}, json.dumps({"items": [{"status": {"privacyStatus": self.privacy}}]})
        raise AssertionError(url)

    privacy = "public"

    def test_senza_secret_non_fa_nulla(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(yt, "_http") as h:
            self.assertIsNone(yt.pubblica_video(self.f.name, "t", "d"))
        h.assert_not_called()

    def test_carica_con_metadati_giusti(self):
        with mock.patch.dict(os.environ, ENV, clear=True), mock.patch.object(yt, "_http", self.finto):
            r = yt.pubblica_video(self.f.name, "Titolo", "Descr", tags=["a"], sintetico=True, shorts=True)
        self.assertEqual(r, "VID1")
        meta = json.loads(self.chiamate[1][3])
        self.assertEqual(meta["snippet"]["title"], "Titolo")
        self.assertIn("#Shorts", meta["snippet"]["description"])
        self.assertTrue(meta["status"]["containsSyntheticMedia"])
        self.assertFalse(meta["status"]["selfDeclaredMadeForKids"])
        self.assertEqual(meta["status"]["privacyStatus"], "public")
        self.assertEqual(self.chiamate[1][2]["Authorization"], "Bearer AT")
        self.assertEqual(self.chiamate[2][0], "PUT")
        self.assertEqual(len(self.chiamate[2][3]), 1000)

    def test_video_bloccato_come_privato_e_segnalato(self):
        self.privacy = "private"
        with mock.patch.dict(os.environ, ENV, clear=True), mock.patch.object(yt, "_http", self.finto):
            with self.assertLogs("youtube", "WARNING") as lg:
                self.assertEqual(yt.pubblica_video(self.f.name, "t", "d"), "VID1")
        self.assertTrue(any("PRIVATE" in m.upper() and "API Services Audit" in m for m in lg.output))
        self.privacy = "public"

    def test_quota_esaurita_non_si_riprova(self):
        n = []

        def finto(metodo, url, *a, **k):
            if "oauth2" in url:
                return 200, {}, json.dumps({"access_token": "AT"})
            n.append(1)
            raise yt.ErroreYouTube("HTTP 403: quota [quotaExceeded]")
        with mock.patch.dict(os.environ, ENV, clear=True), mock.patch.object(yt, "_http", finto), \
             mock.patch.object(yt.time, "sleep"), self.assertLogs("youtube", "ERROR"):
            self.assertIsNone(yt.pubblica_video(self.f.name, "t", "d"))
        self.assertEqual(len(n), 1)

    def test_errore_di_rete_si_riprova(self):
        n = []

        def finto(metodo, url, *a, **k):
            if "oauth2" in url:
                return 200, {}, json.dumps({"access_token": "AT"})
            n.append(1)
            raise yt.ErroreYouTube("HTTP 503: backend")
        with mock.patch.dict(os.environ, ENV, clear=True), mock.patch.object(yt, "_http", finto), \
             mock.patch.object(yt.time, "sleep"), self.assertLogs("youtube", "ERROR"):
            self.assertIsNone(yt.pubblica_video(self.f.name, "t", "d"))
        self.assertEqual(len(n), yt.TENTATIVI)

    def test_token_scaduto_dice_cosa_fare(self):
        def finto(metodo, url, *a, **k):
            raise yt.ErroreYouTube('HTTP 400: invalid_grant: Token has been expired or revoked.')
        with mock.patch.dict(os.environ, ENV, clear=True), mock.patch.object(yt, "_http", finto), \
             self.assertLogs("youtube", "ERROR") as lg:
            self.assertIsNone(yt.pubblica_video(self.f.name, "t", "d"))
        self.assertIn("youtube_autorizza.py", lg.output[0])


class Cfg:
    IG_DIR = tempfile.mkdtemp()
    YT_ABILITATO = True
    YT_TITOLI = {"schede": "Schede", "podcast": "Estratto", "completo": "Podcast"}
    YT_PRIVACY, YT_TAGS, YT_CATEGORIA = "public", ["t"], "27"
    FB_REEL_INVITO = ""
    SOLO_REEL = None


class Aggancio(unittest.TestCase):
    def test_reel_come_short_e_podcast_completo_con_dichiarazione_ia(self):
        with mock.patch.dict(os.environ, ENV, clear=True), mock.patch.object(yt, "pubblica_video", return_value="V") as p:
            ig._reel_youtube("r.mp4", "Testo dal link in bio.", Cfg, False, "schede")
            ig._reel_youtube("p.mp4", "Testo", Cfg, False, "completo", ai=True, shorts=False)
        a, b = p.call_args_list
        self.assertTrue(a.kwargs["shorts"] and not a.kwargs["sintetico"])
        self.assertTrue(b.kwargs["sintetico"] and not b.kwargs["shorts"])
        self.assertTrue(a.args[1].startswith("Schede del "))
        self.assertNotIn("link in bio", a.args[2])
        self.assertIn("intelligenza artificiale", b.args[2])

    def test_prova_a_vuoto_scrive_solo_i_file(self):
        with mock.patch.object(yt, "pubblica_video") as p:
            self.assertEqual(ig._reel_youtube("r.mp4", "T", Cfg, True, "schede"), "dry-run")
        p.assert_not_called()
        self.assertTrue(os.path.exists(os.path.join(Cfg.IG_DIR, "descrizione-youtube-schede.txt")))

    def test_senza_titolo_o_spento_non_fa_nulla(self):
        class Spento(Cfg):
            YT_ABILITATO = False
        class SenzaTitolo(Cfg):
            YT_TITOLI = {"schede": "S"}
        self.assertIsNone(ig._reel_youtube("r.mp4", "T", Spento, True, "schede"))
        self.assertIsNone(ig._reel_youtube("p.mp4", "T", SenzaTitolo, True, "completo"))

    def test_solo_reel_limita_le_voci(self):
        class Solo(Cfg):
            SOLO_REEL = {"ig_schede"}
        self.assertFalse(ig._fa(Solo, "yt_schede"))
        self.assertTrue(ig._fa(Cfg, "yt_completo"))


if __name__ == "__main__":
    unittest.main()
