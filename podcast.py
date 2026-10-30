"""
EM Weekly Digest — podcast settimanale (una voce, circa due minuti).

    python podcast.py genera   [--archivio FILE] [--copione FILE]
    python podcast.py pubblica

genera   Dall'archivio JSON del digest appena prodotto:
         1. copione: UNA chiamata a Claude (le regole sono in config.PODCAST_PROMPT);
         2. controllo: ogni numero del copione deve comparire nell'abstract o nella
            sintesi dello stesso articolo, la lunghezza deve rientrare nei limiti;
            se no, Claude corregge (massimo 3 tentativi), poi si rinuncia;
         3. voce: Chatterbox in locale, in italiano; nomi di riviste, studi e
            termini inglesi in voce inglese, uniti senza pause alla frase;
         4. verifica: ogni frase viene ritrascritta con Whisper e rigenerata se non
            corrisponde al testo;
         5. MP3 e video MP4 (copertina + schede degli articoli che scorrono mentre
            se ne parla). Esito in podcast/esito.json.
         --copione usa un copione già scritto (array JSON come quello di Claude)
         e salta la chiamata API: serve per le prove.

pubblica Il post Facebook del lunedì, rimandato da newsletter_rss.py: video del
         podcast + didascalia del primo articolo, programmato allo stesso orario.
         Se il video non c'è (podcast fallito o disattivato) pubblica il post del
         primo articolo con l'immagine, esattamente come prima. In DRY_RUN scrive
         solo l'anteprima in post_facebook_podcast.txt.

Nessuna delle due fasi blocca la newsletter: la email è già partita.
"""

import argparse
import base64
import difflib
import glob
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config as cfg
import newsletter_rss as nr

log = nr.log
RE_EN = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


# ═══════════════════════════════════════════════════════════════════════════════
# COPIONE
# ═══════════════════════════════════════════════════════════════════════════════
def archivio_recente(percorso=None):
    if percorso:
        return percorso
    files = sorted(glob.glob(os.path.join(cfg.ARCHIVIO_DIR, "digest-s*.json")),
                   key=os.path.getmtime)
    if not files:
        raise RuntimeError(f"Nessun archivio del digest in {cfg.ARCHIVIO_DIR}/")
    return files[-1]


def testo_visibile(t):
    """Il testo come si legge: [[Circulation]] -> Circulation, [[X|pronuncia]] -> X."""
    return RE_EN.sub(lambda m: m.group(1), t)


def conta_parole(t):
    return len(testo_visibile(t).split())


def _blocco_articolo(i, a):
    return (f"--- ARTICOLO {i} ---\n"
            f"pmid: {a['pmid']}\n"
            f"rivista: {a['rivista']}\n"
            f"disegno: {a.get('disegno') or 'n/d'}\n"
            f"titolo: {a.get('titolo_it') or a['titolo']}\n"
            f"sintesi: {a.get('sintesi_it') or ''}\n"
            f"rilevanza: {a.get('rilevanza') or ''}\n"
            f"limite: {a.get('limite') or ''}\n"
            f"abstract: {a.get('abstract') or ''}\n")


# ── Controllo dei numeri ──────────────────────────────────────────────────────
_NUM_EN = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_NUM_EN.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
                "eighty": 80, "ninety": 90, "hundred": 100})
_RE_NUM_FONTE = re.compile(r"\d+(?:[.,]\d+)*(?:[    ]\d{3}(?!\d))*")
_RE_NUM_COPIONE = re.compile(r"\d+(?:\.\d{3})*(?:,\d+)?")


def _interpretazioni(s):
    """Un numero scritto all'inglese o all'italiana può voler dire cose diverse
    (8,115 = 8115 oppure 8.115): si tengono tutte le letture."""
    s = re.sub(r"[    ]", "", s)
    out = set()
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        mig = "," if dec == "." else "."
        out.add(float(s.replace(mig, "").replace(dec, ".")))
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        if re.fullmatch(r"\d{1,3}(?:" + re.escape(sep) + r"\d{3})+", s):
            out.add(float(s.replace(sep, "")))
        if s.count(sep) == 1:
            out.add(float(s.replace(sep, ".")))
    else:
        out.add(float(s))
    return out


def numeri_fonte(a):
    testo = " ".join(str(a.get(k) or "") for k in
                     ("abstract", "sintesi_it", "titolo", "titolo_it", "rilevanza", "limite"))
    out = set()
    for m in _RE_NUM_FONTE.finditer(testo):
        out |= _interpretazioni(m.group(0))
    for w in re.findall(r"[a-z]+", testo.lower()):
        if w in _NUM_EN:
            out.add(float(_NUM_EN[w]))
    return out


