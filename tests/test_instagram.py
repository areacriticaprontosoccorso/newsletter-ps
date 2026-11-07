"""Instagram: diapositive 4:5, didascalia, carosello (API simulata), prova a vuoto."""
import os
import shutil
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import instagram as ig  # noqa: E402


def png(path, larghezza, altezza, rgb=(200, 30, 30)):
    """PNG minimo valido (colore pieno) senza dipendenze."""
    import zlib
    riga = b"\x00" + bytes(rgb) * larghezza
    dati = zlib.compress(riga * altezza)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", larghezza, altezza, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", dati) + chunk(b"IEND", b""))


def cfg_finto(cartella, **kw):
    c = types.SimpleNamespace(IG_ABILITATO=True, IG_USER_ID="17841000", FB_PAGE_ID="555",
                              FB_PAGE_TOKEN="TOK", FB_API_VERSION="v21.0",
                              IG_DIR=os.path.join(cartella, "ig"))
    c.__dict__.update(kw)
    return c


class Didascalia(unittest.TestCase):
    def test_taglio_a_2200(self):
        t = ig.taglia_didascalia("a" * 3000)
        self.assertEqual(len(t), ig.MAX_DIDASCALIA)
        self.assertTrue(t.endswith("…"))

    def test_massimo_30_hashtag(self):
        t = ig.taglia_didascalia("ciao " + " ".join(f"#t{i}" for i in range(40)))
        self.assertEqual(t.count("#"), 30)

    def test_corta_resta_uguale(self):
        self.assertEqual(ig.taglia_didascalia("Ciao #pocus"), "Ciao #pocus")


