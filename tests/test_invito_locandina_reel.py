"""Reel: l'invito ai corsi scritto in testa alle locandine (il carosello non cambia)."""
import base64
import os
import struct
import sys
import tempfile
import unittest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
import instagram as ig  # noqa: E402

try:
    import playwright.sync_api  # noqa: F401
    PLAYWRIGHT = True
except ImportError:
    PLAYWRIGHT = False

INVITO = "Sei un medico d'emergenza-urgenza? Partecipa ai nostri corsi!"


class Cfg:
    COLOR_DARK = "#1a2a30"
    COLOR_ACCENT = "#0a6e8a"
    REEL_INVITO_LOCANDINA = INVITO


PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def immagine_minima(percorso):
    with open(percorso, "wb") as f:
        f.write(PNG_1X1)


class HtmlInvito(unittest.TestCase):
    def test_testo_escapato_e_riga_sotto(self):
        h = ig.html_locandina_invito("AAAA", "image/jpeg", "Corsi <ECM> & co", "Date e sede", "#111", "#c00")
        self.assertIn("Corsi &lt;ECM&gt; &amp; co", h)
        self.assertIn("Date e sede", h)
        self.assertIn("data:image/jpeg;base64,AAAA", h)

    def test_senza_riga_sotto(self):
        self.assertNotIn("monospace", ig.html_locandina_invito("AAAA", "image/png", "Corsi", "", "#111", "#c00"))


class DiapositiveReel(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.loc = os.path.join(self.d, "loc.png")
        immagine_minima(self.loc)
        self.file = [os.path.join(self.d, f"s{k}.jpg") for k in range(3)]
        self.voci = [("c.png", "#111", "copertina"), (self.loc, "#fff", "locandina"), ("l.png", "#fff", "loghi")]

    def test_senza_invito_nulla_cambia(self):
        class C(Cfg):
            REEL_INVITO_LOCANDINA = ""
        self.assertEqual(ig.diapositive_reel(self.file, self.voci, C, self.d), self.file)

    def test_senza_locandine_nulla_cambia(self):
        voci = [("c.png", "#111", "copertina"), ("s.png", "#fff", "scheda"), ("l.png", "#fff", "loghi")]
        self.assertEqual(ig.diapositive_reel(self.file, voci, Cfg, self.d), self.file)

    def test_lunghezze_diverse_nulla_cambia(self):
        self.assertEqual(ig.diapositive_reel(self.file[:2], self.voci, Cfg, self.d), self.file[:2])

    @unittest.skipUnless(PLAYWRIGHT, "serve Playwright")
    def test_solo_la_locandina_cambia_ed_e_1080x1350(self):
        r = ig.diapositive_reel(self.file, self.voci, Cfg, self.d)
        self.assertEqual(r[0], self.file[0])
        self.assertEqual(r[2], self.file[2])
        self.assertNotEqual(r[1], self.file[1])
        self.assertTrue(os.path.exists(r[1]))
        with open(r[1], "rb") as f:
            dati = f.read()
        i = 2
        while i < len(dati):                       # cerca il marcatore SOF del JPEG
            if dati[i] != 0xFF:
                break
            m = dati[i + 1]
            if m in (0xC0, 0xC2):
                alt, lar = struct.unpack(">HH", dati[i + 5:i + 9])
                self.assertEqual((lar, alt), (1080, 1350))
                break
            i += 2 + struct.unpack(">H", dati[i + 2:i + 4])[0]
        else:
            self.fail("JPEG senza SOF")


if __name__ == "__main__":
    unittest.main()