def numeri_non_trovati(testo, a):
    """Numeri del copione assenti dalla fonte. Ammessi: valore identico e
    arrotondamento all'intero o al decimale; con 'circa' uno scarto fino al 5%;
    con 'oltre'/'più di' un valore fino al 25% sotto la fonte, con 'quasi'/'meno di'
    fino al 20% sopra (sono limiti, non stime)."""
    fonte = numeri_fonte(a)
    visibile = testo_visibile(testo)
    mancanti = []
    for m in _RE_NUM_COPIONE.finditer(visibile):
        n = float(m.group(0).replace(".", "").replace(",", "."))
        prima = visibile[max(0, m.start() - 14):m.start()].lower()
        circa = "circa" in prima
        oltre = "oltre" in prima or "più di" in prima
        quasi = "quasi" in prima or "meno di" in prima
        ok = any(
            abs(n - v) < 1e-9
            or (abs(n - round(v)) < 1e-9 and abs(n - v) < 1)
            or abs(n - round(v, 1)) < 1e-9
            or (circa and v and abs(n - v) / abs(v) <= 0.05)
            or (oltre and n <= v <= n * 1.25)
            or (quasi and n * 0.8 <= v <= n)
            for v in fonte
        )
        if not ok:
            mancanti.append(m.group(0))
    return mancanti


def limiti_parole(n):
    f = n / max(1, cfg.ARTICOLI_FINALI)
    return round(cfg.PODCAST_PAROLE_MIN * f), round(cfg.PODCAST_PAROLE_MAX * f)


def intro(n):
    return cfg.PODCAST_INTRO.replace("{Articoli}", _num_it(n).capitalize())


def problemi_copione(articoli, testi):
    problemi = []
    p_min, p_max = limiti_parole(len(articoli))
    for i, (a, t) in enumerate(zip(articoli, testi), 1):
        if not t:
            problemi.append(f"manca il paragrafo dell'articolo {i} (pmid {a['pmid']})")
            continue
        if t.count("[[") != t.count("]]"):
            problemi.append(f"articolo {i}: doppie parentesi quadre non bilanciate")
        fuori = numeri_non_trovati(t, a)
        if fuori:
            problemi.append(
                f"articolo {i}: numeri non presenti nell'abstract o nella sintesi "
                f"({', '.join(fuori)}): riportali come nella fonte o eliminali")
        per_max = round(p_max / len(articoli))
        if conta_parole(t) > per_max + 12:
            problemi.append(f"articolo {i}: {conta_parole(t)} parole, massimo {per_max}: "
                            f"tieni un solo risultato e un solo limite")
        lunghe = [f for f in dividi_frasi(testo_visibile(t)) if len(f.split()) > 34]
        if lunghe:
            problemi.append(f"articolo {i}: frase troppo lunga per l'ascolto: '{lunghe[0][:60]}...'")
    tot = sum(conta_parole(t) for t in testi if t)
    if tot > p_max + 10:
        problemi.append(f"copione troppo lungo: {tot} parole, massimo {p_max}")
    if tot < p_min - 30:
        problemi.append(f"copione troppo corto: {tot} parole, minimo {p_min}")
    return problemi


def _testi_da_risposta(risposta, articoli):
    voci = [v for v in nr._estrai_json_array(risposta) if isinstance(v, dict)]
    per_pmid = {str(v.get("pmid", "")).strip(): (v.get("testo") or "").strip() for v in voci}
    testi = [per_pmid.get(str(a["pmid"]), "") for a in articoli]
    if not all(testi) and len(voci) == len(articoli):
        # pmid storpiati ma numero giusto di paragrafi: vale l'ordine
        testi = [(v.get("testo") or "").strip() for v in voci]
    return testi


def scrivi_copione(articoli, copione_file=None):
    n = len(articoli)
    if copione_file:
        with open(copione_file, encoding="utf-8") as f:
            testi = _testi_da_risposta(f.read(), articoli)
        problemi = problemi_copione(articoli, testi)
        if problemi:
            raise RuntimeError("Copione di prova non valido: " + "; ".join(problemi))
        return testi

    p_min, p_max = limiti_parole(n)
    prompt = cfg.PODCAST_PROMPT.format(
        n=n, min_parole=p_min, max_parole=p_max,
        per_min=round(p_min / n), per_max=round(p_max / n),
        blocchi="\n".join(_blocco_articolo(i, a) for i, a in enumerate(articoli, 1)),
    )
    aggiunta, problemi = "", []
    for tentativo in range(1, 4):
        risposta = nr.chiama_claude(prompt + aggiunta, max_tokens=3000)
        try:
            testi = _testi_da_risposta(risposta, articoli)
            problemi = problemi_copione(articoli, testi)
        except ValueError as e:
            testi, problemi = [], [f"risposta non in formato JSON: {e}"]
        if not problemi:
            log.info(f"Copione pronto al tentativo {tentativo}: "
                     f"{sum(conta_parole(t) for t in testi)} parole")
            return testi
        log.warning(f"Copione, tentativo {tentativo}: " + " | ".join(problemi))
        aggiunta = ("\n\nLA TUA VERSIONE PRECEDENTE AVEVA QUESTI PROBLEMI. Riscrivila "
                    "correggendoli e rispettando tutte le regole:\n- " + "\n- ".join(problemi)
                    + "\n\nVERSIONE PRECEDENTE:\n" + risposta)
    # Ultima rete: se l'unico difetto residuo è la lunghezza e lo sforamento è contenuto
    # (+20%), meglio un podcast un po' più lungo che nessun podcast. Il video viene
    # accelerato fino al limite e l'esito.json registra la durata reale.
    solo_lunghezza = all(("parole, massimo" in x or "troppo lungo" in x) for x in problemi)
    tot = sum(conta_parole(t) for t in testi if t)
    if testi and all(testi) and solo_lunghezza and tot <= p_max * 1.2:
        log.warning(f"Copione accettato oltre il limite ({tot} parole, massimo {p_max}): "
                    "il podcast durerà più di due minuti")
        return testi
    raise RuntimeError("Copione non valido dopo 3 tentativi: " + "; ".join(problemi))


