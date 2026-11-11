"""Instagram: schede essenziali, Reel delle schede, estratto del podcast, API dei Reel (simulata)."""
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import instagram as ig  # noqa: E402
from test_instagram import cfg_finto, png  # noqa: E402

ARTICOLI = [
    {"titolo": "Original title", "titolo_it": "Trombolisi per l'ictus: nuove direzioni",
     "rivista": "NEJM", "data": "2026 Nov 04", "tipo": "revisione", "pmid": "111",
     "rilevanza": "Aggiorna il medico di Area Critica sulle tendenze emergenti."},
    {"titolo": "Solo originale", "rivista": "J", "data": "2026", "pmid": "222",
     "sintesi_it": "Lo studio mostra un beneficio. Seconda frase non usata."},
    {"titolo": "T" * 600, "rivista": "J", "data": "2026", "pmid": "333", "rilevanza": "R " * 300},
]
WL = {"settimana": "45", "anno": "2026"}


def cfg_schede(cartella, **kw):
    c = cfg_finto(cartella, NOME_SERVIZIO="Area Critica", NOME_NEWSLETTER="Digest",
                  COLOR_ACCENT="#c41e3a", COLOR_DARK="#1a1a1a",
                  TIPI_ARTICOLO={"revisione": {"label": "Revisione", "colore": "#6f5b8e"}})
    c.__dict__.update(kw)
    return c


def ha_playwright():
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def ha_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def durata(percorso, cosa="format=duration"):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", cosa, "-of", "csv=p=0", percorso],
                       check=True, capture_output=True, text=True)
    return r.stdout.strip()


class DatiScheda(unittest.TestCase):
    def test_titolo_italiano_e_tipo(self):
        d = ig.dati_scheda(ARTICOLI[0], cfg_schede(tempfile.gettempdir()))
        self.assertEqual(d["titolo"], "Trombolisi per l'ictus: nuove direzioni")
        self.assertEqual(d["tipo"], "Revisione")
        self.assertEqual(d["pratica"], "Aggiorna il medico di Area Critica sulle tendenze emergenti.")

    def test_senza_rilevanza_prima_frase_della_sintesi(self):
        d = ig.dati_scheda(ARTICOLI[1], cfg_schede(tempfile.gettempdir()))
        self.assertEqual(d["titolo"], "Solo originale")
        self.assertEqual(d["pratica"], "Lo studio mostra un beneficio.")
        self.assertEqual(d["tipo"], "")

    def test_testi_lunghi_accorciati(self):
        d = ig.dati_scheda(ARTICOLI[2], cfg_schede(tempfile.gettempdir()))
        self.assertLessEqual(len(d["titolo"]), ig.MAX_TITOLO + 1)
        self.assertLessEqual(len(d["pratica"]), ig.MAX_PRATICA + 1)
        self.assertTrue(d["titolo"].endswith("…"))

    def test_html_senza_markup_iniettato(self):
        d = ig.dati_scheda({"titolo": "<script>x</script>", "rivista": "J", "data": "1", "pmid": "1"},
                           cfg_schede(tempfile.gettempdir()))
        html = ig.html_scheda(d, 1, WL, cfg_schede(tempfile.gettempdir()))
        self.assertNotIn("<script>x</script>", html)


