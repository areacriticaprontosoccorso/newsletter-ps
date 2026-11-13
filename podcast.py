"""
EM Weekly Digest — podcast settimanale (una voce, circa due minuti).

    python podcast.py genera   [--archivio FILE] [--copione FILE]
    python podcast.py pubblica

genera   Lo lancia newsletter_rss.py (genera_podcast) prima di inviare la mail,
         che parte con il video del podcast allegato. Dall'archivio JSON del digest appena prodotto:
         1. copione: UNA chiamata a Claude (le regole sono in config.PODCAST_PROMPT);
         2. controllo: ogni numero del copione deve comparire nell'abstract o nella
            sintesi dello stesso articolo, la lunghezza deve rientrare nei limiti;
            se no, Claude corregge (massimo 3 tentativi), poi si rinuncia;
         3. voce: Gemini TTS (config.PODCAST_MOTORE = "gemini"), tutto il podcast in
            una chiamata; oppure Chatterbox in locale ("chatterbox"), frase per frase;
         4. verifica: l'audio viene ritrascritto con Whisper e rigenerato se non
            corrisponde al testo;
         5. MP3 e video MP4 (copertina + schede degli articoli che scorrono mentre
            se ne parla). Esito in podcast/esito.json.
         --copione usa un copione già scritto (array JSON come quello di Claude)
         e salta la chiamata API: serve per le prove.

pubblica Il post Facebook del lunedì, rimandato da newsletter_rss.py: video del
         podcast + didascalia del primo articolo, programmato allo stesso orario.
         Se il video non c'è o Facebook lo rifiuta: con config.FB_SCHEDE = False
         (impostazione attuale) non esce nulla; con True esce il post del primo
         articolo con l'immagine. In DRY_RUN scrive
         solo l'anteprima in post_facebook_podcast.txt.

Nessuna delle due fasi blocca la newsletter: se il podcast fallisce o supera
config.PODCAST_TIMEOUT_MAIL, la mail parte senza podcast e su Facebook non esce il video.
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
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config as cfg
try:                                   # EM
    import newsletter_rss as nr
except ImportError:                    # NIV: stesso file, altro modulo della newsletter
    import newsletter_NIV as nr
import corsi as avvisi

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
            f"badge: {(cfg.TIPI_ARTICOLO.get(a.get('tipo') or '') or {}).get('label', 'n/d')}\n"
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


def intro(n, apertura=None):
    """Saluto fisso; la frase d'apertura di Claude va prima di "Buon ascolto"."""
    t = cfg.PODCAST_INTRO.replace("{Articoli}", _num_it(n).capitalize())
    if not apertura:
        return t
    apertura = apertura.strip()
    if not apertura.endswith((".", "!", "?")):
        apertura += "."
    m = re.search(r"\s*Buon ascolto[.!]?\s*$", t)
    return (t[:m.start()] + " " + apertura + " " + t[m.start():].strip()) if m else t + " " + apertura


def problemi_copione(articoli, testi, apertura=None, con_apertura=True):
    problemi = []
    if con_apertura:
        if not apertura:
            problemi.append('manca l\'APERTURA (oggetto con "pmid": "apertura")')
        else:
            if conta_parole(apertura) > cfg.PODCAST_APERTURA_MAX_PAROLE + 3:
                problemi.append(f"apertura di {conta_parole(apertura)} parole, massimo "
                                f"{cfg.PODCAST_APERTURA_MAX_PAROLE}")
            if re.search(r"\d", testo_visibile(apertura)):
                problemi.append("l'apertura non deve contenere numeri")
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
                            f"tieni un solo risultato e il peso dell'evidenza")
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
    """(testi degli articoli, frase d'apertura o None)."""
    voci = [v for v in nr._estrai_json_array(risposta) if isinstance(v, dict)]
    apertura = next(((v.get("testo") or "").strip() for v in voci
                     if str(v.get("pmid", "")).strip().lower() == "apertura"), None)
    voci = [v for v in voci if str(v.get("pmid", "")).strip().lower() != "apertura"]
    per_pmid = {str(v.get("pmid", "")).strip(): (v.get("testo") or "").strip() for v in voci}
    testi = [per_pmid.get(str(a["pmid"]), "") for a in articoli]
    if not all(testi) and len(voci) == len(articoli):
        # pmid storpiati ma numero giusto di paragrafi: vale l'ordine
        testi = [(v.get("testo") or "").strip() for v in voci]
    return testi, apertura


def scrivi_copione(articoli, copione_file=None):
    n = len(articoli)
    if copione_file:
        with open(copione_file, encoding="utf-8") as f:
            testi, apertura = _testi_da_risposta(f.read(), articoli)
        # copioni di prova del vecchio formato (senza apertura): si accettano
        problemi = problemi_copione(articoli, testi, apertura, con_apertura=apertura is not None)
        if problemi:
            raise RuntimeError("Copione di prova non valido: " + "; ".join(problemi))
        return testi, apertura

    p_min, p_max = limiti_parole(n)
    prompt = cfg.PODCAST_PROMPT.format(
        n=n, min_parole=p_min, max_parole=p_max,
        per_min=round(p_min / n), per_max=round(p_max / n),
        max_apertura=cfg.PODCAST_APERTURA_MAX_PAROLE,
        blocchi="\n".join(_blocco_articolo(i, a) for i, a in enumerate(articoli, 1)),
    )
    aggiunta, problemi = "", []
    for tentativo in range(1, 4):
        risposta = nr.chiama_claude(prompt + aggiunta, max_tokens=3000)
        try:
            testi, apertura = _testi_da_risposta(risposta, articoli)
            problemi = problemi_copione(articoli, testi, apertura)
        except ValueError as e:
            testi, apertura, problemi = [], None, [f"risposta non in formato JSON: {e}"]
        if not problemi:
            log.info(f"Copione pronto al tentativo {tentativo}: "
                     f"{sum(conta_parole(t) for t in testi)} parole")
            return testi, apertura
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
                    "il podcast sarà più lungo del previsto")
        return testi, apertura
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