# ═══════════════════════════════════════════════════════════════════════════════
# TESTO -> FRASI -> PARTI ITALIANO / INGLESE
# ═══════════════════════════════════════════════════════════════════════════════
def dividi_frasi(t):
    """Frasi per la sintesi: le brevissime si uniscono alla successiva (un input
    troppo corto fa aggiungere al modello sillabe spurie)."""
    pezzi = re.split(r"(?<=[.?!;])\s+", t.strip())
    out = []
    for p in pezzi:
        if out and len(testo_visibile(out[-1])) < 40 and \
                len(testo_visibile(out[-1] + " " + p).split()) <= 34:
            out[-1] += " " + p
        elif p:
            out.append(p)
    return out


def _num_it(intero):
    from num2words import num2words
    return num2words(int(intero), lang="it")


def numeri_in_parole(t):
    """60.000 -> sessantamila; 4,5% -> quattro virgola cinque per cento."""
    def conv(m):
        s, perc = m.group(1), m.group(2)
        intero, _, dec = s.replace(".", "").partition(",")
        parole = _num_it(intero)
        if dec:
            zeri = len(dec) - len(dec.lstrip("0"))
            resto = dec.lstrip("0")
            parole += " virgola " + " ".join(["zero"] * zeri + ([_num_it(resto)] if resto else []))
        return parole + (" per cento" if perc else "")
    t = re.sub(r"(\d+(?:\.\d{3})*(?:,\d+)?)(\s*%)?", conv, t)
    # "uno millilitro" -> "un millilitro" (ma "uno virgola...", "uno per cento" restano)
    return re.sub(r"\buno (?!virgola|per cento)(?=[a-zàèéìòù])", "un ", t)


def pronuncia_it(t):
    for a, b in cfg.PODCAST_PRONUNCIA_IT.items():
        t = re.sub(r"(?<![\w-])" + re.escape(a) + r"(?![\w-])", b, t)
    return numeri_in_parole(t)


_CIFRE_EN = "zero one two three four five six seven eight nine".split()


def _sigla_en(vis):
    """Sigle maiuscole (GFAP, DOAC, UCH-L1) senza pronuncia indicata: lette lettera per
    lettera all'inglese. Il modello da solo le storpia ("UCH-L1" diventava "HLN")."""
    if re.fullmatch(r"[A-Z0-9][A-Z0-9\-]+", vis) and re.search(r"[A-Z]", vis):
        return " ".join(_CIFRE_EN[int(c)] if c.isdigit() else c
                        for c in vis.replace("-", ""))
    return vis


def parti_frase(frase):
    """[('it', testo), ('en', testo), ...] pronti per la sintesi.
    La punteggiatura che segue un termine inglese passa al segmento inglese
    (ne guida l'intonazione); le parole inglesi brevi note diventano grafia fonetica."""
    parti, buf, pos = [], "", 0
    for m in RE_EN.finditer(frase):
        buf += frase[pos:m.start()]
        vis, pron = m.group(1).strip(), (m.group(2) or "").strip()
        fon = cfg.PODCAST_GRAFIA_FONETICA.get(vis.lower())
        pos = m.end()
        if fon and not pron:
            buf += fon
            continue
        if buf.strip():
            parti.append(("it", buf))
        buf = ""
        punt = re.match(r"\s*([,.:;?!]+)", frase[pos:])
        en = pron or _sigla_en(vis)
        if punt:
            en += punt.group(1)
            pos += punt.end()
        parti.append(("en", en))
    buf += frase[pos:]
    if buf.strip(" ,.;:"):
        parti.append(("it", buf))
    pulite = []
    for lingua, txt in parti:
        txt = re.sub(r"\s+", " ", txt).strip()
        if lingua == "it":
            txt = pronuncia_it(txt)
        if txt.strip(" ,.;:"):
            pulite.append((lingua, txt))
    return pulite


