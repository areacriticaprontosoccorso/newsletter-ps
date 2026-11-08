"""Avvisi dei corsi nella newsletter EM: alternanza POCUS/NIV e locandine del video."""
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
