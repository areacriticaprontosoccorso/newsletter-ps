"""Strada di riserva dei Reel: il video su un indirizzo pubblico (allegato temporaneo di una release)."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import instagram as ig  # noqa: E402


class C:
    IG_USER_ID, IG_PAGE_TOKEN, FB_PAGE_TOKEN, FB_API_VERSION = "IG", "TOK", "FBT", "v21.0"


class DaGithub(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.video = os.path.join(self.d, "reel.mp4")
        with open(self.video, "wb") as f:
            f.write(b"video")
        self.comandi = []
        self.env = {"GITHUB_REPOSITORY": "org/repo", "GH_TOKEN": "T"}

    def run_finto(self, *a, **k):
        self.comandi.append(a[0])
        return subprocess.CompletedProcess(a[0], 0, "", "")

    def test_senza_permessi_non_fa_nulla(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(ig.subprocess, "run") as r:
            self.assertIsNone(ig._reel_instagram_da_github(self.video, "x", C))
        r.assert_not_called()

    def test_si_puo_spegnere(self):
        with mock.patch.dict(os.environ, {**self.env, "REEL_URL_GITHUB": "0"}, clear=True), \
             mock.patch.object(ig.subprocess, "run") as r:
            self.assertIsNone(ig._reel_instagram_da_github(self.video, "x", C))
        r.assert_not_called()

    def test_carica_pubblica_e_cancella(self):
        with mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch.object(ig.subprocess, "run", self.run_finto), \
             mock.patch.object(ig, "pubblica_reel", return_value="REEL9") as pr:
            r = ig._reel_instagram_da_github(self.video, "Ciao", C)
        self.assertEqual(r, "REEL9")
        url = pr.call_args.kwargs["video_url"]
        self.assertRegex(url, r"^https://github\.com/org/repo/releases/download/reel-temp-[^/]+/reel-[0-9a-f]+\.mp4$")
        self.assertIsNone(pr.call_args.args[0])                       # nessun caricamento diretto
        self.assertEqual([c[:3] for c in self.comandi], [["gh", "release", "create"], ["gh", "release", "delete"]])
        self.assertIn("--cleanup-tag", self.comandi[1])
        self.assertEqual(self.comandi[0][3], self.comandi[1][3])      # stessa release
        self.assertFalse([n for n in os.listdir(self.d) if n.startswith(".reel-temp")])

    def test_se_instagram_rifiuta_la_release_sparisce_comunque(self):
        with mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch.object(ig.subprocess, "run", self.run_finto), \
             mock.patch.object(ig, "pubblica_reel", return_value=None):
            self.assertIsNone(ig._reel_instagram_da_github(self.video, "x", C))
        self.assertEqual(self.comandi[-1][:3], ["gh", "release", "delete"])

    def test_se_la_release_non_si_crea_non_c_nulla_da_cancellare(self):
        def rotto(*a, **k):
            self.comandi.append(a[0])
            raise subprocess.CalledProcessError(1, a[0], "", "HTTP 403: Resource not accessible")
        with mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch.object(ig.subprocess, "run", rotto), \
             mock.patch.object(ig, "pubblica_reel") as pr:
            self.assertIsNone(ig._reel_instagram_da_github(self.video, "x", C))
        pr.assert_not_called()
        self.assertEqual(len(self.comandi), 1)


class Avvisi(unittest.TestCase):
    """Se dopo il caricamento diretto c'e' la strada di riserva, il rifiuto e' un avviso, non un errore."""

    def prova(self, riserva):
        def finto(metodo, url, campi, file_path=None, timeout=60):
            if url.endswith("/media"):
                return {"id": "C1"}
            raise RuntimeError("HTTP 400: ProcessingFailedError")
        with mock.patch.object(ig, "_chiama", finto), mock.patch.object(ig, "_carica_video",
                                                                          side_effect=RuntimeError("ProcessingFailedError")), \
             mock.patch.object(ig.time, "sleep"):
            with self.assertLogs(ig.log, level="DEBUG") as lg:
                r = ig.pubblica_reel("v.mp4", "x", "IG", "TOK", riserva=riserva)
        self.assertIsNone(r)
        return [x.levelname for x in lg.records if "Reel non pubblicato" in x.getMessage()]

    def test_con_riserva_e_solo_un_avviso(self):
        self.assertEqual(self.prova(True), ["WARNING"])

    def test_senza_riserva_e_un_errore(self):
        self.assertEqual(self.prova(False), ["ERROR"])


if __name__ == "__main__":
    unittest.main()