def _norm(t):
    t = numeri_in_parole(t.lower().replace("’", "'"))
    t = re.sub(r"[^a-zàèéìòù0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _parole(t):
    return [w for w in re.split(r"[\s\-']+", re.sub(r"[^\wàèéìòù\s\-']", " ", t.lower())) if w]


# ═══════════════════════════════════════════════════════════════════════════════
# VOCE
# ═══════════════════════════════════════════════════════════════════════════════
class Voce:
    XF = 0.025                                   # dissolvenza alle giunture (s)
    PAUSA = {",": 0.07, ":": 0.10, ";": 0.12, ".": 0.22, "?": 0.22, "!": 0.22}

    def __init__(self):
        import numpy as np
        import torch
        import librosa
        import pyloudnorm as pyln
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        from faster_whisper import WhisperModel
        self.np, self.torch, self.librosa, self.pyln = np, torch, librosa, pyln
        torch.set_num_threads(os.cpu_count() or 2)
        log.info("Caricamento modello vocale (Chatterbox) e Whisper...")
        self.tts = ChatterboxMultilingualTTS.from_pretrained(device="cpu")
        # Il campione di voce si elabora una volta sola, non a ogni frase.
        self.tts.prepare_conditionals(cfg.PODCAST_VOCE_RIF, exaggeration=0.5)
        self.sr = self.tts.sr
        self.stt = WhisperModel(cfg.PODCAST_WHISPER_MODELLO, device="cpu", compute_type="int8")
        self.meter = pyln.Meter(self.sr)

    # ── primitive ──
    def sintetizza(self, testo, lingua, seme):
        self.torch.manual_seed(seme)
        with self.torch.inference_mode():
            w = self.tts.generate(testo, language_id=lingua,
                                  exaggeration=0.5, cfg_weight=0.5, temperature=0.7)
        # Chatterbox 0.1.7 aggiunge a ogni chiamata un forward hook (analisi
        # dell'allineamento) e non lo toglie: memoria e tempi crescerebbero frase
        # dopo frase fino a esaurire la RAM. Lo si rimuove qui.
        for strato in getattr(getattr(self.tts.t3, "tfmr", None), "layers", []):
            attn = getattr(strato, "self_attn", None)
            if attn is not None:
                attn._forward_hooks.clear()
        import gc
        gc.collect()
        try:   # restituisce al sistema la memoria liberata (glibc)
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass
        return w.squeeze(0).cpu().numpy().astype(self.np.float32)

    def sintetizza_robusto(self, testo, lingua, seme):
        """Chatterbox va in errore (max() su tensore vuoto) con i segmenti di una lettera,
        come la "e" fra GFAP e UCH-L1. Per quelli si raddoppia la lettera; se anche così
        fallisce e il segmento è di una o due lettere, lo si salta (None: resta una pausa).
        Per gli altri l'errore si riprova con un altro seme e poi si propaga."""
        lettere = re.sub(r"[^\wàèéìòù]", "", testo)
        prove = [f"{lettere} {lettere}"] if len(lettere) == 1 else [testo]
        ultimo = None
        for k in range(2):
            for t in prove:
                try:
                    return self.sintetizza(t, lingua, seme + 13 * k)
                except Exception as e:
                    ultimo = e
                    log.warning(f"    sintesi di '{t}' fallita: {e}")
        if len(lettere) <= 2:
            return None
        raise ultimo

    def trascrivi(self, a, lingua, parole=False):
        a16 = self.librosa.resample(a, orig_sr=self.sr, target_sr=16000)
        segs, _ = self.stt.transcribe(a16, language=lingua, beam_size=5, word_timestamps=parole)
        return list(segs)

    def rifila(self, a):
        iv = self.librosa.effects.split(a, top_db=30, frame_length=512, hop_length=128)
        if len(iv) == 0:
            return a
        pad = int(0.012 * self.sr)
        return a[max(0, iv[0][0] - pad): min(len(a), iv[-1][1] + pad)]

    def taglia_coda(self, a, lingua, testo):
        """Sui segmenti brevi il modello a volte aggiunge sillabe dopo la fine:
        si taglia dopo l'ultima parola attesa (tempi parola di Whisper)."""
        attese = _parole(testo)
        parole = [w for s in self.trascrivi(a, lingua, parole=True) for w in s.words]
        if not parole or not attese:
            return a
        fine = None
        for i, w in enumerate(parole[:len(attese) + 2]):
            tok = re.sub(r"[^\wàèéìòù]", "", w.word.lower())
            if i >= len(attese) - 2 and tok and (
                    difflib.SequenceMatcher(None, tok, attese[-1]).ratio() > 0.6
                    or attese[-1].endswith(tok)):
                fine = w.end
        if fine is None:
            return a
        taglio = int((fine + 0.2) * self.sr)
        return a[:taglio] if taglio < len(a) - int(0.4 * self.sr) else a

    def livella(self, a, rif_rms):
        np = self.np
        if len(a) / self.sr >= 0.5:
            try:
                return self.pyln.normalize.loudness(a, self.meter.integrated_loudness(a), -20.0)
            except Exception:
                pass
        rms = float(np.sqrt(np.mean(a ** 2))) + 1e-9
        return a * (rif_rms / rms) if rif_rms else a

    def unisci(self, pezzi):
        """Pezzi audio e pause (float, secondi) con dissolvenza incrociata."""
        np = self.np
        n = int(self.XF * self.sr)
        out = None
        for p in pezzi:
            if isinstance(p, float):
                out = np.concatenate([out, np.zeros(int(p * self.sr), dtype=np.float32)])
            elif out is None:
                out = p
            elif len(out) > n and len(p) > n and np.abs(out[-n:]).max() > 1e-4:
                f = np.linspace(0, 1, n, dtype=np.float32)
                out = np.concatenate([out[:-n], out[-n:] * (1 - f) + p[:n] * f, p[n:]])
            else:
                out = np.concatenate([out, p])
        return out

    # ── una frase ──
    def frase(self, frase_vis, parti, seme):
        np = self.np
        pezzi, rif = [], None
        for k, (lingua, testo) in enumerate(parti):
            grezzo = self.sintetizza_robusto(testo, lingua, seme + 7 * k)
            if grezzo is None:          # connettivo di una lettera non sintetizzabile
                if pezzi:
                    pezzi.append(0.1)
                continue
            a = self.rifila(grezzo)
            if len(_parole(testo)) <= 3:
                a = self.rifila(self.taglia_coda(a, lingua, testo))
            a = self.livella(a, rif).astype(np.float32)
            if len(a) / self.sr >= 0.5:
                rif = float(np.sqrt(np.mean(a ** 2)))
            if pezzi:
                p = self.PAUSA.get(parti[k - 1][1].rstrip()[-1:], 0.0)
                if p:
                    pezzi.append(p)
            pezzi.append(np.clip(a, -0.98, 0.98))
        audio = self.unisci(pezzi)
        sentito = " ".join(s.text.strip() for s in self.trascrivi(audio, "it"))
        punteggio = difflib.SequenceMatcher(None, _norm(frase_vis), _norm(sentito)).ratio()
        return audio, punteggio, sentito

    def frase_verificata(self, frase_vis, seme):
        parti = parti_frase(frase_vis)
        migliore = None
        for t in range(cfg.PODCAST_TENTATIVI):
            audio, punti, sentito = self.frase(testo_visibile(frase_vis), parti, seme + 1000 * t)
            if migliore is None or punti > migliore[1]:
                migliore = (audio, punti, sentito)
            if punti >= cfg.PODCAST_SOGLIA_WHISPER:
                break
            log.info(f"    rigenero (punteggio {punti:.2f}): {sentito[:80]}")
        return migliore


# ═══════════════════════════════════════════════════════════════════════════════
# COPERTINA E VIDEO
# ═══════════════════════════════════════════════════════════════════════════════
VIDEO_L, VIDEO_A = 1080, 1350          # 4:5, il formato verticale del feed


def _accorcia(t, n=95):
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(" ,;:(") + "…"


def html_copertina(articoli, wl):
    logo = ""
    for nome in ("logo-medicina-urgenza.png", "logo.jpg"):
        p = os.path.join(cfg.DIR_BASE, nome)
        if os.path.exists(p):
            mime = "image/png" if nome.endswith(".png") else "image/jpeg"
            with open(p, "rb") as f:
                logo = (f'<img src="data:{mime};base64,{base64.b64encode(f.read()).decode()}" '
                        f'style="height:130px;border-radius:6px;"/>')
            break
    righe = "".join(
        f'<div style="display:flex;gap:20px;">'
        f'<div style="font-family:monospace;font-size:32px;color:{cfg.COLOR_ACCENT};'
        f'font-weight:700;min-width:50px;">{str(i).zfill(2)}</div>'
        f'<div><div style="font-family:Georgia,serif;font-size:30px;color:#f3f3f3;'
        f'line-height:1.28;">{nr.esc(_accorcia(a.get("titolo_it") or a["titolo"]))}</div>'
        f'<div style="font-family:monospace;font-size:20px;color:#9a9a9a;margin-top:8px;">'
        f'{nr.esc(a["rivista"])}</div></div></div>'
        for i, a in enumerate(articoli, 1))
    return f"""<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"></head>
<body style="margin:0;width:{VIDEO_L}px;height:{VIDEO_A}px;background:{cfg.COLOR_DARK};
             overflow:hidden;display:flex;flex-direction:column;">
  <div style="background:{cfg.COLOR_ACCENT};height:12px;flex:none;"></div>
  <div style="flex:1;display:flex;flex-direction:column;justify-content:space-between;
              padding:56px 70px 50px;">
    <div>
      {logo}
      <div style="font-family:monospace;font-size:23px;color:#9a9a9a;letter-spacing:5px;
                  text-transform:uppercase;margin-top:34px;">Podcast · Settimana {wl['settimana']}/{wl['anno']}</div>
      <div style="font-family:Georgia,serif;font-size:64px;font-weight:700;color:#ffffff;
                  line-height:1.1;margin:16px 0 12px;">EM Weekly Digest</div>
      <div style="font-family:Georgia,serif;font-size:31px;color:#d8d8d8;">
        {len(articoli)} articoli, 2 minuti, sola medicina d'emergenza-urgenza</div>
    </div>
    <div style="display:flex;flex-direction:column;gap:30px;">{righe}</div>
    <div style="font-family:monospace;font-size:19px;color:#7a7a7a;">{nr.esc(cfg.NOME_SERVIZIO)}</div>
  </div>
</body></html>"""


def genera_copertina(articoli, wl, dest):
    try:
        from playwright.sync_api import sync_playwright
        tmp = tempfile.mkdtemp()
        f = os.path.join(tmp, "copertina.html")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(html_copertina(articoli, wl))
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page(viewport={"width": VIDEO_L, "height": VIDEO_A})
            pg.goto(pathlib.Path(f).as_uri(), wait_until="networkidle", timeout=cfg.IMG_TIMEOUT_MS)
            pg.screenshot(path=dest)
            b.close()
        shutil.rmtree(tmp, ignore_errors=True)
        return dest
    except Exception as e:
        log.warning(f"Copertina non generata ({e}): uso le schede")
        return None


def genera_video(mp3, sezioni, immagini, copertina, dest):
    """sezioni: [(inizio_s, fine_s), ...] = intro, articoli..., chiusura.
    Sull'intro e sulla chiusura la copertina, su ogni articolo la sua scheda."""
    n_art = len(sezioni) - 2
    schede = [immagini[i] if i < len(immagini) and os.path.exists(immagini[i]) else None
              for i in range(n_art)]
    riserva = copertina or next((s for s in schede if s), None)
    if not riserva:
        raise RuntimeError("nessuna immagine per il video")
    sequenza = [copertina or riserva] + [s or riserva for s in schede] + [copertina or riserva]
    lista = os.path.join(cfg.PODCAST_DIR, "diapositive.txt")
    with open(lista, "w", encoding="utf-8") as f:
        for img, (ini, fin) in zip(sequenza, sezioni):
            f.write(f"file '{os.path.abspath(img)}'\nduration {max(0.5, fin - ini):.3f}\n")
        f.write(f"file '{os.path.abspath(sequenza[-1])}'\n")
    vf = (f"scale={VIDEO_L}:{VIDEO_A}:force_original_aspect_ratio=decrease,"
          f"pad={VIDEO_L}:{VIDEO_A}:(ow-iw)/2:(oh-ih)/2:color=white,format=yuv420p")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista,
         "-i", mp3, "-vf", vf, "-r", "25", "-c:v", "libx264", "-preset", "veryfast",
         "-tune", "stillimage", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
         "-shortest", "-movflags", "+faststart", dest],
        check=True)
    return dest