def silenzia_rumori(a, sr, soglia_piatto=0.2, min_s=0.06, margine_s=0.08):
    """Toglie i lampi di rumore (soffi a banda larga, forti come la voce, che Gemini a volte mette fra
    un blocco e l'altro): tratti di almeno `min_s` secondi con spettro piatto come un rumore bianco
    (piattezza > soglia_piatto fra 100 Hz e 12 kHz) e volume vicino a quello della voce. La voce,
    anche le «s», ha spettro meno piatto o e' piu' debole. Il tratto (piu' un margine) diventa
    silenzio, con brevi dissolvenze; la durata non cambia. Restituisce (audio, [(inizio, durata)])."""
    import numpy as np
    w = int(0.02 * sr)
    n = len(a) // w
    if n < 10:
        return a, []
    X = a[:n * w].reshape(n, w).astype(np.float64)
    S = np.abs(np.fft.rfft(X * np.hanning(w), axis=1)) ** 2 + 1e-12
    f = np.fft.rfftfreq(w, 1 / sr)
    b = (f > 100) & (f < 12000)
    piatto = np.exp(np.log(S[:, b]).mean(1)) / S[:, b].mean(1)
    rms = 20 * np.log10(np.sqrt((X ** 2).mean(1)) + 1e-9)
    voce = rms[rms > -40]
    if not len(voce):
        return a, []
    sospetti = (piatto > soglia_piatto) & (rms > np.median(voce) - 6)
    out, trovati, i = a.copy(), [], 0
    m = int(margine_s / 0.02)
    while i < n:
        if not sospetti[i]:
            i += 1
            continue
        j = i
        while j < n and sospetti[j]:
            j += 1
        if (j - i) * 0.02 >= min_s:
            i0, i1 = max(0, (i - m) * w), min(len(a), (j + m) * w)
            sf_ = min(int(0.01 * sr), (i1 - i0) // 4)
            out[i0 + sf_:i1 - sf_] = 0
            out[i0:i0 + sf_] *= np.linspace(1, 0, sf_, dtype=out.dtype)
            out[i1 - sf_:i1] *= np.linspace(0, 1, sf_, dtype=out.dtype)
            trovati.append((round(i0 / sr, 2), round((i1 - i0) / sr, 2)))
        i = j
    # Schiocchi isolati: al massimo 40 ms di suono forte fra due silenzi (le giunture dei blocchi).
    e = 20 * np.log10(np.sqrt((out[:n * w].reshape(n, w).astype(np.float64) ** 2).mean(1)) + 1e-9)
    k = 3
    while k < n - 3:
        for lung in (1, 2):
            if k + lung + 3 <= n and e[k:k + lung].max() > -35 and e[k - 3:k].max() < -50 \
                    and e[k + lung:k + lung + 3].max() < -50:
                out[k * w:(k + lung) * w] = 0
                trovati.append((round(k * w / sr, 2), round(lung * w / sr, 2)))
                k += lung
                break
        k += 1
    return out, sorted(trovati)


def regia_letta(sentito, atteso, note=None):
    """True se nel parlato ci sono pezzi delle note di regia (PODCAST_GEMINI_STILE) che il copione non
    ha: Gemini a volte legge ad alta voce anche le istruzioni. Conta le sequenze di 3 parole delle
    note sentite e assenti dal copione; ne bastano 2."""
    note = _norm(note if note is not None else getattr(cfg, "PODCAST_GEMINI_STILE", "")).split()
    if len(note) < 3:
        return False
    sentito, atteso = f" {_norm(sentito)} ", f" {_norm(atteso)} "
    trovate = {" ".join(note[i:i + 3]) for i in range(len(note) - 2)
               if f" {' '.join(note[i:i + 3])} " in sentito and f" {' '.join(note[i:i + 3])} " not in atteso}
    return len(trovate) >= 2


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
        self.tts.prepare_conditionals(cfg.PODCAST_VOCE_RIF, exaggeration=cfg.PODCAST_ESAGERAZIONE)
        self.sr = self.tts.sr
        self.stt = WhisperModel(cfg.PODCAST_WHISPER_MODELLO, device="cpu", compute_type="int8")
        self.meter = pyln.Meter(self.sr)

    # ── primitive ──
    def sintetizza(self, testo, lingua, seme):
        self.torch.manual_seed(seme)
        with self.torch.inference_mode():
            w = self.tts.generate(testo, language_id=lingua,
                                  exaggeration=cfg.PODCAST_ESAGERAZIONE,
                                  cfg_weight=cfg.PODCAST_CFG, temperature=0.7)
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


class ModelloNonDisponibile(Exception):
    """Il modello Gemini non esiste o ha finito la quota del giorno: si passa al successivo."""


class VoceGemini:
    """Voce Gemini TTS (API di Google, voci italiane native).

    Il podcast si genera in pochi blocchi di circa 40 secondi (sezioni intere: intro,
    articoli, chiusura): nelle letture lunghe il modello cambia timbro verso la fine
    (prova del 2/11: voce diversa dopo ~80 s). Tutti i blocchi usano lo stesso modello;
    se un modello cade a metà, si ricomincia col successivo. Ogni blocco viene
    ritrascritto con Whisper: si rigenera se non corrisponde al testo e si taglia dopo
    l'ultima parola (a volte il modello aggiunge rumore in coda)."""
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{modello}:generateContent"

    def __init__(self):
        import numpy as np
        import librosa
        from faster_whisper import WhisperModel
        if not cfg.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY mancante: impossibile usare Gemini TTS")
        self.np, self.librosa = np, librosa
        self.sr = 24000
        self.modello_usato = None
        log.info(f"Voce: Gemini TTS ({cfg.PODCAST_GEMINI_VOCE}); verifica con Whisper...")
        self.stt = WhisperModel(cfg.PODCAST_WHISPER_MODELLO, device="cpu", compute_type="int8")

    def _chiama(self, testo, modello):
        np = self.np
        corpo = json.dumps({
            "contents": [{"parts": [{"text": testo}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig":
                                                 {"voiceName": cfg.PODCAST_GEMINI_VOCE}}},
            },
        }).encode("utf-8")
        ultimo = None
        for tentativo in range(4):
            req = urllib.request.Request(
                self.URL.format(modello=modello), data=corpo, method="POST",
                headers={"Content-Type": "application/json", "x-goog-api-key": cfg.GEMINI_API_KEY})
            try:
                with urllib.request.urlopen(req, timeout=240) as r:
                    dati = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                intero = e.read().decode("utf-8", "replace")
                ultimo = f"Gemini TTS HTTP {e.code} ({modello}): {' '.join(intero.split())[:300]}"
                if (e.code == 429 and "PerDay" in intero) or e.code == 404 or \
                        (e.code == 400 and "model" in intero.lower()):
                    raise ModelloNonDisponibile(ultimo)
                if e.code == 429 or e.code >= 500:
                    attesa = 10 * 2 ** tentativo
                    log.warning(f"    {ultimo[:160]} — riprovo tra {attesa}s")
                    time.sleep(attesa)
                    continue
                raise RuntimeError(ultimo)
            except urllib.error.URLError as e:
                ultimo = f"Gemini TTS rete: {e}"
                time.sleep(5 * 2 ** tentativo)
                continue
            parti = (dati.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            inline = next((x["inlineData"] for x in parti if "inlineData" in x), None)
            if not inline:      # es. finishReason SAFETY su testo innocuo: si riprova
                ultimo = f"Gemini TTS: risposta senza audio ({str(dati)[:200]})"
                log.warning(f"    {ultimo[:160]}")
                continue
            m = re.search(r"rate=(\d+)", inline.get("mimeType", ""))
            rate = int(m.group(1)) if m else 24000
            a = np.frombuffer(base64.b64decode(inline["data"]), dtype="<i2").astype(np.float32) / 32768.0
            if rate != self.sr:
                a = self.librosa.resample(a, orig_sr=rate, target_sr=self.sr)
            return a
        raise RuntimeError(ultimo or "Gemini TTS: troppi tentativi")

    def trascrivi_parole(self, a):
        a16 = self.librosa.resample(a, orig_sr=self.sr, target_sr=16000)
        segs, _ = self.stt.transcribe(a16, language="it", beam_size=5, word_timestamps=True)
        return [(w.word, w.start, w.end) for s in segs for w in (s.words or [])]

    def rifila(self, a):
        iv = self.librosa.effects.split(a, top_db=35, frame_length=512, hop_length=128)
        if len(iv) == 0:
            return a
        pad = int(0.03 * self.sr)
        return a[max(0, iv[0][0] - pad): min(len(a), iv[-1][1] + pad)]

    def taglia_dopo_ultima_parola(self, a, parole):
        """Toglie ciò che segue l'ultima parola riconosciuta (rumore, sillabe spurie),
        con una breve dissolvenza."""
        if not parole:
            return a, parole
        fine = int(min(len(a), (parole[-1][2] + 0.30) * self.sr))
        if fine >= len(a) - int(0.05 * self.sr):
            return a, parole
        b = a[:fine].copy()
        n = min(len(b), int(0.06 * self.sr))
        b[-n:] *= self.np.linspace(1, 0, n, dtype=self.np.float32)
        return b, parole

    def taglia_prima_del_testo(self, a, parole, atteso, max_parole=60):
        """Toglie cio' che Gemini dice PRIMA del testo (istruzioni lette ad alta voce): cerca nelle
        parole riconosciute l'inizio del testo atteso (le sue prime 3 parole, o almeno 2 su 3) e
        taglia l'audio poco prima, con una breve dissolvenza in entrata. Restituisce (audio, parole,
        secondi tolti)."""
        attese = _norm(atteso).split()[:3]
        toks = [_norm(w).strip() for w, _, _ in parole]
        if len(attese) < 3 or not parole:
            return a, parole, 0.0
        for k in range(1, min(len(toks) - 2, max_parole)):
            if sum(x == y for x, y in zip(toks[k:k + 3], attese)) >= 2 and toks[k] == attese[0]:
                taglio = max(0.0, parole[k][1] - 0.08)
                i = int(taglio * self.sr)
                b = a[i:].copy()
                n = min(len(b), int(0.03 * self.sr))
                b[:n] *= self.np.linspace(0, 1, n, dtype=self.np.float32)
                return b, [(w, s0 - taglio, s1 - taglio) for w, s0, s1 in parole[k:]], taglio
        return a, parole, 0.0

    @staticmethod
    def _allinea(parole, sezioni):
        """Istante d'inizio di ogni sezione nell'audio, dalle parole di Whisper:
        si cercano in avanti le prime parole di ciascuna sezione."""
        toks = [_norm(w) for w, _, _ in parole]
        inizi, pos = [0.0], 0
        for sez in sezioni[1:]:
            attese = " ".join(_norm(sez).split()[:4])
            n = max(1, len(attese.split()))
            trovato, migliore = None, (0.0, None)
            for i in range(pos, len(toks)):
                r = difflib.SequenceMatcher(None, attese, " ".join(toks[i:i + n])).ratio()
                if r > migliore[0]:
                    migliore = (r, i)
                if r >= 0.8:
                    trovato = i
                    break
            if trovato is None and migliore[0] >= 0.6:
                trovato = migliore[1]
            if trovato is None:
                inizi.append(None)
            else:
                pos = trovato + 1
                inizi.append(parole[trovato][1])
        return inizi

    def uniforma_timbro(self, blocchi):
        """Fra una chiamata e l'altra la voce predefinita esce con un timbro un po'
        diverso (più chiara, più scura). Si porta lo spettro medio di ogni blocco su
        quello del primo, entro ±PODCAST_GEMINI_EQ_MAX_DB, e se ne pareggia il volume."""
        np, lr = self.np, self.librosa
        n_fft, hop = 1024, 256

        def ltas(a):
            S = np.abs(lr.stft(a, n_fft=n_fft, hop_length=hop)) ** 2
            e = S.sum(0)
            voce = S[:, e > np.percentile(e, 40)]
            m = voce.mean(1) + 1e-10
            k = 15                                   # ~350 Hz: si segue l'inviluppo, non le armoniche
            return np.convolve(m, np.ones(k) / k, mode="same"), float(np.sqrt(voce.sum(0).mean()))

        rif, rms_rif = ltas(blocchi[0])
        f = lr.fft_frequencies(sr=self.sr, n_fft=n_fft)
        banda = (f > 80) & (f < 10000)
        out = [blocchi[0]]
        for b in blocchi[1:]:
            m, rms = ltas(b)
            g = np.clip(10 * np.log10(rif / m), -cfg.PODCAST_GEMINI_EQ_MAX_DB, cfg.PODCAST_GEMINI_EQ_MAX_DB)
            g = np.where(banda, g, 0.0)
            S = lr.stft(b, n_fft=n_fft, hop_length=hop)
            c = lr.istft(S * (10 ** (g / 20))[:, None], hop_length=hop, length=len(b))
            m2, rms2 = ltas(c.astype(np.float32))
            out.append((c * (rms_rif / max(rms2, 1e-9))).astype(np.float32))
        return out

    @staticmethod
    def blocchi(vis):
        """Indici delle sezioni raggruppati in blocchi di circa PODCAST_GEMINI_PAROLE_BLOCCO parole."""
        gruppi, cur, n = [], [], 0
        for i, v in enumerate(vis):
            w = len(v.split())
            if cur and n + w > cfg.PODCAST_GEMINI_PAROLE_BLOCCO:
                gruppi.append(cur)
                cur, n = [], 0
            cur.append(i)
            n += w
        if cur:
            gruppi.append(cur)
        # una chiusura breve da sola suonerebbe staccata: va col blocco precedente
        if len(gruppi) > 1 and sum(len(vis[i].split()) for i in gruppi[-1]) < 8:
            gruppi[-2].extend(gruppi.pop())
        return gruppi

    def _blocco(self, sez_vis, modello):
        """Un blocco: chiamata, taglio, verifica (fino a PODCAST_TENTATIVI volte)."""
        trascrizione = "\n\n".join(
            numeri_in_parole(re.sub(r"(?i)emergenza-urgenza", "emergenza urgenza", v)) for v in sez_vis)
        completo = (f"### NOTE DI REGIA (da NON leggere)\n{cfg.PODCAST_GEMINI_STILE}\n\n"
                    f"#### TRASCRIZIONE (leggi ad alta voce SOLO questo testo)\n{trascrizione}")
        # Se Gemini ha letto le note di regia, i tentativi successivi mandano solo il testo, senza
        # alcuna istruzione (anche un'istruzione breve finisce a volte letta ad alta voce).
        breve = trascrizione
        prompt = completo
        migliore = None
        for t in range(cfg.PODCAST_TENTATIVI):
            a = self.rifila(self._chiama(prompt, modello)).astype(self.np.float32)
            parole = self.trascrivi_parole(a)
            a, parole = self.taglia_dopo_ultima_parola(a, parole)
            a, parole, tolti = self.taglia_prima_del_testo(a, parole, sez_vis[0])
            if tolti:
                log.warning(f"    blocco ({len(sez_vis)} sezioni), tentativo {t + 1}: tolti {tolti:.1f}s "
                            "detti prima del testo (istruzioni lette?)")
            sentito = "".join(w for w, _, _ in parole)   # le parole hanno già lo spazio iniziale
            punti = difflib.SequenceMatcher(None, _norm(" ".join(sez_vis)), _norm(sentito)).ratio()
            if regia_letta(sentito, " ".join(sez_vis)):
                log.warning(f"    blocco ({len(sez_vis)} sezioni), tentativo {t + 1}: Gemini ha letto "
                            "le note di regia - riprovo senza note")
                punti, prompt = 0.0, breve
            log.info(f"    blocco ({len(sez_vis)} sezioni), tentativo {t + 1}: "
                     f"{len(a) / self.sr:.1f}s, punteggio {punti:.2f}")
            if migliore is None or punti > migliore[1]:
                migliore = (a, punti, parole)
            if punti >= cfg.PODCAST_SOGLIA_WHISPER:
                break
        return migliore

    def podcast_verificato(self, sezioni):
        """Restituisce audio, istanti (inizio, fine) di ogni sezione, verifica per
        sezione e punteggio complessivo."""
        np = self.np
        vis = [testo_visibile(s) for s in sezioni]
        gruppi = self.blocchi(vis)
        ultimo = None
        for modello in cfg.PODCAST_GEMINI_MODELLI:
            try:
                log.info(f"  Gemini {modello}: {len(gruppi)} blocchi")
                risultati = [self._blocco([vis[i] for i in g], modello) for g in gruppi]
                self.modello_usato = modello
                break
            except ModelloNonDisponibile as e:
                ultimo = str(e)
                log.warning(f"    {ultimo[:200]} — provo il modello successivo")
        else:
            raise RuntimeError(ultimo or "Gemini TTS: nessun modello disponibile")

        if cfg.PODCAST_GEMINI_UNIFORMA and len(risultati) > 1:
            uniformati = self.uniforma_timbro([r[0] for r in risultati])
            risultati = [(u, r[1], r[2]) for u, r in zip(uniformati, risultati)]
        pausa = np.zeros(int(0.45 * self.sr), dtype=np.float32)
        pezzi, parole_tot, inizi, cursore = [], [], [], 0.0
        for g, (a, _, parole) in zip(gruppi, risultati):
            if pezzi:
                pezzi.append(pausa)
                cursore += len(pausa) / self.sr
            locali = self._allinea(parole, [vis[i] for i in g])
            durata_b = len(a) / self.sr
            lung = [len(vis[i]) for i in g]
            for k, x in enumerate(locali):      # sezioni non trovate: stima proporzionale
                if x is None:
                    locali[k] = locali[k - 1] + durata_b * lung[k - 1] / sum(lung)
            inizi += [cursore + x for x in locali]
            parole_tot += [(w, cursore + s0, cursore + s1) for w, s0, s1 in parole]
            pezzi.append(a)
            cursore += durata_b
        audio = np.concatenate(pezzi)
        durata = len(audio) / self.sr
        confini = [(max(0.0, inizi[k] - (0.15 if k else 0)),
                    inizi[k + 1] - 0.15 if k + 1 < len(inizi) else durata)
                   for k in range(len(inizi))]
        verifica = []
        for k, (i0, i1) in enumerate(confini):
            sent = "".join(w for w, s0, _ in parole_tot if i0 <= s0 < i1)
            pk = difflib.SequenceMatcher(None, _norm(vis[k]), _norm(sent)).ratio()
            verifica.append({"frase": vis[k], "sentito": sent.strip(), "punteggio": round(pk, 3)})
        sentito = "".join(w for w, _, _ in parole_tot)
        # Punteggio complessivo: media dei punteggi dei blocchi, pesata sulla lunghezza del testo.
        # Il confronto a caratteri sull'intero copione non e' affidabile (difflib su testi lunghi:
        # il 13/11 dava 0,27 con blocchi da 0,85-0,97).
        pesi = [sum(len(vis[i]) for i in g) for g in gruppi]
        punti = sum(r[1] * w for r, w in zip(risultati, pesi)) / max(1, sum(pesi))
        peggiore = min(r[1] for r in risultati)
        if peggiore < cfg.PODCAST_SOGLIA_MINIMA:       # un blocco cattivo non si nasconde nella media
            raise RuntimeError(f"un blocco della voce e' troppo diverso dal copione (punteggio "
                               f"{peggiore:.2f}, minimo {cfg.PODCAST_SOGLIA_MINIMA})")
        # Una voce che dice altro dal copione non si pubblica: meglio niente podcast (o, in EM, la
        # voce di riserva) che istruzioni lette ad alta voce o pezzi mancanti.
        if regia_letta(sentito, " ".join(vis)):
            raise RuntimeError("Gemini ha letto le note di regia in tutti i tentativi")
        if punti < cfg.PODCAST_SOGLIA_MINIMA:
            raise RuntimeError(f"voce troppo diversa dal copione (punteggio {punti:.2f}, "
                               f"minimo {cfg.PODCAST_SOGLIA_MINIMA})")
        return audio, confini, verifica, punti, parole_tot


# ═══════════════════════════════════════════════════════════════════════════════
# SIGLA E SOTTOTITOLI
# ═══════════════════════════════════════════════════════════════════════════════
def sigla(sr, finale=False):
    """Sigla breve sintetizzata qui (nessun brano esterno, nessun diritto): arpeggio di
    La maggiore con timbro di campana su un accordo morbido. Apertura ~3 s, chiusura ~2 s.
    Il volume si regola dopo, rispetto alla voce."""
    import numpy as np
    durata = 2.0 if finale else 3.2
    n = int(durata * sr)
    t = np.arange(n) / sr
    out = np.zeros(n)
    note = [(0.0, 659.25), (0.2, 880.0)] if finale else \
        [(0.0, 440.0), (0.15, 554.37), (0.30, 659.25), (0.45, 880.0)]
    for t0, f in note:
        i = int(t0 * sr)
        tt = t[: n - i]
        inv = np.minimum(tt / 0.004, 1) * np.exp(-tt * 3.0)
        out[i:] += inv * sum(a * np.sin(2 * np.pi * f * k * tt)
                             for k, a in ((1, 1.0), (2, 0.3), (3, 0.1), (4.2, 0.04)))
    inizio = 0.25 if finale else 0.45
    inv = np.clip((t - inizio) / 0.5, 0, 1) * np.exp(-np.clip(t - inizio, 0, None) * 1.1)
    for f, a in ((110.0, 0.5), (220.0, 0.35), (277.18, 0.3), (329.63, 0.3)):
        out += a * inv * np.sin(2 * np.pi * f * t)
    k = int(0.5 * sr)
    out[-k:] *= np.linspace(1, 0, k)
    return (out / np.max(np.abs(out))).astype(np.float32)


def tempi_parole_copione(sezioni_vis, parole):
    """Ogni parola del copione (testo esatto, con cifre e termini inglesi) con i tempi
    della parola corrispondente sentita da Whisper; le parole non ritrovate prendono
    tempi interpolati fra le vicine."""
    copione = [w for s in sezioni_vis for w in s.split()]
    s_tok, s_map, w_tok, w_map = [], [], [], []
    for i, w in enumerate(copione):
        for t in _norm(w).split():
            s_tok.append(t)
            s_map.append(i)
    for j, (w, _, _) in enumerate(parole):
        for t in _norm(w).split():
            w_tok.append(t)
            w_map.append(j)
    tempi = [None] * len(copione)
    for a, b, size in difflib.SequenceMatcher(None, s_tok, w_tok, autojunk=False).get_matching_blocks():
        for k in range(size):
            i, j = s_map[a + k], w_map[b + k]
            t0, t1 = parole[j][1], parole[j][2]
            tempi[i] = [t0, t1] if tempi[i] is None else [tempi[i][0], max(tempi[i][1], t1)]
    noti = [i for i, x in enumerate(tempi) if x]
    if not noti:
        return []
    i = 0
    while i < len(tempi):
        if tempi[i] is not None:
            i += 1
            continue
        j = i
        while j < len(tempi) and tempi[j] is None:
            j += 1
        prima = tempi[i - 1][1] if i else tempi[noti[0]][0]
        dopo = tempi[j][0] if j < len(tempi) else tempi[noti[-1]][1] + 0.4 * (j - i)
        passo = max(0.05, (dopo - prima) / (j - i))
        for k in range(i, j):
            tempi[k] = [prima + passo * (k - i), prima + passo * (k - i + 1)]
        i = j
    return [(w, t[0], t[1]) for w, t in zip(copione, tempi)]


def _ass_tempo(s):
    s = max(0.0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def scrivi_sottotitoli(tempi, atempo, dest, max_car=34):
    """Sottotitoli ASS (testo bianco su riquadro scuro, in basso) da (parola, inizio, fine)."""
    righe, cur = [], []
    for w, t0, t1 in tempi:
        if cur and (len(" ".join(x[0] for x in cur) + " " + w) > max_car or t0 - cur[-1][2] > 0.7):
            righe.append(cur)
            cur = []
        cur.append((w, t0, t1))
        if w.endswith((".", "!", "?", ":")):
            righe.append(cur)
            cur = []
    if cur:
        righe.append(cur)
    eventi = []
    for k, r in enumerate(righe):
        ini = r[0][1] / atempo
        fin = max(r[-1][2] / atempo, ini + 0.8)
        if k + 1 < len(righe):
            prossimo = righe[k + 1][0][1] / atempo
            fin = prossimo if prossimo - fin < 0.35 else fin
        testo = " ".join(x[0] for x in r).replace("{", "(").replace("}", ")")
        eventi.append(f"Dialogue: 0,{_ass_tempo(ini)},{_ass_tempo(fin)},Base,,0,0,0,,{testo}")
    intest = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: {l}\nPlayResY: {a}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Base,DejaVu Sans,46,&H00FFFFFF,&H00FFFFFF,&H30000000,&H30000000,1,0,0,0,"
        "100,100,0,0,3,14,0,2,60,60,24,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ).format(l=VIDEO_L, a=VIDEO_A)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(intest + "\n".join(eventi) + "\n")
    return dest


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
    for nome in ("logo-medicina-urgenza.png", "logo.jpg", "logo.png"):
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
                  line-height:1.1;margin:16px 0 12px;">{nr.esc(getattr(cfg, "PODCAST_TITOLO", "EM Weekly Digest"))}</div>
      <div style="font-family:Georgia,serif;font-size:31px;color:#d8d8d8;">
        {len(articoli)} articoli, 2 minuti, {nr.esc(getattr(cfg, "PODCAST_TEMA", "sola medicina d'emergenza-urgenza"))}</div>
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


def loghi_video():
    """File con i tre loghi (diapositiva finale del video), o None se non esiste."""
    f = getattr(cfg, "PODCAST_LOGHI", "")
    if f and not os.path.isabs(f):
        f = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
    return f if f and os.path.exists(f) else None


def locandine_video(oggi=None):
    """File da mettere in coda al video: le locandine dei corsi in corso di pubblicazione
    (POCUS e NIV, a prescindere dal turno settimanale della mail) e, sempre per ultima, la
    diapositiva con i tre loghi. Mai eccezioni: in caso di problemi restituisce quello
    che e' riuscito a caricare."""
    files = []
    try:
        for c in avvisi.carica_corsi(getattr(cfg, "PODCAST_CORSI", "em"), oggi=oggi, base_url=cfg.URL_RAW_REPO,
                                     ignora_frequenza=True, massimo=8):
            f = c.get("immagine_file")
            if f and os.path.exists(f) and f not in files:
                files.append(f)
    except Exception as e:
        log.warning(f"Locandine per il video non caricate: {e}")
    loghi = loghi_video()
    if loghi:
        files.append(loghi)
    return files


def chiusura_podcast(con_corsi):
    """(testo, n. parole dell'invito ai corsi, n. parole dei saluti). Con corsi in programma la chiusura
    e': avvertenza, invito ai corsi, saluti; senza, avvertenza e saluti."""
    avviso, saluti = cfg.PODCAST_CHIUSURA_AVVISO, cfg.PODCAST_CHIUSURA_SALUTI
    invito = cfg.PODCAST_CHIUSURA_CORSI if con_corsi else ""
    parole = lambda t: len(testo_visibile(t).split())
    return " ".join(x for x in (avviso, invito, saluti) if x), parole(invito), parole(saluti)


def inizio_invito_corsi(tempi_parole, n_invito, n_saluti, ini_chiusura, fin_chiusura, testo_chiusura):
    """Quando comincia l'invito ai corsi, sul tempo dell'audio grezzo: l'inizio della sua prima
    parola (le ultime parole dei tempi sono quelle della chiusura); senza tempi, una stima in
    proporzione ai caratteri. None se la chiusura non ha l'invito."""
    if n_invito <= 0:
        return None
    if tempi_parole and len(tempi_parole) >= n_invito + n_saluti:
        return tempi_parole[len(tempi_parole) - n_saluti - n_invito][1]
    vis = testo_visibile(testo_chiusura)
    pos = vis.find(testo_visibile(cfg.PODCAST_CHIUSURA_CORSI))
    return ini_chiusura + (fin_chiusura - ini_chiusura) * max(0, pos) / max(1, len(vis))


def html_locandina_corsi(png_b64):
    """Diapositiva 4:5 per una locandina dei corsi: poche parole in testa, la locandina sotto e una
    fascia scura libera in fondo, dove stanno i sottotitoli."""
    return f"""<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8"></head>
<body style="margin:0;width:{VIDEO_L}px;height:{VIDEO_A}px;background:{cfg.COLOR_DARK};overflow:hidden;
             display:flex;flex-direction:column;align-items:center;">
  <div style="background:{cfg.COLOR_ACCENT};height:12px;width:100%;flex:none;"></div>
  <div style="height:176px;flex:none;display:flex;flex-direction:column;justify-content:center;
              align-items:center;gap:8px;">
    <div style="font-family:Georgia,serif;font-size:37px;font-weight:700;color:#ffffff;
                text-align:center;line-height:1.15;padding:0 40px;">{nr.esc(cfg.PODCAST_CHIUSURA_CORSI)}</div>
    <div style="font-family:monospace;font-size:24px;color:#d8d8d8;letter-spacing:1px;">
      Date, sede e iscrizioni nella newsletter</div>
  </div>
  <img src="data:image/png;base64,{png_b64}" style="height:1050px;flex:none;border-radius:4px;"/>
</body></html>"""


def genera_locandine_corsi(file_png, dest_dir):
    """Per ogni locandina (PNG) la diapositiva con la scritta 'Corsi in programma'. Chi non si
    riesce a comporre resta com'e': ritorna sempre una lista di file, uno per locandina."""
    risultato = list(file_png)
    if not file_png:
        return risultato
    try:
        from playwright.sync_api import sync_playwright
        tmp = tempfile.mkdtemp()
        with sync_playwright() as p:
            b = p.chromium.launch(args=["--no-sandbox"])
            pg = b.new_page(viewport={"width": VIDEO_L, "height": VIDEO_A})
            for k, f in enumerate(file_png):
                try:
                    with open(f, "rb") as fh:
                        html = html_locandina_corsi(base64.b64encode(fh.read()).decode())
                    h = os.path.join(tmp, f"corsi-{k}.html")
                    with open(h, "w", encoding="utf-8") as fh:
                        fh.write(html)
                    pg.goto(pathlib.Path(h).as_uri(), wait_until="networkidle", timeout=cfg.IMG_TIMEOUT_MS)
                    dest = os.path.join(dest_dir, f"locandina-corsi-{k + 1}.png")
                    pg.screenshot(path=dest)
                    risultato[k] = dest
                except Exception as e:
                    log.warning(f"Locandina {os.path.basename(f)}: scritta non aggiunta ({e})")
            b.close()
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        log.warning(f"Locandine dei corsi senza scritta ({e})")
    return risultato


def piano_coda(durata_chiusura, n_locandine, sec_loghi, minimo=3.5):
    """Coda del video: la voce di chiusura (e i saluti) corre sulle locandine, che si dividono il suo
    tempo, poi i loghi. Ritorna (secondi per locandina, secondi di silenzio da aggiungere all'audio).
    Una locandina sta almeno `minimo` secondi: se la voce e' piu' corta, l'audio si allunga con una pausa.
    Senza locandine la voce resta sulla copertina e l'audio si allunga solo dei loghi."""
    if n_locandine <= 0:
        return 0.0, float(sec_loghi)
    per = max(minimo, durata_chiusura / n_locandine)
    return per, max(0.0, per * n_locandine - durata_chiusura) + sec_loghi


def genera_video(mp3, sezioni, immagini, copertina, dest, sottotitoli=None, locandine=None, loghi=None,
                 inizio_corsi=None):
    """sezioni: [(inizio_s, fine_s), ...] = intro, articoli..., chiusura.
    Sull'intro e sulla chiusura la copertina, su ogni articolo la sua scheda.
    `locandine`: immagini in coda (le locandine dei corsi e, ultima, i loghi). Le locandine compaiono
    quando la voce comincia l'invito ai corsi (`inizio_corsi`, secondi dell'MP3; senza, all'inizio della
    chiusura) e restano fino alla fine della voce e dei saluti, dividendosi quel tempo; poi i loghi,
    PODCAST_LOCANDINA_SEC secondi in silenzio (l'audio si allunga con una pausa; l'MP3 resta com'e').
    `loghi`: il file dei loghi (predefinito: loghi_video())."""
    sec = getattr(cfg, "PODCAST_LOCANDINA_SEC", 5)
    loghi = loghi if loghi is not None else loghi_video()
    # Il montaggio per concatenazione vuole immagini dello stesso formato delle schede
    # (PNG): le locandine, di solito JPG, si convertono prima. Una che non si converte
    # si salta, il video esce con le altre.
    corsi_png, loghi_png = [], None
    for k, f in enumerate(locandine or []):
        if not os.path.exists(f):
            continue
        dest_png = os.path.join(cfg.PODCAST_DIR, f"locandina-{k + 1}.png")
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", f, "-frames:v", "1",
                            dest_png], check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            log.warning(f"Locandina {os.path.basename(f)} non convertita ({e}): la salto")
            continue
        if loghi and os.path.abspath(f) == os.path.abspath(loghi):
            loghi_png = dest_png
        else:
            corsi_png.append(dest_png)
    corsi_png = genera_locandine_corsi(corsi_png, cfg.PODCAST_DIR)
    n_art = len(sezioni) - 2
    schede = [immagini[i] if i < len(immagini) and os.path.exists(immagini[i]) else None
              for i in range(n_art)]
    riserva = copertina or next((s for s in schede if s), None)
    if not riserva:
        raise RuntimeError("nessuna immagine per il video")
    sequenza = [copertina or riserva] + [s or riserva for s in schede] + [copertina or riserva]
    ini_chiusura, fin_chiusura = sezioni[-1]
    ini_corsi = min(max(inizio_corsi if inizio_corsi is not None else ini_chiusura, ini_chiusura),
                    fin_chiusura - 0.5)
    sulla_copertina = max(0.0, ini_corsi - ini_chiusura)        # avvertenza: ancora la copertina
    dur_corsi = max(0.5, fin_chiusura - ini_corsi)
    per_locandina, silenzio = piano_coda(dur_corsi, len(corsi_png), sec if loghi_png else 0.0)
    lista = os.path.join(cfg.PODCAST_DIR, "diapositive.txt")
    with open(lista, "w", encoding="utf-8") as f:
        # intro e articoli: ognuno dura quanto il suo testo; della chiusura, la copertina sta fino
        # all'invito ai corsi, poi le locandine (se ci sono)
        for img, (ini, fin) in list(zip(sequenza, sezioni))[:-1]:
            f.write(f"file '{os.path.abspath(img)}'\nduration {max(0.5, fin - ini):.3f}\n")
        if not corsi_png:
            f.write(f"file '{os.path.abspath(sequenza[-1])}'\nduration {max(0.5, fin_chiusura - ini_chiusura):.3f}\n")
        elif sulla_copertina > 0.05:
            f.write(f"file '{os.path.abspath(sequenza[-1])}'\nduration {sulla_copertina:.3f}\n")
        ultima = sequenza[-1]
        for img in corsi_png:
            f.write(f"file '{os.path.abspath(img)}'\nduration {per_locandina:.3f}\n")
            ultima = img
        if loghi_png:
            f.write(f"file '{os.path.abspath(loghi_png)}'\nduration {sec:.3f}\n")
            ultima = loghi_png
        f.write(f"file '{os.path.abspath(ultima)}'\n")
    base = (f"scale={VIDEO_L}:{VIDEO_A}:force_original_aspect_ratio=decrease,"
            f"pad={VIDEO_L}:{VIDEO_A}:(ow-iw)/2:(oh-ih)/2:color=white")

    def esegui(vf):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista,
             "-i", mp3, "-vf", vf,
             *(["-af", f"apad=pad_dur={silenzio:.3f}"] if silenzio > 0 else []),
             "-r", "25", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "23", "-c:a", "aac", "-b:a", "128k",
             "-shortest", "-movflags", "+faststart", dest],
            check=True)
    esegui(f"{base},format=yuv420p")
    if not sottotitoli:
        return dest, False
    # Sottotitoli in una seconda passata sul video già montato: applicati direttamente
    # al concat delle immagini (un fotogramma per scheda) si perdeva la copertina.
    tmp = dest + ".sottotitoli.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", dest,
             "-vf", f"subtitles=filename={sottotitoli},format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy",
             "-movflags", "+faststart", tmp], check=True)
        os.replace(tmp, dest)
        return dest, True
    except subprocess.CalledProcessError as e:
        log.warning(f"Sottotitoli non applicati ({e}): video senza")
        return dest, False


