"""Reel su Facebook: stesso video di Instagram, pubblicato sulla Pagina (POCUS e EM)."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import instagram as ig  # noqa: E402

DIDASCALIA = ("POCUS Weekly Digest\n\nSintesi generate con supporto di intelligenza artificiale: verifica sempre "
              "la fonte originale. Iscrizione alla newsletter dal link in bio.\n\n#POCUS #EBM")


class Cfg:
    IG_DIR = "x"
    FB_REEL = True
    FB_REEL_INVITO = "Iscriviti alla newsletter: https://esempio.github.io/n/"
    FB_PAGE_ID = "PAGE"
    FB_PAGE_TOKEN = "TOK"
    FB_API_VERSION = "v21.0"


class Descrizione(unittest.TestCase):
    def test_link_in_bio_sostituito_con_l_indirizzo(self):
        t = ig.descrizione_reel_facebook(DIDASCALIA, Cfg)
        self.assertNotIn("link in bio", t)
        self.assertIn("verifica sempre la fonte originale. Iscriviti alla newsletter: https://esempio.github.io/n/", t)
        self.assertIn("#POCUS #EBM", t)

    def test_senza_invito_la_frase_sparisce(self):
        class Senza(Cfg):
            FB_REEL_INVITO = ""
        t = ig.descrizione_reel_facebook(DIDASCALIA, Senza)
        self.assertNotIn("link in bio", t)
        self.assertNotIn("Iscrizione", t)
        self.assertIn("fonte originale.", t)
        self.assertIn("#POCUS", t)

    def test_didascalia_senza_link_in_bio_invariata(self):
        self.assertEqual(ig.descrizione_reel_facebook("Ciao #a", Cfg), "Ciao #a")


class Pubblicazione(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.video = os.path.join(self.d, "r.mp4")
        with open(self.video, "wb") as f:
            f.write(b"\x00" * 100)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def lancia(self, durata=30.0, ai=False, stato="ready", **k):
        chiamate = []

        def chiama(metodo, url, campi, file_path=None, timeout=60):
            chiamate.append((metodo, url, dict(campi)))
            if campi.get("upload_phase") == "start":
                return {"video_id": "V1", "upload_url": "https://rupload.facebook.com/video-upload/V1"}
            if metodo == "GET":
                return {"status": {"video_status": stato}}
            return {"success": True}
        with mock.patch.object(ig, "_chiama", side_effect=chiama), \
             mock.patch.object(ig, "_carica_video") as carica, \
             mock.patch.object(ig, "_durata_video", return_value=durata), \
             mock.patch("time.sleep"):
            r = ig.pubblica_reel_facebook(self.video, "desc", "PAGE", "TOK", "v21.0", ai, **k)
        return r, chiamate, carica

    def test_tre_passi(self):
        r, ch, carica = self.lancia()
        self.assertEqual(r, "V1")
        self.assertEqual(ch[0][1], "https://graph.facebook.com/v21.0/PAGE/video_reels")
        self.assertEqual(ch[0][2]["upload_phase"], "start")
        self.assertEqual(carica.call_args.kwargs["url"], "https://rupload.facebook.com/video-upload/V1")
        fine = ch[1][2]
        self.assertEqual((fine["upload_phase"], fine["video_id"], fine["video_state"], fine["description"]),
                         ("finish", "V1", "PUBLISHED", "desc"))
        self.assertNotIn("is_ai_generated", fine)

    def test_dichiarazione_ia(self):
        _, ch, _ = self.lancia(ai=True)
        self.assertEqual(ch[1][2]["is_ai_generated"], "true")

    def test_video_troppo_lungo_o_corto_non_si_pubblica(self):
        for durata in (91.0, 2.0):
            r, ch, carica = self.lancia(durata=durata)
            self.assertIsNone(r)
            self.assertEqual(ch, [])
            self.assertFalse(carica.called)

    def test_video_rifiutato_da_facebook(self):
        r, _, _ = self.lancia(stato="error")
        self.assertIsNone(r)

    def test_errore_di_rete_non_solleva(self):
        with mock.patch.object(ig, "_chiama", side_effect=RuntimeError("HTTP 400")), \
             mock.patch.object(ig, "_durata_video", return_value=30.0):
            self.assertIsNone(ig.pubblica_reel_facebook(self.video, "d", "P", "T"))

    def test_ancora_in_elaborazione_allo_scadere_si_prosegue(self):
        with mock.patch.object(ig, "_chiama", return_value={"status": {"video_status": "processing"}}), \
             mock.patch("time.sleep"):
            self.assertEqual(ig._attendi_video_facebook("V", "T", "v21.0", attesa_max=0), "")


class Instradamento(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = type("C", (Cfg,), {"IG_DIR": self.d})

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_spento_o_prova_solo_instagram(self):
        for k, v in (("FB_REEL", False), ("SOLO_INSTAGRAM", True)):
            cfg = type("C", (self.cfg,), {k: v})
            with mock.patch.object(ig, "pubblica_reel_facebook") as pub:
                self.assertIsNone(ig._reel_facebook("v.mp4", DIDASCALIA, cfg, False, "schede"))
            self.assertFalse(pub.called, k)

    def test_senza_token_non_pubblica(self):
        cfg = type("C", (self.cfg,), {"FB_PAGE_TOKEN": ""})
        with mock.patch.object(ig, "pubblica_reel_facebook") as pub:
            self.assertIsNone(ig._reel_facebook("v.mp4", DIDASCALIA, cfg, False, "schede"))
        self.assertFalse(pub.called)

    def test_podcast_dichiarato_ia_le_schede_no(self):
        with mock.patch.object(ig, "pubblica_reel_facebook", return_value="V") as pub:
            ig._reel_facebook("a.mp4", DIDASCALIA, self.cfg, False, "schede")
            ig._reel_facebook("b.mp4", DIDASCALIA, self.cfg, False, "podcast", ai=True)
        self.assertFalse(pub.call_args_list[0].args[5])
        self.assertTrue(pub.call_args_list[1].args[5])
        self.assertNotIn("link in bio", pub.call_args_list[0].args[1])

    def test_prova_a_vuoto_salva_la_descrizione(self):
        with mock.patch.object(ig, "pubblica_reel_facebook") as pub:
            self.assertEqual(ig._reel_facebook("v.mp4", DIDASCALIA, self.cfg, True, "schede"), "dry-run")
        self.assertFalse(pub.called)
        with open(os.path.join(self.d, "descrizione-reel-facebook-schede.txt"), encoding="utf-8") as f:
            self.assertIn("https://esempio.github.io/n/", f.read())

    def test_estratto_podcast_con_i_loghi_sta_nei_90_secondi(self):
        self.assertLessEqual(ig.ESTRATTO_MAX_S + ig.REEL_LOGHI_S, ig.FB_REEL_MAX_S)


if __name__ == "__main__":
    unittest.main()