# ═══════════════════════════════════════════════════════════════════════════════
# GENERA
# ═══════════════════════════════════════════════════════════════════════════════
def genera(archivio=None, copione_file=None):
    os.makedirs(cfg.PODCAST_DIR, exist_ok=True)
    esito = {"ok": False, "avvisi": []}
    try:
        percorso = archivio_recente(archivio)
        with open(percorso, encoding="utf-8") as f:
            doc = json.load(f)
        articoli = doc["pubblicati"]
        wl = {"settimana": doc["settimana"], "anno": doc["anno"]}
        log.info(f"=== Podcast — settimana {wl['settimana']}/{wl['anno']} "
                 f"({len(articoli)} articoli, {percorso}) ===")

        testi = scrivi_copione(articoli, copione_file)
        esito["copione"] = testi
        with open(os.path.join(cfg.PODCAST_DIR, "copione.txt"), "w", encoding="utf-8") as f:
            f.write("\n\n".join([intro(len(articoli))] + [testo_visibile(t) for t in testi]
                                + [cfg.PODCAST_CHIUSURA]) + "\n")

        import numpy as np
        import soundfile as sf
        voce = Voce()
        sr = voce.sr
        sezioni_testo = [intro(len(articoli))] + testi + [cfg.PODCAST_CHIUSURA]
        traccia = [np.zeros(int(0.3 * sr), dtype=np.float32)]
        confini, verifica, cursore = [], [], int(0.3 * sr)
        for s, testo in enumerate(sezioni_testo):
            inizio = cursore
            for j, fr in enumerate(dividi_frasi(testo)):
                audio, punti, sentito = voce.frase_verificata(fr, seme=100 * s + j + 1)
                verifica.append({"frase": testo_visibile(fr), "sentito": sentito,
                                 "punteggio": round(punti, 3)})
                log.info(f"  [{s}.{j}] {punti:.2f} {len(audio) / sr:4.1f}s | {testo_visibile(fr)[:70]}")
                if punti < cfg.PODCAST_SOGLIA_WHISPER:
                    esito["avvisi"].append(f"frase sotto soglia ({punti:.2f}): {testo_visibile(fr)[:80]}")
                if j:
                    traccia.append(np.zeros(int(0.2 * sr), dtype=np.float32))
                    cursore += int(0.2 * sr)
                traccia.append(audio)
                cursore += len(audio)
            confini.append((inizio / sr, cursore / sr))
            pausa = int(0.45 * sr)
            traccia.append(np.zeros(pausa, dtype=np.float32))
            cursore += pausa
        traccia.append(np.zeros(int(0.3 * sr), dtype=np.float32))
        grezzo = np.concatenate(traccia)
        wav = os.path.join(cfg.PODCAST_DIR, "podcast.wav")
        sf.write(wav, grezzo, sr)

        durata_grezza = len(grezzo) / sr
        atempo = min(max(durata_grezza / (cfg.PODCAST_DURATA_MAX - 0.5), 1.0), cfg.PODCAST_ATEMPO_MAX)
        mp3 = os.path.join(cfg.PODCAST_DIR, f"podcast-s{str(wl['settimana']).zfill(2)}-{wl['anno']}.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
             "-af", f"atempo={atempo:.4f},loudnorm=I=-16:TP=-1.5:LRA=11",
             "-ar", "44100", "-ac", "1", "-b:a", "128k",
             "-metadata", f"title=EM Weekly Digest — podcast settimana {wl['settimana']}/{wl['anno']}",
             mp3], check=True)
        durata = durata_grezza / atempo
        if durata > cfg.PODCAST_DURATA_MAX + 1:
            esito["avvisi"].append(f"durata {durata:.0f}s oltre il limite anche accelerando")
        log.info(f"Audio: {durata:.1f}s (grezzo {durata_grezza:.1f}s, velocità x{atempo:.2f}) -> {mp3}")

        sezioni = [(a / atempo, b / atempo) for a, b in confini]
        # le diapositive coprono anche le pause: ognuna dura fino all'inizio della successiva
        sezioni = [(sezioni[i][0] if i else 0.0,
                    sezioni[i + 1][0] if i + 1 < len(sezioni) else durata)
                   for i in range(len(sezioni))]
        prefisso = os.path.join(cfg.IMG_DIR, f"digest-s{str(wl['settimana']).zfill(2)}-")
        immagini = [f"{prefisso}{str(i).zfill(2)}.png" for i in range(1, len(articoli) + 1)]
        copertina = genera_copertina(articoli, wl, os.path.join(cfg.PODCAST_DIR, "copertina.png"))
        mp4 = os.path.join(cfg.PODCAST_DIR, f"podcast-s{str(wl['settimana']).zfill(2)}-{wl['anno']}.mp4")
        genera_video(mp3, sezioni, immagini, copertina, mp4)
        log.info(f"Video: {mp4} ({os.path.getsize(mp4) / 1e6:.1f} MB)")

        os.remove(wav)
        esito.update(ok=True, mp3=mp3, mp4=mp4, durata=round(durata, 1),
                     velocita=round(atempo, 3), verifica=verifica,
                     settimana=wl["settimana"], anno=wl["anno"], n_articoli=len(articoli))
    except Exception as e:
        log.error(f"Podcast non generato: {e}")
        esito["errore"] = str(e)
    with open(cfg.PODCAST_ESITO, "w", encoding="utf-8") as f:
        json.dump(esito, f, ensure_ascii=False, indent=2)
    for avviso in esito["avvisi"]:
        log.warning(f"Podcast: {avviso}")
    return esito["ok"]


