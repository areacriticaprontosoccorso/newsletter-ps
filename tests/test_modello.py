"""Modello Claude di EM (Sonnet 5.5): il ragionamento si spegne con {"type": "between_tools"}."""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RADICE)
import config as cfg  # noqa: E402
import newsletter_rss as nr  # noqa: E402


class Risposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ok(testo="ciao"):
    return Risposta(json.dumps({"content": [{"type": "text", "text": testo}],
                                "usage": {"input_tokens": 1, "output_tokens": 1}}).encode())


class Modello(unittest.TestCase):
    def test_modello_em(self):
        self.assertEqual(cfg.ANTHROPIC_MODEL, "claude-sonnet-5-5")

    def test_forma_del_thinking_spento(self):
        self.assertEqual(nr.thinking_spento("claude-sonnet-5-5"), {"type": "between_tools"})
        self.assertEqual(nr.thinking_spento("claude-sonnet-5"), {"type": "disabled"})

    def test_richiesta_senza_temperature_e_con_between_tools(self):
        with mock.patch.object(nr.urllib.request, "urlopen", return_value=ok()) as u:
            self.assertEqual(nr.chiama_claude("x"), "ciao")
        corpo = json.loads(u.call_args[0][0].data)
        self.assertEqual(corpo["model"], "claude-sonnet-5-5")
        self.assertEqual(corpo["thinking"], {"type": "between_tools"})
        self.assertNotIn("temperature", corpo)

    def test_thinking_attivo_omette_il_parametro(self):
        with mock.patch.object(nr.urllib.request, "urlopen", return_value=ok()) as u:
            nr.chiama_claude("x", thinking=True)
        self.assertNotIn("thinking", json.loads(u.call_args[0][0].data))

    def test_400_sul_thinking_riprova_con_l_altra_forma(self):
        errore = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(
            b'{"error":{"message":"thinking.type between_tools not supported; use disabled"}}'))
        with mock.patch.object(nr.urllib.request, "urlopen", side_effect=[errore, ok()]) as u:
            self.assertEqual(nr.chiama_claude("x"), "ciao")
        secondo = json.loads(u.call_args_list[1][0][0].data)
        self.assertEqual(secondo["thinking"], {"type": "disabled"})
        self.assertNotIn("_riprovato", secondo)


    def test_risposta_finita_nel_ragionamento_riprova_con_piu_spazio(self):
        solo_pensiero = Risposta(json.dumps({"content": [{"type": "thinking", "thinking": ""}],
                                             "stop_reason": "max_tokens",
                                             "usage": {"input_tokens": 1, "output_tokens": 3000}}).encode())
        with mock.patch.object(nr.urllib.request, "urlopen", side_effect=[solo_pensiero, ok("copione")]) as u:
            self.assertEqual(nr.chiama_claude("x", max_tokens=3000), "copione")
        secondo = json.loads(u.call_args_list[1][0][0].data)
        self.assertEqual(secondo["max_tokens"], 9000)
        self.assertNotIn("_piu_spazio", secondo)

    def test_copione_ha_spazio_per_il_ragionamento(self):
        import podcast
        import inspect
        self.assertIn("max_tokens=8000", inspect.getsource(podcast.copione) if hasattr(podcast, "copione")
                      else open(os.path.join(RADICE, "podcast.py"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