@unittest.skipUnless(ha_playwright(), "Playwright non installato")
class GeneraSchede(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_png_1080x1350_e_parole(self):
        file, parole = ig.genera_schede(ARTICOLI, WL, cfg_schede(self.d), os.path.join(self.d, "s"))
        self.assertEqual(len(file), 3)
        self.assertEqual(len(parole), 3)
        with open(file[0], "rb") as f:
            testa = f.read(24)
        self.assertEqual(testa[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(testa[16:20], "big"), ig.LARGHEZZA)
        self.assertEqual(int.from_bytes(testa[20:24], "big"), ig.ALTEZZA)

    def test_senza_articoli_niente(self):
        self.assertEqual(ig.genera_schede([], WL, cfg_schede(self.d), os.path.join(self.d, "s")), ([], []))


class Loghi(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = {n: os.path.join(self.d, n) for n in ("c.png", "s1.png", "l.jpg", "loghi.png")}
        for f in self.f.values():
            with open(f, "wb") as fh:
                fh.write(b"x")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_loghi_sempre_per_ultimi(self):
        corsi = [{"immagine_file": self.f["l.jpg"]}]
        voci = ig.assembla(self.f["c.png"], [self.f["s1.png"]], corsi, self.f["loghi.png"])
        self.assertEqual([t for _, _, t in voci], ["copertina", "scheda", "locandina", "loghi"])
        self.assertEqual(voci[-1][0], self.f["loghi.png"])

    def test_senza_file_dei_loghi_nessuna_diapositiva_in_piu(self):
        for ch in (None, "/non/esiste.png"):
            voci = ig.assembla(None, [self.f["s1.png"]], [], ch)
            self.assertEqual([t for _, _, t in voci], ["scheda"])

    def test_oltre_il_massimo_i_loghi_restano(self):
        voci = ig.assembla(self.f["c.png"], [self.f["s1.png"]] * 12, [], self.f["loghi.png"])
        voci = ig.limita_diapositive(voci)
        self.assertEqual(len(voci), ig.MAX_DIAPOSITIVE)
        self.assertEqual(voci[0][2], "copertina")
        self.assertEqual(voci[-1][2], "loghi")

    def test_durata_nel_reel(self):
        self.assertEqual(ig.durate_reel(["scheda", "loghi"], [30])[1], ig.REEL_LOGHI_S)


class Durate(unittest.TestCase):
    def test_per_tipo(self):
        d = ig.durate_reel(["copertina", "scheda", "scheda", "locandina"], [2, 200])
        self.assertEqual(d[0], ig.REEL_COPERTINA_S)
        self.assertEqual(d[1], ig.REEL_SCHEDA_MIN_S)      # poche parole: minimo
        self.assertEqual(d[2], ig.REEL_SCHEDA_MAX_S)      # tante parole: massimo
        self.assertEqual(d[3], ig.REEL_LOCANDINA_S)

    def test_parole_mancanti_valore_medio(self):
        d = ig.durate_reel(["scheda"], [])
        self.assertTrue(ig.REEL_SCHEDA_MIN_S <= d[0] <= ig.REEL_SCHEDA_MAX_S)

    def test_punto_di_taglio(self):
        self.assertEqual(ig.punto_di_taglio(None), ig.ESTRATTO_STANDARD_S)
        self.assertEqual(ig.punto_di_taglio([[0, 10], [10, 30]]), ig.ESTRATTO_STANDARD_S)
        self.assertEqual(ig.punto_di_taglio([[0, 16], [16, 40], [40, 62], [62, 84], [84, 110]]), 84)
        self.assertEqual(ig.punto_di_taglio([[0, 50], [50, 100]]), 50)


@unittest.skipUnless(ha_ffmpeg() and ha_playwright(), "serve ffmpeg e Playwright")
class Video(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = []
        for i, col in enumerate(((200, 30, 30), (30, 200, 30))):
            p = os.path.join(self.d, f"s{i}.png")
            png(p, 1080, 1350, col)
            self.f.append(p)
        self.jpg = ig.prepara_diapositive([(p, "#fff") for p in self.f], os.path.join(self.d, "ig"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_reel_9_16_con_la_durata_giusta(self):
        dest = ig.crea_reel(self.jpg, [3.0, 2.0], os.path.join(self.d, "r.mp4"))
        import json
        info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "json", dest], check=True, capture_output=True, text=True).stdout)
        flusso = info["streams"][0]
        self.assertEqual((flusso["width"], flusso["height"]), (1080, 1920))
        self.assertAlmostEqual(float(durata(dest)), 5.0, delta=0.2)

    def test_con_musica_stessa_durata(self):
        musica = os.path.join(self.d, "m.mp3")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=1", musica], check=True)
        dest = ig.crea_reel(self.jpg, [3.0, 2.0], os.path.join(self.d, "r.mp4"), musica=musica)
        self.assertAlmostEqual(float(durata(dest)), 5.0, delta=0.2)

    def test_numero_durate_sbagliato(self):
        with self.assertRaises(ValueError):
            ig.crea_reel(self.jpg, [3.0], os.path.join(self.d, "r.mp4"))

    def test_estratto_podcast(self):
        src = os.path.join(self.d, "pod.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=1080x1350:r=5:d=20", "-f", "lavfi", "-i",
                        "sine=frequency=300:duration=20", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", "-shortest", src], check=True)
        dest, dur = ig.crea_estratto_podcast(src, os.path.join(self.d, "e.mp4"), [[0, 8], [8, 14]])
        self.assertAlmostEqual(dur, 20.0, delta=0.2)      # taglio 75 s oltre la fine: tutto il video
        self.assertAlmostEqual(float(durata(dest)), 20.0, delta=0.3)
        self.assertEqual(durata(dest, "stream=width,height").split()[0], "1080,1920")

    def test_estratto_podcast_con_loghi_in_coda(self):
        src = os.path.join(self.d, "pod.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=1080x1350:r=5:d=10", "-f", "lavfi", "-i",
                        "sine=frequency=300:duration=10", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", "-shortest", src], check=True)
        dest, dur = ig.crea_estratto_podcast(src, os.path.join(self.d, "e.mp4"), None, self.f[0])
        self.assertAlmostEqual(dur, 10.0, delta=0.2)      # l'estratto, senza la chiusura
        self.assertAlmostEqual(float(durata(dest)), 10.0 + ig.REEL_LOGHI_S, delta=0.4)

    def test_estratto_podcast_con_i_saluti_sui_loghi(self):
        src = os.path.join(self.d, "pod.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=1080x1350:r=5:d=70", "-f", "lavfi", "-i",
                        "sine=frequency=300:duration=70", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", "-shortest", src], check=True)
        sezioni = [[0, 10], [10, 50], [50, 60], [60, 70]]
        dest, dur = ig.crea_estratto_podcast(src, os.path.join(self.d, "e.mp4"), sezioni, self.f[0],
                                             saluti=[62.0, 68.0])
        self.assertAlmostEqual(dur, 60.0, delta=0.2)             # taglio a fine sezione, prima dei saluti
        self.assertAlmostEqual(float(durata(dest)), 60.0 + 6.3, delta=0.4)   # loghi lunghi quanto i saluti

    def test_coda_saluti_e_taglio_con_massimo(self):
        self.assertEqual(ig.coda_saluti([100.0, 108.5]), (100.0, 8.5))
        self.assertIsNone(ig.coda_saluti(None))
        self.assertIsNone(ig.coda_saluti([100.0, 150.0]))        # troppo lunghi: non plausibili
        self.assertEqual(ig.punto_di_taglio([[0, 16], [16, 62], [62, 84], [84, 110]], massimo=80), 62)
        self.assertEqual(ig.punto_di_taglio(None, massimo=60), 60)


class RiprovaReel(unittest.TestCase):
    """Se Instagram non riesce a elaborare il video si riprova (tre tentativi); la pubblicazione mai."""

    def esegui(self, esiti_carico, publish_ok=True):
        chiamate = []

        def chiama(metodo, url, campi, file_path=None, timeout=60):
            chiamate.append(url.split("/v21.0/")[-1])
            if url.endswith("/media"):
                return {"id": f"C{sum(1 for c in chiamate if c.endswith('/media'))}"}
            if url.endswith("/media_publish"):
                if not publish_ok:
                    raise RuntimeError("HTTP 500")
                return {"id": "REEL"}
            return {"status_code": "FINISHED"}
        esiti = list(esiti_carico)

        def carica(*a, **k):
            e = esiti.pop(0)
            if e:
                raise RuntimeError(e)
        with mock.patch.object(ig, "_chiama", chiama), mock.patch.object(ig, "_carica_video", carica), \
             mock.patch.object(ig.time, "sleep") as sleep:
            r = ig.pubblica_reel("v.mp4", "d", "IG", "TOK")
        return r, chiamate, sleep

    def test_errore_di_elaborazione_poi_riesce(self):
        r, ch, sleep = self.esegui(['HTTP 400: {"type":"ProcessingFailedError","message":"Request processing failed"}', None])
        self.assertEqual(r, "REEL")
        self.assertEqual(sum(c == "IG/media" for c in ch), 2)           # due contenitori
        self.assertEqual(sum(c == "IG/media_publish" for c in ch), 1)   # una sola pubblicazione
        sleep.assert_any_call(ig.PAUSA_RIPROVA_S)

    def test_tre_errori_di_elaborazione_si_rinuncia(self):
        err = "HTTP 400: ProcessingFailedError"
        r, ch, sleep = self.esegui([err, err, err])
        self.assertIsNone(r)
        self.assertEqual(sum(c == "IG/media" for c in ch), 3)
        self.assertNotIn("IG/media_publish", ch)
        self.assertEqual([c[0][0] for c in sleep.call_args_list], list(ig.PAUSE_RIPROVA_S))

    def test_due_errori_poi_riesce(self):
        err = "HTTP 400: ProcessingFailedError"
        r, ch, _ = self.esegui([err, err, None])
        self.assertEqual(r, "REEL")
        self.assertEqual(sum(c == "IG/media_publish" for c in ch), 1)

    def test_altri_errori_non_si_riprovano(self):
        r, ch, _ = self.esegui(["HTTP 400: token scaduto"])
        self.assertIsNone(r)
        self.assertEqual(sum(c == "IG/media" for c in ch), 1)

    def test_pubblicazione_fallita_non_si_ripete(self):
        r, ch, _ = self.esegui([None], publish_ok=False)
        self.assertIsNone(r)
        self.assertEqual(sum(c == "IG/media_publish" for c in ch), 1)

    def test_il_log_dice_in_che_fase_e_fallito(self):
        with self.assertLogs("instagram", level="ERROR") as lg:
            self.esegui(["HTTP 400: token scaduto"])
        self.assertIn("caricamento del video", lg.output[0])


class NonAncoraPronto(unittest.TestCase):
    """«Media ID is not available» (2207027) con il contenitore gia' FINISHED: si riprova a pubblicare."""
    ERR = 'HTTP 400: {"error":{"message":"Media ID is not available","code":9007,"error_subcode":2207027}}'

    def finto(self, errori_publish):
        self.chiamate = []
        errori = list(errori_publish)

        def chiama(metodo, url, campi, file_path=None, timeout=60):
            self.chiamate.append((url.split("/v21.0/")[-1], dict(campi)))
            if url.endswith("/media"):
                return {"id": "C"}
            if url.endswith("/media_publish"):
                if errori:
                    raise RuntimeError(errori.pop(0))
                return {"id": "POST"}
            if "fields" in campi and campi["fields"] == "source":
                return {"source": "https://video.example/fb.mp4"}
            return {"status_code": "FINISHED"}
        return chiama

    def test_carosello_riprova_e_pubblica_una_volta(self):
        with mock.patch.object(ig, "_chiama", self.finto([self.ERR, self.ERR])), \
             mock.patch.object(ig, "url_pubblico", return_value="https://x/y.jpg"), \
             mock.patch.object(ig.time, "sleep") as sleep:
            r = ig.pubblica_carosello(["a.jpg", "b.jpg"], "d", "IG", "PG", "TOK")
        self.assertEqual(r, "POST")
        self.assertEqual(sum(c[0] == "IG/media_publish" for c in self.chiamate), 3)
        sleep.assert_any_call(ig.PAUSA_NON_PRONTO_S)

    def test_sempre_non_pronto_si_rinuncia(self):
        with mock.patch.object(ig, "_chiama", self.finto([self.ERR] * 9)), \
             mock.patch.object(ig, "url_pubblico", return_value="https://x/y.jpg"), \
             mock.patch.object(ig.time, "sleep"):
            self.assertIsNone(ig.pubblica_carosello(["a.jpg"], "d", "IG", "PG", "TOK"))
        self.assertEqual(sum(c[0] == "IG/media_publish" for c in self.chiamate), ig.TENTATIVI_PUBBLICAZIONE)

    def test_altro_errore_di_pubblicazione_non_si_ripete(self):
        with mock.patch.object(ig, "_chiama", self.finto(["HTTP 500"])), \
             mock.patch.object(ig, "url_pubblico", return_value="https://x/y.jpg"), \
             mock.patch.object(ig.time, "sleep"):
            self.assertIsNone(ig.pubblica_carosello(["a.jpg"], "d", "IG", "PG", "TOK"))
        self.assertEqual(sum(c[0] == "IG/media_publish" for c in self.chiamate), 1)

    def test_reel_di_riserva_dal_video_di_facebook(self):
        class C:
            FB_PAGE_TOKEN, IG_PAGE_TOKEN, IG_USER_ID, FB_API_VERSION = "FBT", "TOK", "IG", "v21.0"
        with mock.patch.object(ig, "_chiama", self.finto([])), \
             mock.patch.object(ig, "_carica_video") as cv, mock.patch.object(ig.time, "sleep"):
            r = ig._reel_instagram_da_facebook("VID", "Ciao", C)
        self.assertEqual(r, "POST")
        cv.assert_not_called()                                   # nessun caricamento: lo scarica Instagram
        campi = next(c[1] for c in self.chiamate if c[0] == "IG/media")
        self.assertEqual(campi["video_url"], "https://video.example/fb.mp4")
        self.assertNotIn("upload_type", campi)

    def test_riserva_senza_video_facebook(self):
        self.assertIsNone(ig._reel_instagram_da_facebook(None, "x", object()))
        self.assertIsNone(ig._reel_instagram_da_facebook("dry-run", "x", object()))


class ApiReel(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.video = os.path.join(self.d, "r.mp4")
        open(self.video, "wb").write(b"video")
        self.chiamate = []

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def finto(self, metodo, url, campi, file_path=None, timeout=60):
        self.chiamate.append((metodo, url.split("/v21.0/")[-1], dict(campi)))
        if url.endswith("/media"):
            return {"id": "CONT"}
        if url.endswith("/CONT"):
            return {"status_code": "FINISHED"}
        if url.endswith("/media_publish"):
            return {"id": "REEL1"}
        raise AssertionError(url)

    def test_ordine_chiamate(self):
        with mock.patch.object(ig, "_chiama", self.finto), \
             mock.patch.object(ig, "_carica_video") as cv, mock.patch.object(ig.time, "sleep"):
            r = ig.pubblica_reel(self.video, "Ciao #pocus", "IGID", "TOK")
        self.assertEqual(r, "REEL1")
        self.assertEqual([c[1] for c in self.chiamate], ["IGID/media", "CONT", "IGID/media_publish"])
        campi = self.chiamate[0][2]
        self.assertEqual(campi["media_type"], "REELS")
        self.assertEqual(campi["upload_type"], "resumable")
        self.assertEqual(campi["caption"], "Ciao #pocus")
        cv.assert_called_once_with("CONT", self.video, "TOK", "v21.0")

    def test_caricamento_con_intestazioni_giuste(self):
        class Risposta:
            def read(self):
                return b'{"success": true}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        visto = {}

        def apri(req, timeout=0):
            visto["url"] = req.full_url
            visto["h"] = {k.lower(): v for k, v in req.header_items()}
            visto["n"] = len(req.data)
            return Risposta()
        with mock.patch.object(ig.urllib.request, "urlopen", apri):
            ig._carica_video("CONT", self.video, "TOK", "v21.0")
        self.assertEqual(visto["url"], "https://rupload.facebook.com/ig-api-upload/v21.0/CONT")
        self.assertEqual(visto["h"]["authorization"], "OAuth TOK")
        self.assertEqual(visto["h"]["offset"], "0")
        self.assertEqual(visto["h"]["file_size"], "5")

    def test_errore_non_solleva(self):
        with mock.patch.object(ig, "_chiama", side_effect=RuntimeError("HTTP 400: token scaduto")):
            self.assertIsNone(ig.pubblica_reel(self.video, "x", "IGID", "TOK"))

    def test_rifiuto_non_pubblica(self):
        def rifiuta(metodo, url, campi, file_path=None, timeout=60):
            return {"id": "CONT"} if url.endswith("/media") else {"status_code": "ERROR"}
        with mock.patch.object(ig, "_chiama", rifiuta), mock.patch.object(ig, "_carica_video"), \
             mock.patch.object(ig.time, "sleep"):
            self.assertIsNone(ig.pubblica_reel(self.video, "x", "IGID", "TOK"))


class Configurazione(unittest.TestCase):
    def test_configurato(self):
        d = tempfile.gettempdir()
        self.assertTrue(ig.configurato(cfg_finto(d)))
        self.assertTrue(ig.configurato(cfg_finto(d, FB_PAGE_TOKEN="", IG_PAGE_TOKEN="T")))
        self.assertFalse(ig.configurato(cfg_finto(d, FB_PAGE_TOKEN="")))
        self.assertFalse(ig.configurato(cfg_finto(d, IG_USER_ID="")))
        self.assertFalse(ig.configurato(cfg_finto(d, FB_PAGE_ID="")))
        self.assertFalse(ig.configurato(cfg_finto(d, IG_ABILITATO=False)))


@unittest.skipUnless(ha_ffmpeg() and ha_playwright(), "serve ffmpeg e Playwright")
class Ingresso(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.img = os.path.join(self.d, "a.png")
        png(self.img, 1080, 1350)
        self.voci = [(self.img, "#fff", "scheda")]

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_prova_a_vuoto_crea_carosello_e_reel_senza_api(self):
        c = cfg_schede(self.d, IG_REEL_SCHEDE=True)
        with mock.patch.object(ig, "_chiama") as ch, mock.patch.object(ig.urllib.request, "urlopen") as uo:
            r = ig.pubblica(self.voci, "Ciao", c, dry_run=True, parole=[30])
        ch.assert_not_called()
        uo.assert_not_called()
        self.assertEqual(r, "dry-run")
        self.assertTrue(os.path.exists(os.path.join(c.IG_DIR, "ig-01.jpg")))
        self.assertTrue(os.path.exists(os.path.join(c.IG_DIR, "reel-schede.mp4")))

    def test_pubblica_carosello_e_poi_reel(self):
        c = cfg_schede(self.d, IG_REEL_SCHEDE=True)
        with mock.patch.object(ig, "pubblica_carosello", return_value="CAR") as pc, \
             mock.patch.object(ig, "pubblica_reel", return_value="REEL") as pr:
            r = ig.pubblica(self.voci, "Ciao", c, parole=[30])
        self.assertEqual(r, "CAR")
        pc.assert_called_once()
        pr.assert_called_once()
        self.assertEqual(pr.call_args[0][0], os.path.join(c.IG_DIR, "reel-schede.mp4"))
        self.assertEqual(pr.call_args[0][3], "TOK")

    def test_reel_spento(self):
        c = cfg_schede(self.d, IG_REEL_SCHEDE=False)
        with mock.patch.object(ig, "pubblica_carosello", return_value="CAR"), \
             mock.patch.object(ig, "pubblica_reel") as pr:
            ig.pubblica(self.voci, "Ciao", c)
        pr.assert_not_called()

    def test_carosello_fallito_il_reel_ci_prova_lo_stesso_e_non_solleva(self):
        c = cfg_schede(self.d, IG_REEL_SCHEDE=True)
        with mock.patch.object(ig, "pubblica_carosello", return_value=None), \
             mock.patch.object(ig, "pubblica_reel", side_effect=RuntimeError("boom")):
            self.assertIsNone(ig.pubblica(self.voci, "Ciao", c))

    def test_reel_podcast_prova_a_vuoto(self):
        src = os.path.join(self.d, "pod.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=blue:s=1080x1350:r=5:d=6", "-f", "lavfi", "-i",
                        "sine=frequency=300:duration=6", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", "-shortest", src], check=True)
        c = cfg_schede(self.d, IG_REEL_PODCAST=True)
        r = ig.pubblica_reel_podcast(src, None, "Podcast #em", c, dry_run=True)
        self.assertEqual(r, "dry-run")
        self.assertTrue(os.path.exists(os.path.join(c.IG_DIR, "reel-podcast.mp4")))
        self.assertEqual(open(os.path.join(c.IG_DIR, "didascalia-podcast.txt"), encoding="utf-8").read().strip(),
                         "Podcast #em")

    def test_reel_podcast_senza_video_o_spento(self):
        c = cfg_schede(self.d, IG_REEL_PODCAST=True)
        self.assertIsNone(ig.pubblica_reel_podcast(None, None, "x", c, dry_run=True))
        self.assertIsNone(ig.pubblica_reel_podcast("/non/esiste.mp4", None, "x", c, dry_run=True))
        c2 = cfg_schede(self.d, IG_REEL_PODCAST=False)
        self.assertIsNone(ig.pubblica_reel_podcast(self.img, None, "x", c2, dry_run=True))


if __name__ == "__main__":
    unittest.main()
