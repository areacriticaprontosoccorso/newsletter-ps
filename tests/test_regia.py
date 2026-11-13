"""Voce Gemini: le note di regia lette ad alta voce si riconoscono e la voce si scarta."""
import os
import sys
import unittest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
import config as cfg  # noqa: E402
import podcast  # noqa: E402

try:
    import numpy  # noqa: F401
    NUMPY = True
except ImportError:
    NUMPY = False


class Regia(unittest.TestCase):
    def test_note_lette_riconosciute(self):
        sentito = ("Secondo studio, come un medico d'urgenza che presenta i colleghi la rassegna settimanale "
                   "della letteratura. Tono professionale, caldo e naturale, ritmo sostenuto ma ben scandito.")
        self.assertTrue(podcast.regia_letta(sentito, "Secondo studio, su BMC Pulmonary Medicine."))

    def test_copione_letto_bene(self):
        testo = "Secondo studio, su BMC Pulmonary Medicine: ventisette pazienti con riacutizzazione grave."
        self.assertFalse(podcast.regia_letta(testo, testo))

    def test_parole_comuni_non_bastano(self):
        # una sola sequenza in comune con le note (es. «stesso ritmo dall») non basta
        self.assertFalse(podcast.regia_letta("con lo stesso timbro e via", "altro testo"))

    def test_soglia_minima_e_pulizia_configurate(self):
        self.assertLess(cfg.PODCAST_SOGLIA_MINIMA, cfg.PODCAST_SOGLIA_WHISPER)
        self.assertTrue(cfg.PODCAST_PULIZIA)


@unittest.skipUnless(NUMPY, "serve numpy")
class Rumori(unittest.TestCase):
    SR = 16000

    def _voce(self, secondi):
        import numpy as np
        t = np.arange(int(secondi * self.SR)) / self.SR
        # una «voce»: armoniche di 110 Hz modulate, spettro tutt'altro che piatto
        return (0.2 * sum(np.sin(2 * np.pi * 110 * h * t) / h for h in range(1, 8))
                * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)

    def test_lampo_di_rumore_tolto_voce_intatta(self):
        import numpy as np
        rng = np.random.default_rng(0)
        voce, silenzio = self._voce(2.0), np.zeros(int(0.4 * self.SR), dtype=np.float32)
        lampo = (0.3 * rng.standard_normal(int(0.2 * self.SR))).astype(np.float32)
        a = np.concatenate([voce, silenzio, lampo, silenzio, voce])
        out, trovati = podcast.silenzia_rumori(a, self.SR)
        self.assertEqual(len(out), len(a))
        self.assertEqual(len(trovati), 1)
        i0 = len(voce) + len(silenzio)
        self.assertLess(np.abs(out[i0 + 400:i0 + len(lampo) - 400]).max(), 1e-6)
        np.testing.assert_array_equal(out[:len(voce) - 2000], a[:len(voce) - 2000])
        np.testing.assert_array_equal(out[-len(voce) + 2000:], a[-len(voce) + 2000:])

    def test_schiocco_fra_due_silenzi_tolto(self):
        import numpy as np
        silenzio = np.zeros(int(0.3 * self.SR), dtype=np.float32)
        schiocco = np.full(int(0.01 * self.SR), 0.8, dtype=np.float32)
        a = np.concatenate([self._voce(1.0), silenzio, schiocco, silenzio, self._voce(1.0)])
        out, trovati = podcast.silenzia_rumori(a, self.SR)
        self.assertTrue(trovati)
        self.assertLess(np.abs(out[len(a) // 2 - 2000:len(a) // 2 + 2000]).max(), 1e-6)

    def test_solo_voce_nulla_cambia(self):
        a = self._voce(3.0)
        out, trovati = podcast.silenzia_rumori(a, self.SR)
        self.assertEqual(trovati, [])
        self.assertTrue((out == a).all())


@unittest.skipUnless(NUMPY, "serve numpy")
class PunteggioComplessivo(unittest.TestCase):
    """Il punteggio complessivo e' la media pesata dei blocchi (il 13/11 il confronto sull'intero
    copione dava 0,27 con blocchi da 0,85-0,97 e scartava un podcast buono)."""

    def _voce(self, punteggi):
        import numpy as np
        from unittest import mock
        v = podcast.VoceGemini.__new__(podcast.VoceGemini)
        v.np, v.sr = np, 16000
        sezioni = [f"Sezione numero {k} con un testo di prova abbastanza lungo." for k in range(6)]
        gruppi = [[0, 1], [2, 3], [4, 5]]
        v.blocchi = lambda vis: gruppi
        risposte = iter([(np.zeros(16000, dtype=np.float32), p,
                          [(" " + w, 0.1 * i, 0.1 * i + 0.05) for i, w in enumerate(
                              " ".join(sezioni[g[0]:g[-1] + 1]).split())]) for p, g in zip(punteggi, gruppi)])
        v._blocco = lambda sez, modello: next(risposte)
        v._allinea = lambda parole, sez: [0.0] + [None] * (len(sez) - 1)
        with mock.patch.object(podcast.cfg, "PODCAST_GEMINI_UNIFORMA", False), \
                mock.patch.object(podcast.cfg, "PODCAST_GEMINI_MODELLI", ["m"]):
            return v.podcast_verificato(sezioni)

    def test_blocchi_buoni_podcast_buono(self):
        _, _, _, punti, _ = self._voce([0.96, 0.97, 0.85])
        self.assertAlmostEqual(punti, (0.96 + 0.97 + 0.85) / 3, places=2)

    def test_blocchi_scarsi_podcast_scartato(self):
        with self.assertRaises(RuntimeError):
            self._voce([0.5, 0.6, 0.7])


if __name__ == "__main__":
    unittest.main()