# ═══════════════════════════════════════════════════════════════════════════════
# GENERA
# ═══════════════════════════════════════════════════════════════════════════════
def misura_lufs(wav):
    """Loudness integrata (LUFS) di un file, con il filtro ebur128 di ffmpeg."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", wav, "-af", "ebur128",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m else -20.0


def aggiungi_tappeto(mp3, musica, db=-24.0):
    """Mette `musica` (in ciclo) sotto tutto l'MP3, `db` LU sotto la voce (che e' a -16 LUFS), con
    dissolvenza in entrata e in uscita. La durata non cambia. True se fatto; mai eccezioni:
    se qualcosa va storto l'MP3 resta com'e'."""
    try:
        if not musica or not os.path.exists(musica):
            return False
        durata = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", mp3],
            check=True, capture_output=True, text=True).stdout.strip())
        guadagno = (-16.0 + db) - misura_lufs(musica)
        tmp = mp3 + ".tappeto.mp3"
        filtro = (f"[1:a]aresample=44100,volume={guadagno:.2f}dB,atrim=0:{durata:.3f},"
                  f"afade=t=in:st=0:d=1.5,afade=t=out:st={max(0.0, durata - 2.5):.2f}:d=2.5[m];"
                  "[0:a][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[out]")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-stream_loop", "-1", "-i", musica,
                        "-filter_complex", filtro, "-map", "[out]", "-map_metadata", "0",
                        "-ar", "44100", "-ac", "1", "-b:a", "128k", "-t", f"{durata:.3f}", tmp],
                       check=True, capture_output=True)
        os.replace(tmp, mp3)
        return True
    except Exception as e:
        log.warning(f"Tappeto musicale non aggiunto ({e}): il podcast resta senza")
        return False