class Composizione(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = {n: os.path.join(self.d, n) for n in ("c.png", "s1.png", "s2.png", "l.jpg")}
        for f in self.f.values():
            open(f, "wb").write(b"x")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_ordine_e_locandine_una_volta(self):
        corsi = [{"immagine_file": self.f["l.jpg"]}, {"immagine_file": self.f["l.jpg"]},
                 {"immagine_file": None}]
        v = ig.assembla(self.f["c.png"], [self.f["s1.png"], self.f["s2.png"], "/manca.png"], corsi)
        self.assertEqual([x[2] for x in v], ["copertina", "scheda", "scheda", "locandina"])

    def test_senza_copertina(self):
        v = ig.assembla(None, [self.f["s1.png"]], [])
        self.assertEqual([x[2] for x in v], ["scheda"])

    def test_didascalia_completa(self):
        t = ig.componi_didascalia("EM Weekly Digest", ["Primo", "Secondo"],
                                  [{"titolo": "Corso POCUS", "link": "https://www.ecm.unito.it"}],
                                  "Sintesi con IA.", "#pocus #em", data_leggibile=lambda c: "23-24 feb 2027")
        self.assertIn("01 Primo\n02 Secondo", t)
        self.assertIn("- Corso POCUS (23-24 feb 2027)", t)
        self.assertIn("Iscrizioni: www.ecm.unito.it", t)
        self.assertTrue(t.rstrip().endswith("#pocus #em"))

    def test_didascalia_senza_corsi(self):
        t = ig.componi_didascalia("Titolo", ["A"], [], "", "#x")
        self.assertNotIn("Corsi in programma", t)


class Limite(unittest.TestCase):
    def test_oltre_10_si_tolgono_le_ultime_schede(self):
        voci = [("c", "#fff", "copertina")] + [(f"s{i}", "#fff", "scheda") for i in range(1, 9)] \
            + [("l1", "#fff", "locandina"), ("l2", "#fff", "locandina")]
        r = ig.limita_diapositive(voci)
        self.assertEqual(len(r), 10)
        self.assertEqual(r[0][2], "copertina")
        self.assertEqual([x[2] for x in r].count("locandina"), 2)
        self.assertEqual([x[0] for x in r if x[2] == "scheda"], [f"s{i}" for i in range(1, 8)])

    def test_sotto_il_limite_invariato(self):
        voci = [("a", "#fff", "scheda")] * 4
        self.assertEqual(ig.limita_diapositive(voci), voci)


class Diapositive(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_jpeg_1080x1350_da_formati_diversi(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright non installato")
        a, b = os.path.join(self.d, "a.png"), os.path.join(self.d, "b.png")
        png(a, 1440, 1900)       # scheda alta
        png(b, 800, 300)         # immagine bassa e larga
        file = ig.prepara_diapositive([(a, "#fff"), (b, "#fff"), (os.path.join(self.d, "manca.png"), "#fff")],
                                      os.path.join(self.d, "out"))
        self.assertEqual(len(file), 2)
        for f in file:
            with open(f, "rb") as fh:
                dati = fh.read()
            self.assertEqual(dati[:3], b"\xff\xd8\xff")           # JPEG
            i = 2
            while i < len(dati):                                    # cerca il marcatore SOF
                if dati[i] != 0xFF:
                    i += 1
                    continue
                m = dati[i + 1]
                if m in (0xC0, 0xC2):
                    h, w = struct.unpack(">HH", dati[i + 5:i + 9])
                    self.assertEqual((w, h), (1080, 1350))
                    break
                i += 2 + struct.unpack(">H", dati[i + 2:i + 4])[0]
            else:
                self.fail("SOF non trovato")

    def test_senza_file_nessuna_diapositiva(self):
        self.assertEqual(ig.prepara_diapositive([], self.d), [])
        self.assertEqual(ig.prepara_diapositive([("/non/esiste.png", "#fff")], self.d), [])


class Api(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = [os.path.join(self.d, f"s{i}.jpg") for i in range(3)]
        for f in self.f:
            open(f, "wb").write(b"\xff\xd8\xff")
        self.chiamate = []
        self.n = 0

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def finto(self, metodo, url, campi, file_path=None, timeout=60):
        self.chiamate.append((metodo, url.split("/v21.0/")[1], dict(campi), file_path))
        self.n += 1
        if url.endswith("/photos"):
            return {"id": f"foto{self.n}"}
        if metodo == "GET" and "fields" in campi and campi["fields"] == "images":
            return {"images": [{"width": 100, "source": "https://x/piccola"},
                               {"width": 1080, "source": f"https://x/grande{self.n}"}]}
        if metodo == "GET":
            return {"status_code": "FINISHED"}
        if url.endswith("/media_publish"):
            return {"id": "POST1"}
        return {"id": f"cont{self.n}"}

    def test_carosello_ordine_chiamate(self):
        with mock.patch.object(ig, "_chiama", self.finto), mock.patch.object(ig.time, "sleep"):
            r = ig.pubblica_carosello(self.f, "Didascalia", "IGID", "PAGE", "TOK")
        self.assertEqual(r, "POST1")
        percorsi = [c[1] for c in self.chiamate if c[0] == "POST"]
        self.assertEqual(percorsi.count("PAGE/photos"), 3)               # tre foto non pubblicate
        self.assertEqual(percorsi.count("IGID/media"), 4)                # 3 figli + contenitore
        self.assertEqual(percorsi[-1], "IGID/media_publish")
        figli = [c[2] for c in self.chiamate if c[1] == "IGID/media" and c[2].get("is_carousel_item")]
        self.assertEqual(len(figli), 3)
        self.assertTrue(all(c["image_url"].startswith("https://x/grande") for c in figli))   # la piu' grande
        car = [c[2] for c in self.chiamate if c[2].get("media_type") == "CAROUSEL"][0]
        self.assertEqual(len(car["children"].split(",")), 3)
        self.assertEqual(car["caption"], "Didascalia")
        foto = [c for c in self.chiamate if c[1] == "PAGE/photos"]
        self.assertTrue(all(c[2]["published"] == "false" for c in foto))

    def test_una_sola_diapositiva_post_singolo(self):
        with mock.patch.object(ig, "_chiama", self.finto), mock.patch.object(ig.time, "sleep"):
            r = ig.pubblica_carosello(self.f[:1], "Solo una", "IGID", "PAGE", "TOK")
        self.assertEqual(r, "POST1")
        medias = [c[2] for c in self.chiamate if c[1] == "IGID/media"]
        self.assertEqual(len(medias), 1)
        self.assertIn("image_url", medias[0])
        self.assertNotIn("media_type", medias[0])

    def test_errore_api_non_solleva(self):
        def rotto(*a, **k):
            raise RuntimeError("HTTP 400: token scaduto")
        with mock.patch.object(ig, "_chiama", rotto):
            self.assertIsNone(ig.pubblica_carosello(self.f, "x", "IGID", "PAGE", "TOK"))

    def test_rifiuto_di_instagram_non_solleva(self):
        def f(metodo, url, campi, file_path=None, timeout=60):
            if metodo == "GET" and campi.get("fields") == "status_code":
                return {"status_code": "ERROR"}
            return self.finto(metodo, url, campi, file_path)
        with mock.patch.object(ig, "_chiama", f), mock.patch.object(ig.time, "sleep"):
            self.assertIsNone(ig.pubblica_carosello(self.f, "x", "IGID", "PAGE", "TOK"))
        self.assertFalse([c for c in self.chiamate if c[1].endswith("media_publish")])

    def test_massimo_dieci(self):
        molte = self.f * 5
        with mock.patch.object(ig, "_chiama", self.finto), mock.patch.object(ig.time, "sleep"):
            ig.pubblica_carosello(molte, "x", "IGID", "PAGE", "TOK")
        self.assertEqual(len([c for c in self.chiamate if c[1] == "PAGE/photos"]), 10)


class Ingresso(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.img = os.path.join(self.d, "a.png")
        png(self.img, 600, 900)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_senza_credenziali_non_fa_nulla(self):
        c = cfg_finto(self.d, IG_USER_ID="")
        with mock.patch.object(ig, "_chiama") as ch:
            self.assertIsNone(ig.pubblica([(self.img, "#fff", "scheda")], "x", c))
        ch.assert_not_called()

    def test_token_instagram_dedicato(self):
        for extra, atteso in (({"IG_PAGE_TOKEN": "TOK_IG"}, "TOK_IG"), ({}, "TOK")):
            c = cfg_finto(self.d, **extra)
            with mock.patch.object(ig, "pubblica_carosello", return_value="ok") as pc, \
                 mock.patch.object(ig, "prepara_diapositive", return_value=[self.img]):
                ig.pubblica([(self.img, "#fff", "scheda")], "x", c)
            self.assertEqual(pc.call_args[0][4], atteso)

    def test_disabilitato(self):
        c = cfg_finto(self.d, IG_ABILITATO=False)
        with mock.patch.object(ig, "_chiama") as ch:
            self.assertIsNone(ig.pubblica([(self.img, "#fff", "scheda")], "x", c))
        ch.assert_not_called()

    def test_prova_a_vuoto_salva_e_non_chiama_api(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright non installato")
        c = cfg_finto(self.d)
        with mock.patch.object(ig, "_chiama") as ch:
            r = ig.pubblica([(self.img, "#fff", "scheda")], "Ciao #pocus", c, dry_run=True)
        ch.assert_not_called()
        self.assertEqual(r, "dry-run")
        self.assertTrue(os.path.exists(os.path.join(c.IG_DIR, "ig-01.jpg")))
        self.assertEqual(open(os.path.join(c.IG_DIR, "didascalia.txt"), encoding="utf-8").read().strip(),
                         "Ciao #pocus")

    def test_nessuna_voce(self):
        self.assertIsNone(ig.pubblica([], "x", cfg_finto(self.d)))


if __name__ == "__main__":
    unittest.main()
