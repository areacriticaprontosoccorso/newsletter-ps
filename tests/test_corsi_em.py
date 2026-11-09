"""Avvisi dei corsi nella newsletter EM: alternanza POCUS/NIV e locandine del video."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DRY_RUN", "1")

import config as cfg  # noqa: E402
import corsi as avvisi  # noqa: E402

BASE = "https://example.test/"


def carica(giorno, **kw):
    return avvisi.carica_corsi("em", oggi=giorno, base_url=BASE, **kw)


def serie(c):
    return "pocus" if "ecografia" in c["titolo"] else "niv"


class TestAlternanza(unittest.TestCase):
    def test_file_corsi_valido(self):
        self.assertEqual(avvisi.verifica(), [])

    def test_prima_del_9_novembre_niente(self):
        self.assertEqual(carica(date(2026, 11, 2)), [])

    def test_si_alternano_una_a_settimana(self):
        atteso = ["pocus", "niv"] * 6
        lunedi = [date(2026, 11, 9) + timedelta(weeks=k) for k in range(12)]
        visti = []
        for g in lunedi:
            c = carica(g)
            self.assertEqual(len(c), 1, g)
            visti.append(serie(c[0]))
        self.assertEqual(visti, atteso)

    def test_ogni_lunedi_dalla_prima_uscita_a_fine_corsi_mai_vuoto(self):
        g = date(2026, 11, 9)
        while g < date(2027, 11, 10):
            self.assertGreaterEqual(len(carica(g)), 1, g)
            g += timedelta(weeks=1)

    def test_ultima_uscita_prima_del_corso_lo_annuncia_sempre(self):
        titoli = [c["titolo"] for c in carica(date(2027, 3, 8))]   # NIV il 9/3, turno POCUS libero
        self.assertTrue(any("ventilazione" in t for t in titoli))

    def test_ignora_frequenza_da_entrambe(self):
        c = carica(date(2026, 11, 9), ignora_frequenza=True)
        self.assertEqual(sorted(serie(x) for x in c), ["niv", "pocus"])

    def test_seconda_edizione_dopo_la_prima(self):
        c = carica(date(2027, 2, 25), ignora_frequenza=True)
        # POCUS: finita la I edizione (24/2) compare la II; NIV: ancora la I.
        per_serie = {serie(x): x["titolo"] for x in c}
        self.assertTrue(per_serie["pocus"].endswith("II edizione"))
        self.assertTrue(per_serie["niv"].endswith("I edizione")
                        and not per_serie["niv"].endswith("II edizione"))

    def test_locandine_esistono(self):
        for x in carica(date(2026, 11, 9), ignora_frequenza=True):
            self.assertTrue(x["immagine_file"] and os.path.exists(x["immagine_file"]), x["titolo"])

    def test_corsi_finiti_spariscono(self):
        self.assertEqual(carica(date(2027, 11, 11), ignora_frequenza=True), [])


class TestPodcast(unittest.TestCase):
    def test_locandine_video_due_file_distinti_e_loghi_in_fondo(self):
        import podcast
        f = podcast.locandine_video(date(2026, 11, 9))
        self.assertEqual(len(f), 3)
        self.assertEqual(len(set(f)), 3)
        self.assertEqual(f[-1], podcast.loghi_video())
        self.assertTrue(f[-1].endswith(os.path.join("loghi", "chiusura.png")))

    def test_a_corsi_finiti_restano_i_loghi(self):
        import podcast
        self.assertEqual(podcast.locandine_video(date(2027, 12, 1)), [podcast.loghi_video()])

    def test_senza_file_dei_loghi_nessuna_immagine_in_piu(self):
        import podcast
        vecchio = cfg.PODCAST_LOGHI
        try:
            cfg.PODCAST_LOGHI = "loghi/non-esiste.png"
            self.assertEqual(len(podcast.locandine_video(date(2026, 11, 9))), 2)
            self.assertEqual(podcast.locandine_video(date(2027, 12, 1)), [])
        finally:
            cfg.PODCAST_LOGHI = vecchio

    def test_durata_locandina_in_config(self):
        self.assertEqual(cfg.PODCAST_LOCANDINA_SEC, 5)


class TestCodaVideo(unittest.TestCase):
    """La voce di chiusura corre sulle locandine e finisce ai loghi."""

    def test_piano_voce_lunga_si_divide_fra_le_locandine(self):
        import podcast
        per, silenzio = podcast.piano_coda(12.0, 2, 5.0)
        self.assertEqual(per, 6.0)
        self.assertEqual(silenzio, 5.0)           # solo i loghi

    def test_piano_voce_corta_allunga_l_audio(self):
        import podcast
        per, silenzio = podcast.piano_coda(6.0, 4, 5.0)       # 4 locandine, minimo 3,5 s l'una
        self.assertEqual(per, 3.5)
        self.assertAlmostEqual(silenzio, 4 * 3.5 - 6.0 + 5.0)

    def test_piano_senza_locandine_solo_i_loghi(self):
        import podcast
        self.assertEqual(podcast.piano_coda(9.0, 0, 5.0), (0.0, 5.0))
        self.assertEqual(podcast.piano_coda(9.0, 0, 0.0), (0.0, 0.0))

    def test_scritta_sulla_locandina(self):
        import podcast
        html = podcast.html_locandina_corsi("AAAA")
        self.assertIn("Sei un medico d&#x27;emergenza-urgenza? Partecipa ai nostri corsi!", html.replace("&#39;", "&#x27;"))
        self.assertIn("newsletter", html)

    def test_chiusura_con_corsi_avvertenza_invito_saluti(self):
        import podcast
        testo, n_invito, n_saluti = podcast.chiusura_podcast(True)
        self.assertTrue(testo.startswith("Sintesi generate con l'intelligenza artificiale"))
        self.assertTrue(testo.endswith("Partecipa ai nostri corsi! Alla prossima."))
        self.assertEqual((n_invito, n_saluti), (8, 2))

    def test_chiusura_senza_corsi_niente_invito(self):
        import podcast
        testo, n_invito, n_saluti = podcast.chiusura_podcast(False)
        self.assertNotIn("corsi!", testo)
        self.assertTrue(testo.endswith("nella newsletter. Alla prossima."))
        self.assertEqual((n_invito, n_saluti), (0, 2))

    def test_inizio_invito_dai_tempi_delle_parole(self):
        import podcast
        testo, n_i, n_s = podcast.chiusura_podcast(True)
        parole = [("x", float(k), k + 0.5) for k in range(40)]          # 40 parole, la 40a e' l'ultima
        # le ultime 10 sono invito (8) e saluti (2): l'invito comincia alla parola 30
        self.assertEqual(podcast.inizio_invito_corsi(parole, n_i, n_s, 0, 40, testo), 30.0)

    def test_inizio_invito_stimato_senza_tempi_e_nessuno_senza_invito(self):
        import podcast
        testo, n_i, n_s = podcast.chiusura_podcast(True)
        t = podcast.inizio_invito_corsi([], n_i, n_s, 10.0, 20.0, testo)
        self.assertTrue(10.0 < t < 20.0)
        testo2, n_i2, n_s2 = podcast.chiusura_podcast(False)
        self.assertIsNone(podcast.inizio_invito_corsi([], n_i2, n_s2, 10.0, 20.0, testo2))

    @unittest.skipUnless(shutil.which("ffmpeg"), "serve ffmpeg")
    def test_video_voce_sulle_locandine_e_loghi_in_silenzio(self):
        import podcast
        with tempfile.TemporaryDirectory() as d:
            vecchio = cfg.PODCAST_DIR
            cfg.PODCAST_DIR = d
            try:
                mp3, cop = os.path.join(d, "a.mp3"), os.path.join(d, "c.png")
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                                "sine=frequency=300:duration=20", mp3], check=True)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                                "color=c=0x333333:s=1080x1350", "-frames:v", "1", cop], check=True)
                sezioni = [(0, 4), (4, 12), (12, 20)]                  # intro, 1 articolo, chiusura 8 s
                loc = podcast.locandine_video(date(2026, 11, 9))       # 2 locandine + loghi
                out = os.path.join(d, "v.mp4")
                podcast.genera_video(mp3, sezioni, [cop], cop, out, locandine=loc, inizio_corsi=14.0)
                dur = float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                    check=True, capture_output=True, text=True).stdout.strip())
                # copertina fino a 14 s; poi 2 locandine da 3,5 s sulla voce (6 s: l'audio si allunga
                # di 1 s) e 5 s di loghi in silenzio: 4 + 8 + 2 + 7 + 5
                self.assertAlmostEqual(dur, 26.0, delta=0.5)
                with open(os.path.join(d, "diapositive.txt")) as f:
                    voci = f.read()
                self.assertIn("duration 2.000", voci)                  # la copertina fino all'invito
                self.assertEqual(voci.count("duration 3.500"), 2)      # le due locandine
                self.assertIn("duration 5.000", voci)
            finally:
                cfg.PODCAST_DIR = vecchio


if __name__ == "__main__":
    unittest.main()