def credito_musica():
    """La riga di credito della musica del podcast, o '' se il file non c'e'."""
    musica = getattr(cfg, "PODCAST_MUSICA", None)
    credito = (getattr(cfg, "PODCAST_MUSICA_CREDITO", "") or "").strip()
    return credito if credito and musica and os.path.exists(musica) else ""


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

        testi, apertura = scrivi_copione(articoli, copione_file)
        esito["copione"] = testi
        esito["apertura"] = apertura
        testo_intro = intro(len(articoli), apertura)
        # Le locandine dei corsi in programma: se ce ne sono, la voce le presenta prima dei saluti.
        locandine = locandine_video()
        loghi = loghi_video()
        n_corsi = len(locandine) - (1 if loghi and loghi in locandine else 0)
        chiusura, n_invito, n_saluti = chiusura_podcast(n_corsi > 0)
        with open(os.path.join(cfg.PODCAST_DIR, "copione.txt"), "w", encoding="utf-8") as f:
            f.write("\n\n".join([testo_intro] + [testo_visibile(t) for t in testi]
                                + [chiusura]) + "\n")

        import numpy as np
        import soundfile as sf
        gemini = cfg.PODCAST_MOTORE == "gemini"
        sezioni_testo = [testo_intro] + testi + [chiusura]
        sezioni_vis = [testo_visibile(s) for s in sezioni_testo]
        risultato_gemini, voce = None, None
        if gemini:
            try:
                voce = VoceGemini()
                risultato_gemini = voce.podcast_verificato(sezioni_testo)
            except Exception as e:
                if not cfg.PODCAST_RIPIEGO_CHATTERBOX:
                    raise
                # Stesso copione, nessuna chiamata API in più: la voce la fa il runner.
                log.warning(f"Gemini TTS non disponibile ({str(e)[:200]}): ripiego su Chatterbox")
                esito["avvisi"].append(f"Gemini non disponibile, voce Chatterbox: {str(e)[:120]}")
                esito["ripiego"] = "chatterbox"
                gemini = False
        if not gemini:
            voce = Voce()
        sr = voce.sr
        # sigla d'apertura (il volume si regola alla fine, sulla voce) o breve silenzio
        sigla_ini = sigla(sr) if cfg.PODCAST_SIGLA else np.zeros(0, dtype=np.float32)
        traccia = [sigla_ini, np.zeros(int(0.3 * sr), dtype=np.float32)]
        confini, verifica, cursore = [], [], len(sigla_ini) + int(0.3 * sr)
        tempi_parole = []           # (parola del copione, inizio, fine) per i sottotitoli
        if gemini:
            audio, conf_sez, verifica, punti, parole = risultato_gemini
            esito["punteggio"] = round(punti, 3)
            if punti < cfg.PODCAST_SOGLIA_WHISPER:
                esito["avvisi"].append(f"audio sotto soglia Whisper ({punti:.2f})")
            for v in verifica:
                log.info(f"  {v['punteggio']:.2f} | {v['frase'][:70]}")
            off = cursore / sr
            confini = [(off + i0, off + i1) for i0, i1 in conf_sez]
            tempi_parole = tempi_parole_copione(sezioni_vis, [(w, off + a0, off + a1)
                                                              for w, a0, a1 in parole])
            traccia.append(audio)
            cursore += len(audio)
            traccia.append(np.zeros(int(0.45 * sr), dtype=np.float32))
            cursore += int(0.45 * sr)
            sezioni_testo = []          # niente ciclo per frase
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
                # Chatterbox non dà i tempi delle parole: si distribuiscono sulla frase
                parole_fr = testo_visibile(fr).split()
                durata_fr = len(audio) / sr
                peso = [len(w) + 1 for w in parole_fr]
                t = cursore / sr
                for w, q in zip(parole_fr, peso):
                    d = durata_fr * q / sum(peso)
                    tempi_parole.append((w, t, t + d))
                    t += d
                traccia.append(audio)
                cursore += len(audio)
            confini.append((inizio / sr, cursore / sr))
            pausa = int(0.45 * sr)
            traccia.append(np.zeros(pausa, dtype=np.float32))
            cursore += pausa
        # La voce si normalizza da sola (-16 LUFS); le sigle si aggiungono dopo a un
        # livello fisso rispetto alla voce: normalizzando tutto insieme il loudnorm
        # alzerebbe proprio la sigla, che è la parte più bassa.
        off_ini = len(sigla_ini) / sr
        voce_raw = np.concatenate(traccia[1:])
        voce_raw, rumori = silenzia_rumori(voce_raw, sr)
        if rumori:
            log.info(f"Audio: {len(rumori)} lampi di rumore tolti dalla voce "
                     f"({', '.join(f'{t:.1f}s' for t, _ in rumori)})")
        wav = os.path.join(cfg.PODCAST_DIR, "podcast_voce.wav")
        sf.write(wav, voce_raw, sr)
        durata_voce = len(voce_raw) / sr
        sigla_fin = sigla(sr, finale=True) if cfg.PODCAST_SIGLA else None
        coda = (len(sigla_fin) / sr if sigla_fin is not None else 0) + 0.3
        atempo = min(max(durata_voce / (cfg.PODCAST_DURATA_MAX - off_ini - coda - 0.5), 1.0),
                     cfg.PODCAST_ATEMPO_MAX)
        mp3 = os.path.join(cfg.PODCAST_DIR, f"podcast-s{str(wl['settimana']).zfill(2)}-{wl['anno']}.mp3")
        titolo = (f"title={getattr(cfg, 'PODCAST_TITOLO', 'EM Weekly Digest')} — podcast settimana "
                  f"{wl['settimana']}/{wl['anno']}")
        # Pulizia della voce: via i rumori di fondo (rombo sotto 70 Hz, fruscio e ronzii nelle pause).
        pulizia = ("highpass=f=70,afftdn=nr=12:nf=-50:tn=1,"
                   if getattr(cfg, "PODCAST_PULIZIA", True) else "")
        voce_norm = f"{pulizia}atempo={atempo:.4f},loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100"
        if cfg.PODCAST_SIGLA:
            f_ini = os.path.join(cfg.PODCAST_DIR, "sigla_ini.wav")
            f_fin = os.path.join(cfg.PODCAST_DIR, "sigla_fin.wav")
            sf.write(f_ini, sigla_ini, sr)
            sf.write(f_fin, sigla_fin, sr)
            obiettivo = -16 + cfg.PODCAST_SIGLA_DB
            g_ini = obiettivo - misura_lufs(f_ini)
            g_fin = obiettivo - misura_lufs(f_fin)
            filtro = (f"[0:a]aresample=44100,volume={g_ini:.2f}dB[a0];[1:a]{voce_norm}[a1];"
                      f"[2:a]aresample=44100,volume={g_fin:.2f}dB,apad=pad_dur=0.3[a2];"
                      f"[a0][a1][a2]concat=n=3:v=0:a=1[out]")
            ingressi = ["-i", f_ini, "-i", wav, "-i", f_fin]
        else:
            filtro = f"[0:a]{voce_norm},apad=pad_dur=0.3[out]"
            ingressi = ["-i", wav]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *ingressi,
                        "-filter_complex", filtro, "-map", "[out]",
                        "-ar", "44100", "-ac", "1", "-b:a", "128k", "-metadata", titolo, mp3],
                       check=True)
        if aggiungi_tappeto(mp3, getattr(cfg, "PODCAST_MUSICA", None), getattr(cfg, "PODCAST_MUSICA_DB", -24)):
            log.info(f"Audio: tappeto musicale {cfg.PODCAST_MUSICA} sotto la voce")
        durata = off_ini + durata_voce / atempo + coda
        if durata > cfg.PODCAST_DURATA_MAX + 1:
            esito["avvisi"].append(f"durata {durata:.0f}s oltre il limite anche accelerando")
        log.info(f"Audio: {durata:.1f}s (voce {durata_voce:.1f}s, velocità x{atempo:.2f}) -> {mp3}")

        def mappa(t):          # tempo nella traccia grezza -> tempo nell'MP3
            return off_ini + max(0.0, t - off_ini) / atempo

        sezioni = [(mappa(a), mappa(b)) for a, b in confini]
        # le diapositive coprono anche le pause: ognuna dura fino all'inizio della successiva
        sezioni = [(sezioni[i][0] if i else 0.0,
                    sezioni[i + 1][0] if i + 1 < len(sezioni) else durata)
                   for i in range(len(sezioni))]
        # Le schede che scorrono nel video: quelle indicate nell'archivio ("immagini", NIV) oppure
        # le schede PNG della newsletter (EM).
        prefisso = os.path.join(getattr(cfg, "IMG_DIR", "immagini"), f"digest-s{str(wl['settimana']).zfill(2)}-")
        immagini = doc.get("immagini") or [f"{prefisso}{str(i).zfill(2)}.png"
                                           for i in range(1, len(articoli) + 1)]
        copertina = genera_copertina(articoli, wl, os.path.join(cfg.PODCAST_DIR, "copertina.png"))
        mp4 = os.path.join(cfg.PODCAST_DIR, f"podcast-s{str(wl['settimana']).zfill(2)}-{wl['anno']}.mp4")
        ass = None
        if cfg.PODCAST_SOTTOTITOLI and tempi_parole:
            ass = scrivi_sottotitoli([(w, mappa(a), mappa(b)) for w, a, b in tempi_parole], 1.0,
                                     os.path.join(cfg.PODCAST_DIR, "sottotitoli.ass"))
        if locandine:
            log.info(f"Video: {n_corsi} locandine sulla voce di chiusura, poi i loghi "
                     f"({getattr(cfg, 'PODCAST_LOCANDINA_SEC', 5)} s)")
        t_corsi = inizio_invito_corsi(tempi_parole, n_invito, n_saluti, confini[-1][0], confini[-1][1],
                                      chiusura)
        t_corsi = mappa(t_corsi) if t_corsi is not None else None
        _, con_sottotitoli = genera_video(mp3, sezioni, immagini, copertina, mp4,
                                          sottotitoli=ass, locandine=locandine, inizio_corsi=t_corsi)
        esito["sottotitoli"] = con_sottotitoli
        # Per il Reel del podcast: da dove partono i saluti finali (dall'invito ai corsi, se c'e').
        t_saluti = t_corsi
        if t_saluti is None and tempi_parole and len(tempi_parole) >= n_saluti > 0:
            t_saluti = mappa(tempi_parole[len(tempi_parole) - n_saluti][1])
        if t_saluti is not None:
            esito["saluti"] = [round(t_saluti, 2), round(durata, 2)]
        log.info(f"Video: {mp4} ({os.path.getsize(mp4) / 1e6:.1f} MB)")

        for f in ("podcast_voce.wav", "sigla_ini.wav", "sigla_fin.wav"):
            try:
                os.remove(os.path.join(cfg.PODCAST_DIR, f))
            except OSError:
                pass
        esito.update(motore="gemini" if gemini else "chatterbox",
                     modello=getattr(voce, "modello_usato", None),
                     voce=cfg.PODCAST_GEMINI_VOCE if gemini else cfg.PODCAST_VOCE_RIF)
        esito.update(ok=True, mp3=mp3, mp4=mp4, durata=round(durata, 1),
                     sezioni=[[round(a, 2), round(b, 2)] for a, b in sezioni],
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
    if not getattr(cfg, "FB_SCHEDE", True) and p.get("message_elenco"):
        # unico post della settimana: elenco di tutti gli articoli
        messaggio_video = cfg.PODCAST_FB_TESTA_ELENCO.format(n=p["totale"]) + p["message_elenco"]
    else:
        messaggio_video = cfg.PODCAST_FB_TESTA.format(n=p["totale"]) + p["message"]
    if credito_musica():
        messaggio_video += "\n\n" + credito_musica()
    titolo = cfg.PODCAST_FB_TITOLO.format(**wl)

    quando = datetime.fromisoformat(p["quando"])
    adesso = datetime.now(ZoneInfo(cfg.FB_FUSO))
    if quando <= adesso + timedelta(minutes=12):
        log.warning("Orario del post del lunedì troppo vicino o passato: pubblico subito")
        quando = None

    schede = getattr(cfg, "FB_SCHEDE", True)   # False: niente ripiego sull'immagine
    if cfg.DRY_RUN or not cfg.FB_ABILITATO or not (cfg.FB_PAGE_ID and cfg.FB_PAGE_TOKEN):
        if not video and not schede:
            with open("post_facebook_podcast.txt", "w", encoding="utf-8") as f:
                f.write("Podcast non disponibile: con FB_SCHEDE = False il lunedì "
                        "non uscirebbe nessun post.\n")
            log.info("Podcast: nessun video e schede disattivate: nessun post (anteprima scritta)")
            return True
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
                      f"{e.read().decode('utf-8', 'replace')[:400]}")
        except Exception as e:
            log.error(f"Podcast: caricamento video fallito ({e})")
    else:
        log.warning(f"Podcast non disponibile ({esito.get('errore', 'nessun esito')})")

    if not post_id and not schede:
        log.warning("Post del lunedì non pubblicato: niente video e schede disattivate "
                    "(FB_SCHEDE = False)")
        return True
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
