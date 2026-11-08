"""monitor.py: controllo del mancato avvio e riepilogo settimanale (senza rete)."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor  # noqa: E402

U = timezone.utc


def run(creato, stato="completed", esito="success", iniziato=None):
    return {"created_at": creato, "run_started_at": iniziato or creato, "status": stato, "conclusion": esito,
            "html_url": "https://github.com/x/y/actions/runs/1"}


class Controllo(unittest.TestCase):
    ADESSO = datetime(2026, 11, 9, 11, 30, tzinfo=U)         # lunedi 09/11, 12:30 a Roma

    def test_nessun_run_oggi_e_un_problema(self):
        p = monitor.valuta_runs("EM", [run("2026-11-02T06:50:00Z")], self.ADESSO, 120)
        self.assertEqual(len(p), 1)
        self.assertIn("non e' partito", p[0])

    def test_run_riuscito_nessun_problema(self):
        self.assertEqual(monitor.valuta_runs("EM", [run("2026-11-09T06:50:00Z")], self.ADESSO, 120), [])

    def test_ripartito_a_mano_dopo_un_fallimento_va_bene(self):
        runs = [run("2026-11-09T06:50:00Z", esito="failure"), run("2026-11-09T07:30:00Z")]
        self.assertEqual(monitor.valuta_runs("EM", runs, self.ADESSO, 120), [])

    def test_run_fallito(self):
        p = monitor.valuta_runs("EM", [run("2026-11-09T06:50:00Z", esito="failure")], self.ADESSO, 120)
        self.assertIn("failure", p[0])

    def test_run_in_corso_da_poco_va_bene_da_troppo_no(self):
        recente = run("2026-11-09T11:00:00Z", "in_progress", None)
        vecchio = run("2026-11-09T07:00:00Z", "in_progress", None)
        self.assertEqual(monitor.valuta_runs("EM", [recente], self.ADESSO, 120), [])
        self.assertIn("bloccato", monitor.valuta_runs("EM", [vecchio], self.ADESSO, 120)[0])

    def test_giorno_giusto_per_ogni_newsletter(self):
        chiamati = []

        def leggi(n):
            chiamati.append(n["nome"])
            return []
        for giorno, atteso in ((9, "EM Weekly Digest"), (11, "POCUS Weekly Digest"), (13, "NIV Weekly Digest")):
            chiamati.clear()
            monitor.controlla(datetime(2026, 11, giorno, 11, 30, tzinfo=U), leggi_runs=leggi)
            self.assertEqual(chiamati, [atteso])
        chiamati.clear()
        monitor.controlla(datetime(2026, 11, 10, 11, 30, tzinfo=U), leggi_runs=leggi)    # martedi
        self.assertEqual(chiamati, [])

    def test_github_non_raggiungibile_e_segnalato(self):
        def leggi(n):
            raise RuntimeError("HTTP 503")
        p = monitor.controlla(self.ADESSO, leggi_runs=leggi)
        self.assertIn("impossibile leggere", p[0])


class Iscritti(unittest.TestCase):
    N = {"nome": "POCUS", "repo": "o/r", "iscritti": "subscribers.json"}
    DA = datetime(2026, 11, 1, tzinfo=U)

    def file(self, quanti, nuovi_dal=None):
        voci = [{"email": f"a{i}@x.it", "subscribed_at": "2026-05-01T00:00:00.000Z"} for i in range(quanti)]
        for i in range(nuovi_dal or 0):
            voci[-1 - i]["subscribed_at"] = "2026-11-05T10:00:00.000Z"
        return json.dumps(voci)

    def test_totale_nuovi_e_usciti(self):
        # prima 100, ora 108 con 10 nuovi: 2 usciti
        with mock.patch("monitor.gh", return_value=[{"sha": "abc"}]):
            s = monitor.stat_iscritti(self.N, self.DA, leggi_file=lambda rif: self.file(100) if rif == "abc"
                                      else self.file(108, 10))
        self.assertEqual((s["totale"], s["nuovi"], s["prima"], s["usciti"]), (108, 10, 100, 2))

    def test_senza_storico_niente_usciti(self):
        with mock.patch("monitor.gh", return_value=[]):
            s = monitor.stat_iscritti(self.N, self.DA, leggi_file=lambda rif: self.file(50, 3))
        self.assertEqual((s["totale"], s["nuovi"]), (50, 3))
        self.assertNotIn("usciti", s)

    def test_errore_di_lettura(self):
        def leggi(rif):
            raise RuntimeError("HTTP 404")
        self.assertIn("errore", monitor.stat_iscritti(self.N, self.DA, leggi_file=leggi))

    def test_destinatari_em(self):
        self.assertEqual(monitor.conta_destinatari_em({"DESTINATARI": "a@x.it, b@x.it,,"}), 2)
        self.assertIsNone(monitor.conta_destinatari_em({}))


class Graph(unittest.TestCase):
    DA = datetime(2026, 11, 2, tzinfo=U)

    def test_instagram_post_della_settimana_e_permesso_mancante(self):
        def fake(percorso, token, versione, **p):
            if percorso == "IG":
                return {"username": "u", "followers_count": 1234}
            if percorso == "IG/media":
                return {"data": [
                    {"id": "1", "timestamp": "2026-11-04T06:00:00+0000", "media_product_type": "REELS",
                     "media_type": "VIDEO", "caption": "POCUS\nresto", "like_count": 10, "comments_count": 1},
                    {"id": "2", "timestamp": "2026-10-20T06:00:00+0000", "media_type": "CAROUSEL_ALBUM"}]}
            if percorso.endswith("/insights"):
                raise RuntimeError("(#10) Application does not have permission for this action (codice 10)")
        with mock.patch("monitor.graph", side_effect=fake):
            r = monitor.stat_instagram("IG", "tok", self.DA, versione="v25.0")
        self.assertEqual(len(r["post"]), 1)                       # quello del 20/10 e' fuori
        self.assertEqual(r["post"][0]["tipo"], "Reel")
        self.assertEqual(r["post"][0]["testo"], "POCUS")
        self.assertTrue(r["senza_permesso"])
        self.assertIsNone(r["post"][0]["views"])

    def test_errore_dell_account_non_solleva(self):
        with mock.patch("monitor.graph", side_effect=RuntimeError("token scaduto (codice 190)")):
            r = monitor.stat_instagram("IG", "tok", self.DA, versione="v25.0")
        self.assertIn("190", r["errore"])

    def test_versione_di_ripiego(self):
        def fake(percorso, token, versione, **p):
            if versione != "v23.0":
                raise RuntimeError("Unsupported")
            return {"id": "1"}
        with mock.patch("monitor.graph", side_effect=fake):
            self.assertEqual(monitor.versione_funzionante("IG", "t"), "v23.0")

    def test_errori_http_senza_token(self):
        import io
        import urllib.error
        corpo = json.dumps({"error": {"message": "Invalid access_token=EAAB1234567890123456789012345", "code": 190}})
        err = urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(corpo.encode()))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as c:
                monitor.http_json("https://graph.facebook.com/x")
        self.assertNotIn("EAAB1234567890", str(c.exception))
        self.assertIn("190", str(c.exception))


class Riepilogo(unittest.TestCase):
    def test_testo_completo_senza_indirizzi(self):
        a = datetime(2026, 11, 8, 17, 0, tzinfo=U)
        q = datetime(2026, 11, 4, 5, 0, tzinfo=U).astimezone(monitor.ROMA)
        ig = {"utente": "u", "follower": 1200, "senza_permesso": False,
              "post": [{"quando": q, "tipo": "Reel", "testo": "POCUS Weekly", "views": 900, "reach": 500, "saved": 7,
                        "shares": 3, "like": 40, "commenti": 2, "link": ""}]}
        fb = {"nome": "Pagina", "follower": 800, "post": [{"quando": q, "testo": "Post", "link": "", "reazioni": 5,
                                                          "commenti": 1, "condivisioni": 0, "visualizzazioni": None}]}
        isc = {"POCUS Weekly Digest": {"totale": 108, "nuovi": 10, "prima": 100, "usciti": 2}}
        oggetto, testo = monitor.componi_statistiche(a - timedelta(days=7), a, ig, fb, isc, 50)
        self.assertIn("08/11", oggetto)
        for atteso in ("108 iscritti", "10 nuovi", "circa 2 disiscritti", "50 destinatari", "@u", "1.200 follower",
                       "visualizzazioni    900", "mer 04/11", "visualizzazioni non lette"):
            self.assertIn(atteso, testo)
        self.assertNotIn("@x.it", testo)

    def test_errori_diventano_righe(self):
        a = datetime(2026, 11, 8, 17, 0, tzinfo=U)
        _, testo = monitor.componi_statistiche(a - timedelta(days=7), a, {"errore": "token scaduto"},
                                               {"errore": "x"}, {"P": {"errore": "HTTP 404"}}, None)
        self.assertIn("Non disponibile: token scaduto", testo)
        self.assertIn("non letti (HTTP 404)", testo)
        self.assertIn("n/d destinatari", testo)


class Main(unittest.TestCase):
    def test_controlla_scrive_solo_se_ci_sono_problemi(self):
        with mock.patch("monitor.controlla", return_value=[]), mock.patch("avviso.invia") as invia:
            monitor.main(["controlla"])
        self.assertFalse(invia.called)
        with mock.patch("monitor.controlla", return_value=["EM: non partito"]), mock.patch("avviso.invia") as invia:
            monitor.main(["controlla"])
        self.assertTrue(invia.called)
        self.assertIn("EM: non partito", invia.call_args[0][1])

    def test_statistiche_sempre(self):
        with mock.patch("monitor.statistiche", return_value=("ogg", "testo")), mock.patch("avviso.invia") as invia:
            monitor.main(["statistiche"])
        invia.assert_called_once_with("ogg", "testo")

    def test_comando_sconosciuto(self):
        self.assertEqual(monitor.main(["boh"]), 2)


if __name__ == "__main__":
    unittest.main()