# ═══════════════════════════════════════════════════════════════════════════════
# PUBBLICA
# ═══════════════════════════════════════════════════════════════════════════════
def _multipart_video(campi, percorso):
    conf = uuid.uuid4().hex
    parti = [f'--{conf}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
             for k, v in campi.items()]
    with open(percorso, "rb") as f:
        dati = f.read()
    parti += [f'--{conf}\r\nContent-Disposition: form-data; name="source"; '
              f'filename="{os.path.basename(percorso)}"\r\nContent-Type: video/mp4\r\n\r\n'.encode("utf-8"),
              dati, b"\r\n", f"--{conf}--\r\n".encode("utf-8")]
    return b"".join(parti), f"multipart/form-data; boundary={conf}"


def carica_video(percorso, descrizione, titolo, quando):
    """Video sulla Pagina, programmato (o subito se l'orario è troppo vicino)."""
    campi = {"description": descrizione, "title": titolo, "access_token": cfg.FB_PAGE_TOKEN}
    if quando:
        campi.update(published="false", scheduled_publish_time=str(int(quando.timestamp())))
    corpo, ctype = _multipart_video(campi, percorso)
    url = f"https://graph-video.facebook.com/{cfg.FB_API_VERSION}/{cfg.FB_PAGE_ID}/videos"
    req = urllib.request.Request(url, data=corpo, headers={"Content-Type": ctype}, method="POST")
    with urllib.request.urlopen(req, timeout=max(cfg.FB_TIMEOUT, 300)) as r:
        return json.loads(r.read().decode("utf-8"))


