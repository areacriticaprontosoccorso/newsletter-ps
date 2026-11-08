"""Prima diapositiva del carosello/Reel: schermata della pagina di iscrizione (POCUS e NIV)."""
import importlib
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
import config as cfg  # noqa: E402
import instagram as ig  # noqa: E402

try:
    import playwright.sync_api  # noqa: F401
    PLAYWRIGHT = True
except ImportError:
    PLAYWRIGHT = False

PAGINA = ('<html><body style="background:#111;color:#fff;font:20px sans-serif"><h1>Iscriviti</h1>'
          '<input placeholder="la-tua@email.com"></body></html>')


class Cfg:
    COLOR_DARK = "#1a2a30"
    COLOR_ACCENT = "#0a6e8a"
    NOME_SERVIZIO = "Area Critica e Pronto Soccorso"
    NEWSLETTER_PAGE_URL = "https://esempio.github.io/newsletter-prova/"
    IG_COPERTINA_TITOLO = "Prova Weekly Digest"
    IG_COPERTINA_SOTTOTITOLO = "La newsletter di <prova> & co"
    IG_COPERTINA_INVITO = "Iscriviti alla newsletter"


def dimensioni_png(percorso):
    with open(percorso, "rb") as f:
        testa = f.read(24)
    return struct.unpack(">II", testa[16:24])


class Html(unittest.TestCase):
    def test_titolo_invito_e_indirizzo_vero(self):
        h = ig.html_copertina_iscrizione("QUJD", Cfg)
        self.assertIn("Prova Weekly Digest", h)
        self.assertIn("Iscriviti alla newsletter", h)
        self.assertIn("link in bio", h)
        self.assertIn("esempio.github.io/newsletter-prova", h)
        self.assertNotIn("https://esempio", h)                 # nella barra: senza https:// e senza barra finale
        self.assertIn("data:image/png;base64,QUJD", h)

    def test_testi_con_caratteri_speciali_escapati(self):
        h = ig.html_copertina_iscrizione("QUJD", Cfg)
        self.assertIn("&lt;prova&gt; &amp; co", h)


class Generazione(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.pagina = os.path.join(self.d, "index.html")
        with open(self.pagina, "w", encoding="utf-8") as f:
            f.write(PAGINA)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    @unittest.skipUnless(PLAYWRIGHT, "serve Playwright")
    def test_png_1080x1350(self):
        dest = os.path.join(self.d, "out", "cop.png")
        self.assertEqual(ig.genera_copertina_iscrizione(Cfg, dest, self.pagina), dest)
        self.assertEqual(dimensioni_png(dest), (1080, 1350))

    def test_pagina_assente_nessuna_copertina(self):
        self.assertIsNone(ig.genera_copertina_iscrizione(Cfg, os.path.join(self.d, "c.png"),
                                                          os.path.join(self.d, "non-esiste.html")))

    def test_disattivata(self):
        class Spenta(Cfg):
            IG_COPERTINA_ISCRIZIONE = False
        self.assertIsNone(ig.genera_copertina_iscrizione(Spenta, os.path.join(self.d, "c.png"), self.pagina))
        self.assertFalse(os.path.exists(os.path.join(self.d, "c.png")))

    def test_errore_imprevisto_non_solleva(self):
        with mock.patch("os.makedirs", side_effect=OSError("disco")):
            self.assertIsNone(ig.genera_copertina_iscrizione(Cfg, os.path.join(self.d, "x", "c.png"), self.pagina))


class Ordine(unittest.TestCase):
    def test_copertina_per_prima_e_loghi_per_ultimi(self):
        d = tempfile.mkdtemp()
        try:
            f = {n: os.path.join(d, n) for n in ("c.png", "s.png", "l.png")}
            for p in f.values():
                open(p, "wb").close()
            voci = ig.assembla(f["c.png"], [f["s.png"]], [], f["l.png"])
            self.assertEqual([t for _, _, t in voci], ["copertina", "scheda", "loghi"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_copertina_dura_quattro_secondi_nel_reel(self):
        self.assertEqual(ig.durate_reel(["copertina", "scheda"], [30])[0], 4.0)


MODULO = next((m for m in ("newsletter_pocus", "newsletter_NIV") if os.path.exists(os.path.join(RADICE, m + ".py"))), None)


@unittest.skipUnless(MODULO, "solo POCUS e NIV")
class Integrazione(unittest.TestCase):
    """pubblica_instagram mette la schermata di iscrizione come prima diapositiva."""

    def test_prima_diapositiva_e_la_copertina(self):
        nl = importlib.import_module(MODULO)
        d = tempfile.mkdtemp()
        try:
            scheda = os.path.join(d, "scheda.png")
            cop = os.path.join(d, "cop.png")
            for p in (scheda, cop):
                open(p, "wb").close()
            chiamate = {}
            with mock.patch.object(cfg, "IG_DIR", os.path.join(d, "instagram")), \
                 mock.patch.object(ig, "genera_schede", return_value=([scheda], [10])), \
                 mock.patch.object(ig, "genera_copertina_iscrizione", return_value=cop) as gen, \
                 mock.patch.object(ig, "pubblica", side_effect=lambda voci, *a, **k: chiamate.update(voci=voci) or "x"):
                art = [{"titolo": "T", "titolo_it": "T", "rivista": "J", "data": "2026", "pmid": "1",
                        "rilevanza": "C.", "origine": "niv"}]
                wl = {"settimana": "45", "anno": "2026"}
                if MODULO == "newsletter_pocus":
                    nl.pubblica_instagram(art, [], wl, dry_run=True)
                else:
                    nl.pubblica_instagram(art, wl, dry_run=True)
            self.assertEqual(gen.call_args[0][2], os.path.join(RADICE, "index.html"))
            self.assertEqual(chiamate["voci"][0][2], "copertina")
            self.assertEqual(chiamate["voci"][0][0], cop)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
