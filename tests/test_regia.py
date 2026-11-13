"""Voce Gemini: le note di regia lette ad alta voce si riconoscono e la voce si scarta."""
import os
import sys
import unittest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
import config as cfg  # noqa: E402
import podcast  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