def pubblica_foto(immagine, messaggio, quando):
    """Il post del primo articolo come prima: foto non pubblicata + post di feed."""
    r = nr._fb_post(f"{cfg.FB_PAGE_ID}/photos", {"published": "false"}, file_path=immagine)
    campi = {"message": messaggio, "attached_media[0]": json.dumps({"media_fbid": r["id"]})}
    if quando:
        campi.update(published="false", scheduled_publish_time=str(int(quando.timestamp())))
    return nr._fb_post(f"{cfg.FB_PAGE_ID}/feed", campi)


def pubblica():
    if not os.path.exists(cfg.PODCAST_PRIMO_POST):
        log.info("Podcast: nessun post del lunedì in sospeso, niente da pubblicare")
        return True
    with open(cfg.PODCAST_PRIMO_POST, encoding="utf-8") as f:
        p = json.load(f)
    esito = {}
    if os.path.exists(cfg.PODCAST_ESITO):
        with open(cfg.PODCAST_ESITO, encoding="utf-8") as f:
            esito = json.load(f)
    video = esito.get("mp4") if esito.get("ok") and esito.get("mp4") and \
        os.path.exists(esito["mp4"]) else None
    wl = {"settimana": p["settimana"], "anno": p["anno"]}
    messaggio_video = cfg.PODCAST_FB_TESTA.format(n=p["totale"]) + p["message"]
    titolo = cfg.PODCAST_FB_TITOLO.format(**wl)

    quando = datetime.fromisoformat(p["quando"])
    adesso = datetime.now(ZoneInfo(cfg.FB_FUSO))
    if quando <= adesso + timedelta(minutes=12):
        log.warning("Orario del post del lunedì troppo vicino o passato: pubblico subito")
        quando = None

    if cfg.DRY_RUN or not cfg.FB_ABILITATO or not (cfg.FB_PAGE_ID and cfg.FB_PAGE_TOKEN):
        with open("post_facebook_podcast.txt", "w", encoding="utf-8") as f:
            f.write(f"{'=' * 70}\nPOST DEL LUNEDÌ — "
                    f"{'VIDEO ' + video if video else 'IMMAGINE ' + str(p.get('immagine'))}\n"
                    f"programmato per {quando.strftime('%A %d/%m alle %H:%M') if quando else 'subito'}\n"
                    f"{'=' * 70}\n\n{messaggio_video if video else p['message']}\n")
        log.info("Podcast: anteprima del post del lunedì in post_facebook_podcast.txt "
                 f"({'video' if video else 'immagine'}; nessuna pubblicazione)")
        return True

    post_id, link = None, None
    if video:
        try:
            r = carica_video(video, messaggio_video, titolo, quando)
            post_id = r.get("id")
            link = f"https://www.facebook.com/{cfg.FB_PAGE_ID}/videos/{post_id}"
            log.info(f"Podcast: video {'programmato' if quando else 'pubblicato'} -> {post_id}")
        except urllib.error.HTTPError as e:
            log.error(f"Podcast: caricamento video HTTP {e.code}: "
                      f"{e.read().decode('utf-8', 'replace')[:400]} — ripiego sull'immagine")
        except Exception as e:
            log.error(f"Podcast: caricamento video fallito ({e}) — ripiego sull'immagine")
    else:
        log.warning(f"Podcast non disponibile ({esito.get('errore', 'nessun esito')}): "
                    "il post del lunedì esce con l'immagine")

    if not post_id:
        if not p.get("immagine") or not os.path.exists(p["immagine"]):
            log.error("Post del lunedì: manca anche l'immagine, nessuna pubblicazione")
            return False
        try:
            r = pubblica_foto(p["immagine"], p["message"], quando)
            post_id = r.get("id")
            link = nr.link_post(post_id)
            log.info(f"Post del lunedì con immagine -> {post_id}")
        except urllib.error.HTTPError as e:
            log.error(f"Post del lunedì HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
            return False
        except Exception as e:
            log.error(f"Post del lunedì fallito: {e}")
            return False

    try:
        nr.scrivi_post_gruppo(post_id, {"titolo": p["titolo"]}, wl, link=link)
    except Exception as e:
        log.warning(f"Messaggio per il gruppo non scritto: {e}")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comando", choices=["genera", "pubblica"])
    ap.add_argument("--archivio", help="archivio JSON del digest (default: il più recente)")
    ap.add_argument("--copione", help="copione già scritto (array JSON): salta Claude")
    args = ap.parse_args()
    if args.comando == "genera":
        ok = genera(args.archivio, args.copione)
    else:
        ok = pubblica()
    # Codice d'uscita 0 anche se il podcast fallisce: nel workflow il passo è comunque
    # non bloccante, e il post del lunedì ha il suo ripiego.
    sys.exit(0 if ok or args.comando == "genera" else 1)


if __name__ == "__main__":
    main()
