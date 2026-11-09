"""Musica: credito nelle didascalie e tappeto sotto la voce del podcast."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
import instagram as ig  # noqa: E402


class Credito(unittest.TestCase):
    def test_credito_solo_se_il_file_esiste(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "m.mp3")

            class C:
                IG_MUSICA = f
                IG_MUSICA_CREDITO = "Musica: X (CC BY 4.0)"
            self.assertEqual(ig.con_credito_musica("Testo", C), "Testo")
            open(f, "wb").close()
            self.assertEqual(ig.con_credito_musica("Testo\n", C), "Testo\n\nMusica: X (CC BY 4.0)")
            C.IG_MUSICA_CREDITO = ""
            self.assertEqual(ig.con_credito_musica("Testo", C), "Testo")


@unittest.skipUnless(shutil.which("ffmpeg") and os.path.exists(os.path.join(RADICE, "podcast.py")),
                     "serve ffmpeg e il podcast (solo EM)")
class Tappeto(unittest.TestCase):
    def test_durata_invariata_e_file_assente(self):
        import podcast
        with tempfile.TemporaryDirectory() as d:
            mp3, mus = os.path.join(d, "v.mp3"), os.path.join(d, "m.mp3")
            for f, fr, t in ((mp3, 300, 8), (mus, 500, 3)):
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"sine=frequency={fr}:duration={t}", f], check=True)
            self.assertFalse(podcast.aggiungi_tappeto(mp3, os.path.join(d, "manca.mp3")))
            self.assertTrue(podcast.aggiungi_tappeto(mp3, mus))        # musica piu' corta: va in ciclo
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                        "-of", "csv=p=0", mp3], check=True, capture_output=True,
                                       text=True).stdout.strip())
            self.assertAlmostEqual(dur, 8.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
