"""Ripubblicazione dei soli Reel falliti (ripubblica.py e cfg.SOLO_REEL in instagram.py)."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
import instagram as ig  # noqa: E402
import ripubblica as rp  # noqa: E402


class Voci(unittest.TestCase):
    def test_predefinite_e_valide(self):
        self.assertEqual(rp.voci_da_ripubblicare(""), set(rp.PREDEFINITE.split(",")))
        self.assertNotIn("fb_podcast", rp.PREDEFINITE)
        self.assertEqual(rp.voci_da_ripubblicare("IG_schede, x"), {"ig_schede", "x"})

    def test_voce_sconosciuta(self):
        with self.assertRaises(ValueError):
            rp.voci_da_ripubblicare("ig_schede,mail")

    def test_fa(self):
        class C:
            pass
        self.assertTrue(ig._fa(C, "carosello"))                 # senza SOLO_REEL: tutto
        C.SOLO_REEL = {"ig_podcast"}
        self.assertTrue(ig._fa(C, "ig_podcast"))
        self.assertFalse(ig._fa(C, "carosello"))


class Esecuzione(unittest.TestCase):
    """main(): voci, firma di pubblica_instagram del repository e archivio della corsa."""

    def test_nome_workflow(self):
        with mock.patch.dict(os.environ, {"GITHUB_WORKFLOW_REF": "o/r/.github/workflows/newsletter_NIV.yml@refs/heads/main"}):
            self.assertEqual(rp.nome_workflow(), "newsletter_NIV.yml")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(rp.nome_workflow(), "newsletter.yml")

    def test_main_passa_voci_e_articoli(self):
        import inspect
        visto = {}

        def finto(*args, **kw):
            visto["args"], visto["voci"] = args, set(rp.cfg.SOLO_REEL)
        firma = inspect.signature(rp.nr.pubblica_instagram)
        finto.__signature__ = firma                  # main() sceglie gli argomenti dalla firma reale
        parametri = list(firma.parameters)
        with tempfile.TemporaryDirectory() as d:
            vecchia = os.getcwd()
            os.makedirs(os.path.join(d, "archivio"))
            with open(os.path.join(d, "archivio", "digest-s47-2026.json"), "w", encoding="utf-8") as f:
                json.dump({"settimana": 47, "anno": 2026, "wl": {"settimana": 47, "anno": 2026, "mese": "Novembre"},
                           "dry_run": False, "pubblicati": [{"pmid": "1"}]}, f)
            os.chdir(d)
            try:
                with mock.patch.dict(os.environ, {"RIPUBBLICA_COSA": "ig_schede"}), \
                        mock.patch.object(rp, "scarica", return_value=d), \
                        mock.patch.object(rp, "rimetti_file"), \
                        mock.patch.object(rp.nr, "pubblica_instagram", finto):
                    self.assertEqual(rp.main(), 0)
            finally:
                os.chdir(vecchia)
                rp.cfg.SOLO_REEL = None
        self.assertEqual(visto["voci"], {"ig_schede"})
        self.assertEqual(visto["args"][0], [{"pmid": "1"}])
        self.assertEqual(len(visto["args"]), 3 if "immagini" in parametri else 2)
        self.assertEqual(visto["args"][-1]["mese"], "Novembre")      # wl completo dall'archivio


class Corsa(unittest.TestCase):
    def test_solo_corse_vere_della_settimana(self):
        dati = {"dry_run": False, "settimana": 47, "anno": 2026, "pubblicati": [{"pmid": "1"}]}
        self.assertTrue(rp.corsa_valida(dati, 47))
        self.assertFalse(rp.corsa_valida(dati, 46))
        self.assertFalse(rp.corsa_valida(dict(dati, dry_run=True), 47))
        self.assertFalse(rp.corsa_valida(dict(dati, pubblicati=[]), 47))
        self.assertTrue(rp.corsa_valida(dati, None))             # run_id indicato: nessun vincolo

    def _artifact(self, base, rid, dry):
        d = os.path.join(base, rid, "digest-73", "archivio")
        os.makedirs(d)
        with open(os.path.join(d, "digest-s47-2026.json"), "w") as f:
            json.dump({"dry_run": dry, "settimana": 47, "anno": 2026, "pubblicati": [{"pmid": "1"}]}, f)

    def test_scarica_salta_le_prove_e_prende_la_corsa_vera(self):
        with tempfile.TemporaryDirectory() as base:
            def finto_gh(*args):
                if args[:2] == ("run", "list"):
                    return json.dumps([{"databaseId": 3}, {"databaseId": 2}, {"databaseId": 1}])
                rid = args[2]
                self._artifact(base, rid, dry=(rid == "3"))       # la piu' recente e' una prova
                return ""
            with mock.patch.object(rp, "_gh", side_effect=finto_gh), \
                    mock.patch.object(rp, "date") as d, \
                    mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "99"}):
                d.today.return_value.isocalendar.return_value = (2026, 47, 1)
                radice = rp.scarica(repo="o/r", base=base)
            self.assertEqual(radice, os.path.join(base, "2", "digest-73"))
            self.assertFalse(os.path.exists(os.path.join(base, "3")))   # la prova e' stata tolta

    def test_nessuna_corsa(self):
        with tempfile.TemporaryDirectory() as base, \
                mock.patch.object(rp, "_gh", return_value="[]"):
            with self.assertRaises(RuntimeError):
                rp.scarica(repo="o/r", base=base)

    def test_rimetti_file(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dest:
            for c in ("archivio", "immagini", "podcast", "instagram"):
                os.makedirs(os.path.join(src, c))
                open(os.path.join(src, c, "f"), "w").close()
            os.utime(os.path.join(src, "podcast", "f"), (0, 0))
            rp.rimetti_file(src, dest)
            self.assertTrue(os.path.exists(os.path.join(dest, "podcast", "f")))
            self.assertFalse(os.path.exists(os.path.join(dest, "instagram")))
            self.assertGreater(os.path.getmtime(os.path.join(dest, "podcast", "f")), 1e9)


class SoloReel(unittest.TestCase):
    """Con SOLO_REEL si pubblica solo cio' che e' indicato."""

    def _cfg(self, d, solo):
        class C:
            IG_DIR = d
            IG_USER_ID, FB_PAGE_ID, IG_PAGE_TOKEN = "u", "p", "t"
            SOLO_REEL = solo
        return C

    def test_reel_schede_solo_instagram(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._cfg(d, {"ig_schede"})
            with mock.patch.object(ig, "crea_reel", side_effect=lambda f, du, dest, *a: open(dest, "wb").close()), \
                    mock.patch.object(ig, "pubblica_reel", return_value="IG1") as reel, \
                    mock.patch.object(ig, "_reel_facebook") as fb, \
                    mock.patch.object(ig, "_reel_x") as x:
                self.assertEqual(ig._reel_schede(["a.jpg"], ["scheda"], None, "d", cfg, False), "IG1")
            reel.assert_called_once()
            fb.assert_not_called()
            x.assert_not_called()

    def test_reel_podcast_solo_facebook(self):
        with tempfile.TemporaryDirectory() as d:
            mp4 = os.path.join(d, "p.mp4")
            open(mp4, "wb").close()
            cfg = self._cfg(d, {"fb_podcast"})

            def estratto(src, dest, *a, **k):
                open(dest, "wb").close()
                return dest, 80.0
            with mock.patch.object(ig, "crea_estratto_podcast", side_effect=estratto), \
                    mock.patch.object(ig, "pubblica_reel") as reel, \
                    mock.patch.object(ig, "_reel_facebook", return_value="FB1") as fb:
                ig.pubblica_reel_podcast(mp4, [], "d", cfg)
            reel.assert_not_called()
            fb.assert_called_once()

    def test_niente_podcast_richiesto_niente_estratto(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(ig, "crea_estratto_podcast") as estratto:
                ig.pubblica_reel_podcast("x.mp4", [], "d", self._cfg(d, {"ig_schede"}))
            estratto.assert_not_called()


if __name__ == "__main__":
    unittest.main()
