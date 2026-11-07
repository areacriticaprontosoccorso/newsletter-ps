"""Pubblicazione su Instagram: un carosello per ogni uscita (modulo condiviso EM/POCUS/NIV).

Il carosello e' fatto di diapositive 4:5 (1080x1350, JPEG: l'API di Instagram non accetta
PNG): copertina del podcast (solo EM), schede degli articoli, locandine dei corsi.
Le schede hanno altezze diverse e le locandine sono verticali (1:1,41): ognuna viene
messa per intero, senza ritagli, su un fondo 4:5 (bande ai lati).

L'API vuole indirizzi pubblici per le immagini. Le diapositive si caricano quindi come foto
NON pubblicate sulla Pagina Facebook collegata (stesso token delle altre pubblicazioni) e si
usa l'indirizzo che Facebook restituisce. Nulla compare sulla Pagina.

Oltre al carosello escono due Reel (video 9:16, senza musica salvo un file facoltativo):
  - il Reel delle schede: le stesse diapositive del carosello, una dopo l'altra, ognuna per il
    tempo di leggerla (le schede in un riquadro 4:5 nella zona sicura, fuori dai pulsanti);
  - solo EM: un estratto di 60-90 s del podcast.
Le schede di Instagram sono essenziali (titolo + «perche' conta»), diverse da quelle della
mail e di Facebook.

Principi:
  - non solleva mai eccezioni e non blocca mai l'invio della newsletter;
  - senza IG_USER_ID (o senza token della Pagina) non fa nulla e lo dice nel log;
  - in prova a vuoto (dry run) non chiama nessuna API: salva le diapositive e la didascalia.
"""
import base64
import json
import logging
import mimetypes
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

log = logging.getLogger(__name__)

LARGHEZZA, ALTEZZA = 1080, 1350        # 4:5, il formato verticale massimo del feed
MAX_DIAPOSITIVE = 10                   # limite di Instagram per un carosello
MAX_DIDASCALIA = 2200                  # caratteri
MAX_HASHTAG = 30
SFONDO_SCHEDE = "#ffffff"
SFONDO_LOCANDINE = "#ffffff"
ATTESA_MAX_S = 90                      # attesa massima che Instagram elabori un contenitore
PAUSA_S = 3
ATTESA_REEL_S = 300                    # un video si elabora piu' lentamente di una foto

REEL_L, REEL_A = 1080, 1920            # 9:16
REEL_Y = 240                           # sopra il riquadro 4:5: la zona che l'app copre col profilo
REEL_COPERTINA_S = 3.0                 # durata delle diapositive nel Reel, in secondi
REEL_LOCANDINA_S = 4.0
REEL_SCHEDA_MIN_S, REEL_SCHEDA_MAX_S = 5.0, 8.0
REEL_PAROLE_AL_SEC = 4.0               # lettura veloce: 3 s fissi + parole / 4
ESTRATTO_MIN_S, ESTRATTO_STANDARD_S, ESTRATTO_MAX_S = 45.0, 75.0, 90.0


# ─────────────────────────────── diapositive ───────────────────────────────

def prepara_diapositive(voci, cartella, prefisso="ig"):
    """voci: [(percorso_immagine, sfondo_css), ...]. Restituisce i percorsi dei JPEG 1080x1350,
    nello stesso ordine; una voce che non si converte viene saltata. Con Chromium (Playwright),
    gia' presente nei workflow per le schede, quindi senza dipendenze nuove."""
    voci = [(p, s) for p, s in voci if p and os.path.exists(p)]
    if not voci:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Instagram: Playwright non installato, nessuna diapositiva")
        return []
    os.makedirs(cartella, exist_ok=True)
    fatte = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            pagina = browser.new_page(viewport={"width": LARGHEZZA, "height": ALTEZZA},
                                      device_scale_factor=1)
            for k, (src, sfondo) in enumerate(voci, 1):
                try:
                    mime = mimetypes.guess_type(src)[0] or "image/png"
                    with open(src, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("ascii")
                    pagina.set_content(
                        f'<html><body style="margin:0;width:{LARGHEZZA}px;height:{ALTEZZA}px;'
                        f'background:{sfondo};overflow:hidden"><img src="data:{mime};base64,{b64}" '
                        'style="display:block;width:100%;height:100%;object-fit:contain"></body></html>',
                        wait_until="load")
                    dest = os.path.join(cartella, f"{prefisso}-{k:02d}.jpg")
                    pagina.screenshot(path=dest, type="jpeg", quality=92)
                    fatte.append(dest)
                except Exception as e:
                    log.warning(f"Instagram: diapositiva {os.path.basename(src)} saltata ({e})")
            browser.close()
    except Exception as e:
        log.error(f"Instagram: preparazione diapositive interrotta ({e})")
    return fatte


def limita_diapositive(voci, massimo=MAX_DIAPOSITIVE):
    """voci: [(percorso, sfondo, tipo)] con tipo in 'copertina' | 'scheda' | 'locandina'.
    Oltre il massimo si tolgono le ultime schede: copertina e locandine restano."""
    voci = list(voci)
    while len(voci) > massimo:
        for i in range(len(voci) - 1, -1, -1):
            if voci[i][2] == "scheda":
                del voci[i]
                break
        else:
            del voci[massimo:]
    return voci



# ───────────────────────────── schede essenziali ─────────────────────────────

MAX_TITOLO = 260                       # caratteri: oltre, il titolo si accorcia
MAX_PRATICA = 260


def _taglia_parole(t, massimo):
    t = re.sub(r"\s+", " ", (t or "")).strip()
    if len(t) <= massimo:
        return t
    return t[:massimo].rsplit(" ", 1)[0].rstrip(",;:. ") + "…"


def _prima_frase(t, massimo=MAX_PRATICA):
    t = re.sub(r"\s+", " ", (t or "")).strip()
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    return _taglia_parole(m.group(1) if m else t, massimo)


def dati_scheda(a, cfg):
    """Dati essenziali di un articolo: titolo (in italiano se c'e'), tipo di studio, rivista e data,
    il «perche' conta» (la rilevanza; in mancanza, la prima frase della sintesi) e il PubMed ID."""
    tipo = (getattr(cfg, "TIPI_ARTICOLO", {}) or {}).get(a.get("tipo") or "")
    colore = getattr(cfg, "COLOR_ACCENT", "#c41e3a")
    if a.get("origine") == "em" and getattr(cfg, "COLOR_EM", None):
        colore = cfg.COLOR_EM
    pratica = (a.get("rilevanza") or "").strip() or _prima_frase(a.get("sintesi_it"))
    return {
        "titolo": _taglia_parole(a.get("titolo_it") or a.get("titolo") or "", MAX_TITOLO),
        "pratica": _taglia_parole(pratica, MAX_PRATICA),
        "rivista": a.get("rivista") or "",
        "data": a.get("data") or "",
        "tipo": tipo["label"] if tipo else "",
        "tipo_colore": tipo["colore"] if tipo else "",
        "pmid": a.get("pmid") or "",
        "colore": colore,
    }


def _e(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def html_scheda(d, indice, wl, cfg, larghezza=LARGHEZZA, altezza=ALTEZZA):
    """Scheda essenziale 4:5 per lo screenshot. I caratteri si adattano da soli all'altezza
    (script in pagina): un titolo lungo come quelli dei consensus non esce dal riquadro."""
    colore = d["colore"]
    scuro = getattr(cfg, "COLOR_DARK", "#1a1a1a")
    badge = (f'<span style="font:700 20px monospace;letter-spacing:2px;text-transform:uppercase;'
             f'color:#fff;background:{_e(d["tipo_colore"])};padding:7px 14px;border-radius:4px;'
             f'margin-left:18px;">{_e(d["tipo"])}</span>') if d["tipo"] else ""
    pratica = (f'<div style="margin-top:46px;background:#f7f4ef;border-left:10px solid {colore};'
               f'padding:34px 40px 38px;"><div style="font:700 20px monospace;letter-spacing:3px;'
               f'text-transform:uppercase;color:{colore};margin-bottom:16px;">Perché conta</div>'
               f'<div id="r" style="font-family:Georgia,\'Times New Roman\',serif;font-weight:700;'
               f'font-size:46px;line-height:1.3;color:#1a1a1a;">{_e(d["pratica"])}</div></div>'
               ) if d["pratica"] else ""
    fonte = " · ".join(x for x in (d["rivista"], d["data"]) if x)
    pmid = f'PubMed {_e(d["pmid"])}' if d["pmid"] else ""
    return f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="UTF-8"></head>
<body style="margin:0;width:{larghezza}px;height:{altezza}px;background:#ffffff;overflow:hidden;position:relative;">
  <div style="background:{colore};height:10px;"></div>
  <div style="background:{scuro};padding:26px 64px 28px;">
    <div style="font:20px monospace;color:#9a9a9a;letter-spacing:3px;text-transform:uppercase;">{_e(getattr(cfg, "NOME_SERVIZIO", ""))}</div>
    <div style="font:22px monospace;color:#e2e2e2;margin-top:10px;white-space:nowrap;">{_e(getattr(cfg, "NOME_NEWSLETTER", ""))} &middot; Settimana {_e(wl["settimana"])}/{_e(wl["anno"])}</div>
  </div>
  <div id="c" style="position:absolute;left:64px;right:64px;top:170px;bottom:110px;overflow:hidden;">
    <div id="i">
      <div style="margin-bottom:30px;white-space:nowrap;">
        <span style="font:700 54px monospace;color:{colore};">{indice:02d}</span>{badge}
      </div>
      <div style="font:22px monospace;color:#888;margin-bottom:26px;">{_e(fonte)}</div>
      <div id="t" style="font-family:Georgia,'Times New Roman',serif;font-weight:700;font-size:70px;line-height:1.2;color:#1a1a1a;">{_e(d["titolo"])}</div>
      {pratica}
    </div>
  </div>
  <div style="position:absolute;left:64px;right:64px;bottom:34px;border-top:2px solid #e8e3db;padding-top:18px;font:20px monospace;color:#0a4d68;">{pmid}</div>
<script>
(function () {{
  var c = document.getElementById('c'), i = document.getElementById('i');
  var t = document.getElementById('t'), r = document.getElementById('r');
  var ft = 70, fr = 46;
  function applica() {{ t.style.fontSize = ft + 'px'; if (r) r.style.fontSize = fr + 'px'; }}
  applica();
  while (i.offsetHeight > c.clientHeight && (ft > 34 || fr > 26)) {{
    if (ft > 34) ft -= 2;
    if (fr > 26) fr -= 1;
    applica();
  }}
  var libero = c.clientHeight - i.offsetHeight;
  if (libero > 0) c.style.top = (170 + Math.floor(libero * 0.35)) + 'px';
}})();
</script>
</body></html>"""


def genera_schede(articoli, wl, cfg, cartella):
    """Una scheda essenziale PNG 1080x1350 per articolo. Restituisce (percorsi, parole): le parole
    di ogni scheda servono a calcolarne la durata nel Reel. Mai eccezioni (in caso di problemi
    restituisce quello che e' riuscito a fare)."""
    percorsi, parole = [], []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Instagram: Playwright non installato, nessuna scheda")
        return percorsi, parole
    try:
        os.makedirs(cartella, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            pagina = browser.new_page(viewport={"width": LARGHEZZA, "height": ALTEZZA},
                                      device_scale_factor=1)
            for i, a in enumerate(articoli, 1):
                try:
                    d = dati_scheda(a, cfg)
                    pagina.set_content(html_scheda(d, i, wl, cfg), wait_until="load")
                    dest = os.path.join(cartella, f"scheda-{i:02d}.png")
                    pagina.screenshot(path=dest, type="png")
                    percorsi.append(dest)
                    parole.append(len((d["titolo"] + " " + d["pratica"]).split()))
                except Exception as e:
                    log.warning(f"Instagram: scheda {i} non generata ({e})")
            browser.close()
    except Exception as e:
        log.error(f"Instagram: generazione schede interrotta ({e})")
    return percorsi, parole


# ──────────────────────────────────── Reel ────────────────────────────────────

def durate_reel(tipi, parole=None):
    """Secondi di ogni diapositiva nel Reel. `tipi`: 'copertina' | 'scheda' | 'locandina' nell'ordine
    delle diapositive; `parole`: parole di ogni scheda, nell'ordine delle schede."""
    parole = list(parole or [])
    durate, k = [], 0
    for t in tipi:
        if t == "scheda":
            n = parole[k] if k < len(parole) else 40
            k += 1
            durate.append(round(min(REEL_SCHEDA_MAX_S,
                                    max(REEL_SCHEDA_MIN_S, 3.0 + n / REEL_PAROLE_AL_SEC)), 2))
        elif t == "copertina":
            durate.append(REEL_COPERTINA_S)
        else:
            durate.append(REEL_LOCANDINA_S)
    return durate


def _colore_ffmpeg(c):
    c = (c or "").strip().lstrip("#")
    return "0x" + c if re.fullmatch(r"[0-9a-fA-F]{6}", c) else "0x1a1a1a"


def crea_reel(diapositive, durate, dest, sfondo="#1a1a1a", musica=None):
    """Video 9:16 dalle diapositive 4:5 (JPEG 1080x1350): ognuna per la sua durata, nel riquadro
    sotto la zona coperta dall'app. Traccia audio sempre presente: silenziosa, oppure `musica`
    (file facoltativo, ripetuto e con dissolvenza finale) a volume basso. Solleva se ffmpeg fallisce."""
    if not diapositive or len(diapositive) != len(durate):
        raise ValueError("diapositive e durate non corrispondono")
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    n = len(diapositive)
    totale = sum(durate)
    ingressi = []
    for img, d in zip(diapositive, durate):
        ingressi += ["-loop", "1", "-framerate", "30", "-t", f"{d:.3f}", "-i", os.path.abspath(img)]
    colore = _colore_ffmpeg(sfondo)
    parti = [f"[{k}:v]scale={LARGHEZZA}:{ALTEZZA},setsar=1,pad={REEL_L}:{REEL_A}:0:{REEL_Y}:color={colore}[v{k}]"
             for k in range(n)]
    graf = (";".join(parti) + ";" + "".join(f"[v{k}]" for k in range(n))
            + f"concat=n={n}:v=1:a=0,fps=30,fade=t=out:st={max(0.0, totale - 0.5):.2f}:d=0.5,"
            "format=yuv420p[v]")
    if musica and os.path.exists(musica):
        ingressi += ["-stream_loop", "-1", "-i", musica]
        graf += (f";[{n}:a]volume=0.35,atrim=0:{totale:.3f},"
                 f"afade=t=out:st={max(0.0, totale - 1.5):.2f}:d=1.5[a]")
    else:
        ingressi += ["-f", "lavfi", "-t", f"{totale:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        graf += f";[{n}:a]anull[a]"
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *ingressi, "-filter_complex", graf,
                        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
                        "-tune", "stillimage", "-crf", "24", "-c:a", "aac", "-b:a", "128k",
                        "-ar", "44100", "-t", f"{totale:.3f}", "-movflags", "+faststart", dest],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError((e.stderr or b"").decode("utf-8", "replace")[-300:] or "ffmpeg") from None
    return dest


def punto_di_taglio(sezioni):
    """Dove tagliare l'estratto del podcast: la fine della sezione (intro, articoli...) che cade fra
    ESTRATTO_MIN_S e ESTRATTO_MAX_S, la piu' vicina a ESTRATTO_STANDARD_S; senza, ESTRATTO_STANDARD_S."""
    fini = [float(f) for _, f in (sezioni or []) if ESTRATTO_MIN_S <= float(f) <= ESTRATTO_MAX_S]
    if not fini:
        return ESTRATTO_STANDARD_S
    return min(fini, key=lambda x: abs(x - ESTRATTO_STANDARD_S))


def crea_estratto_podcast(mp4, dest, sezioni=None, sfondo_sfocato=True):
    """Estratto del video del podcast, in 9:16: il video 4:5 nel riquadro sotto la zona coperta
    dall'app, sopra e sotto lo stesso fotogramma sfocato; dissolvenza finale di immagine e voce.
    Restituisce (dest, durata). Solleva se ffmpeg fallisce."""
    durata = punto_di_taglio(sezioni)
    try:
        lung = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", mp4],
            check=True, capture_output=True, text=True).stdout.strip())
        durata = min(durata, lung)
    except (subprocess.CalledProcessError, ValueError, OSError):
        pass
    st = max(0.0, durata - 0.8)
    fg = f"[a]scale={LARGHEZZA}:{ALTEZZA}[fg];"
    bg = (f"[b]scale={REEL_L}:{REEL_A}:force_original_aspect_ratio=increase,crop={REEL_L}:{REEL_A},"
          f"boxblur=40:5[bg];" if sfondo_sfocato else
          f"color=c=black:s={REEL_L}x{REEL_A}[bg];")
    graf = (f"[0:v]split=2[a][b];{bg}{fg}[bg][fg]overlay=0:{REEL_Y},"
            f"fade=t=out:st={st:.2f}:d=0.8,format=yuv420p[v];"
            f"[0:a]afade=t=out:st={st:.2f}:d=0.8[au]")
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4, "-t", f"{durata:.3f}",
                        "-filter_complex", graf, "-map", "[v]", "-map", "[au]", "-r", "30",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "aac",
                        "-b:a", "128k", "-movflags", "+faststart", dest],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError((e.stderr or b"").decode("utf-8", "replace")[-300:] or "ffmpeg") from None
    return dest, durata

# ──────────────────────────────── didascalia ───────────────────────────────

def taglia_didascalia(testo, massimo=MAX_DIDASCALIA, max_hashtag=MAX_HASHTAG):
    """Rispetta i limiti di Instagram: 2200 caratteri e 30 hashtag al massimo."""
    testo = (testo or "").strip()
    visti, cont = [], 0

    def tieni(m):
        nonlocal cont
        cont += 1
        return m.group(0) if cont <= max_hashtag else ""
    testo = re.sub(r"#\w+", tieni, testo)
    testo = re.sub(r"[ \t]+\n", "\n", testo)
    if len(testo) > massimo:
        testo = testo[:massimo - 1].rstrip() + "…"
    return testo


def assembla(copertina, schede, corsi):
    """Mette in ordine le diapositive: copertina (se c'e'), schede degli articoli, locandine
    dei corsi (una sola volta per file). `corsi`: voci di corsi.carica_corsi()."""
    voci = []
    if copertina and os.path.exists(copertina):
        voci.append((copertina, SFONDO_SCHEDE, "copertina"))
    voci += [(p, SFONDO_SCHEDE, "scheda") for p in (schede or []) if p and os.path.exists(p)]
    viste = set()
    for c in corsi or []:
        f = c.get("immagine_file")
        if f and f not in viste and os.path.exists(f):
            viste.add(f)
            voci.append((f, SFONDO_LOCANDINE, "locandina"))
    return voci


def componi_didascalia(intestazione, titoli, corsi=None, chiusura="", hashtag="", data_leggibile=None):
    """Didascalia: intestazione, elenco numerato degli articoli, corsi in programma, chiusura,
    hashtag. Su Instagram i link nel testo non sono cliccabili: l'indirizzo va scritto in chiaro."""
    parti = [intestazione.strip()]
    if titoli:
        parti.append("\n".join(f"{i:02d} {t.strip()}" for i, t in enumerate(titoli, 1)))
    if corsi:
        righe = ["Corsi in programma:"]
        for c in corsi:
            d = f" ({data_leggibile(c)})" if data_leggibile else ""
            righe.append(f"- {c['titolo']}{d}")
        link = next((c.get("link") for c in corsi if c.get("link")), "")
        if link:
            righe.append("Iscrizioni: " + re.sub(r"^https?://", "", link).rstrip("/"))
        parti.append("\n".join(righe))
    if chiusura:
        parti.append(chiusura.strip())
    if hashtag:
        parti.append(hashtag.strip())
    return taglia_didascalia("\n\n".join(parti))


# ─────────────────────────────────── API ───────────────────────────────────

def _multipart(campi, file_campo, file_path):
    conf = uuid.uuid4().hex
    parti = [f'--{conf}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
             for k, v in campi.items()]
    with open(file_path, "rb") as f:
        dati = f.read()
    parti.append(f'--{conf}\r\nContent-Disposition: form-data; name="{file_campo}"; '
                 f'filename="{os.path.basename(file_path)}"\r\nContent-Type: image/jpeg\r\n\r\n'
                 .encode("utf-8"))
    parti.append(dati)
    parti.append(b"\r\n")
    parti.append(f"--{conf}--\r\n".encode("utf-8"))
    return b"".join(parti), f"multipart/form-data; boundary={conf}"


def _chiama(metodo, url, campi, file_path=None, timeout=60):
    """Una chiamata alla Graph API; restituisce il JSON. Solleva in caso di errore HTTP."""
    if metodo == "GET":
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(campi), method="GET")
    elif file_path:
        corpo, ctype = _multipart(campi, "source", file_path)
        req = urllib.request.Request(url, data=corpo, headers={"Content-Type": ctype}, method="POST")
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(campi).encode("utf-8"),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"},
                                     method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {e.code}: {corpo}") from None


def url_pubblico(percorso, page_id, token, versione):
    """Carica la diapositiva come foto non pubblicata sulla Pagina e ne restituisce
    l'indirizzo pubblico (quello che l'API di Instagram puo' scaricare)."""
    base = f"https://graph.facebook.com/{versione}"
    foto = _chiama("POST", f"{base}/{page_id}/photos",
                   {"published": "false", "access_token": token}, file_path=percorso)
    info = _chiama("GET", f"{base}/{foto['id']}", {"fields": "images", "access_token": token})
    immagini = info.get("images") or []
    if not immagini:
        raise RuntimeError("Facebook non ha restituito l'indirizzo dell'immagine")
    return max(immagini, key=lambda i: i.get("width", 0))["source"]


def _attendi(contenitore, token, versione, attesa_max=ATTESA_MAX_S):
    """Aspetta che Instagram finisca di elaborare il contenitore (status_code FINISHED)."""
    base = f"https://graph.facebook.com/{versione}/{contenitore}"
    inizio = time.time()
    stato = ""
    while time.time() - inizio <= attesa_max:
        stato = _chiama("GET", base, {"fields": "status_code", "access_token": token}).get("status_code", "")
        if stato in ("FINISHED", "PUBLISHED"):
            return True
        if stato in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram ha rifiutato il contenuto ({stato})")
        time.sleep(PAUSA_S)
    raise RuntimeError(f"Instagram non ha finito di elaborare entro {attesa_max} s (stato: {stato or '?'})")


def pubblica_carosello(file_diapositive, didascalia, ig_user_id, page_id, token, versione="v21.0"):
    """Pubblica le diapositive: con una sola, un post con immagine; con due o piu', un carosello.
    Restituisce l'id del post, o None se qualcosa non va (l'errore resta nel log)."""
    try:
        file_diapositive = file_diapositive[:MAX_DIAPOSITIVE]
        if not file_diapositive:
            return None
        base = f"https://graph.facebook.com/{versione}/{ig_user_id}"
        didascalia = taglia_didascalia(didascalia)
        urls = [url_pubblico(f, page_id, token, versione) for f in file_diapositive]
        if len(urls) == 1:
            c = _chiama("POST", f"{base}/media",
                        {"image_url": urls[0], "caption": didascalia, "access_token": token})["id"]
        else:
            figli = []
            for u in urls:
                fid = _chiama("POST", f"{base}/media",
                              {"image_url": u, "is_carousel_item": "true", "access_token": token})["id"]
                _attendi(fid, token, versione)
                figli.append(fid)
            c = _chiama("POST", f"{base}/media",
                        {"media_type": "CAROUSEL", "children": ",".join(figli),
                         "caption": didascalia, "access_token": token})["id"]
        _attendi(c, token, versione)
        post = _chiama("POST", f"{base}/media_publish",
                       {"creation_id": c, "access_token": token})["id"]
        log.info(f"Instagram: pubblicato ({len(urls)} diapositive) -> {post}")
        return post
    except Exception as e:
        log.error(f"Instagram: pubblicazione fallita ({e})")
        return None


def _carica_video(contenitore, percorso, token, versione):
    """Carico del file nel contenitore di un Reel (upload 'resumable': niente indirizzo pubblico)."""
    with open(percorso, "rb") as f:
        dati = f.read()
    req = urllib.request.Request(
        f"https://rupload.facebook.com/ig-api-upload/{versione}/{contenitore}", data=dati,
        headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(dati)),
                 "Content-Type": "application/octet-stream"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            esito = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}") from None
    if esito.get("success") is False:
        raise RuntimeError(f"caricamento del video rifiutato: {str(esito)[:200]}")
    return esito


def pubblica_reel(video, didascalia, ig_user_id, token, versione="v21.0"):
    """Pubblica un Reel. Restituisce l'id del post, o None se qualcosa non va (errore nel log)."""
    try:
        base = f"https://graph.facebook.com/{versione}/{ig_user_id}"
        c = _chiama("POST", f"{base}/media",
                    {"media_type": "REELS", "upload_type": "resumable", "share_to_feed": "true",
                     "caption": taglia_didascalia(didascalia), "access_token": token})["id"]
        _carica_video(c, video, token, versione)
        _attendi(c, token, versione, attesa_max=ATTESA_REEL_S)
        post = _chiama("POST", f"{base}/media_publish", {"creation_id": c, "access_token": token})["id"]
        log.info(f"Instagram: Reel pubblicato -> {post}")
        return post
    except Exception as e:
        log.error(f"Instagram: Reel non pubblicato ({e})")
        return None


# ─────────────────────────── punto d'ingresso unico ───────────────────────────

def token_instagram(cfg):
    """Token della Pagina per Instagram: IG_PAGE_TOKEN se c'e' (cosi' FB_PAGE_TOKEN, dei post
    Facebook, non si tocca), altrimenti FB_PAGE_TOKEN."""
    return getattr(cfg, "IG_PAGE_TOKEN", "") or getattr(cfg, "FB_PAGE_TOKEN", "")


def configurato(cfg):
    """True se ci sono IG_USER_ID, FB_PAGE_ID e un token: solo allora si pubblica davvero."""
    return bool(getattr(cfg, "IG_ABILITATO", True) and getattr(cfg, "IG_USER_ID", "")
                and getattr(cfg, "FB_PAGE_ID", "") and token_instagram(cfg))


def _reel_schede(file, tipi, parole, didascalia, cfg, dry_run):
    """Reel con le stesse diapositive del carosello. Mai eccezioni. Restituisce id, 'dry-run' o None."""
    try:
        if not getattr(cfg, "IG_REEL_SCHEDE", True):
            return None
        dest = os.path.join(cfg.IG_DIR, "reel-schede.mp4")
        crea_reel(file, durate_reel(tipi, parole), dest, getattr(cfg, "COLOR_DARK", "#1a1a1a"),
                  getattr(cfg, "IG_MUSICA", None))
        mb = os.path.getsize(dest) / 1e6
        if dry_run:
            log.info(f"Instagram (prova a vuoto): Reel delle schede {mb:.1f} MB in {dest}")
            return "dry-run"
        log.info(f"Instagram: Reel delle schede pronto ({mb:.1f} MB)")
        return pubblica_reel(dest, didascalia, cfg.IG_USER_ID, token_instagram(cfg),
                             getattr(cfg, "FB_API_VERSION", "v21.0"))
    except Exception as e:
        log.error(f"Instagram: Reel delle schede non creato ({e})")
        return None


def pubblica(voci, didascalia, cfg, dry_run=False, parole=None):
    """voci: [(percorso, sfondo, tipo)] come in `limita_diapositive`. Prepara le diapositive e le
    pubblica come carosello e come Reel (o, in prova a vuoto, le salva in cfg.IG_DIR con la
    didascalia). `parole`: parole di ogni scheda, per la durata nel Reel. Mai eccezioni.
    Restituisce l'id del carosello, 'dry-run' oppure None."""
    try:
        if not getattr(cfg, "IG_ABILITATO", True):
            return None
        voci = limita_diapositive(voci)
        if not voci:
            log.info("Instagram: niente da pubblicare")
            return None
        if not dry_run and not configurato(cfg):
            log.warning("Instagram: IG_USER_ID, FB_PAGE_ID o token (IG_PAGE_TOKEN / FB_PAGE_TOKEN) "
                        "mancanti - non pubblico")
            return None
        file = prepara_diapositive([(p, s) for p, s, _ in voci], cfg.IG_DIR)
        if not file:
            log.warning("Instagram: nessuna diapositiva pronta - non pubblico")
            return None
        versione = getattr(cfg, "FB_API_VERSION", "v21.0")
        if dry_run:
            with open(os.path.join(cfg.IG_DIR, "didascalia.txt"), "w", encoding="utf-8") as f:
                f.write(taglia_didascalia(didascalia) + "\n")
            log.info(f"Instagram (prova a vuoto): {len(file)} diapositive e didascalia in {cfg.IG_DIR}/")
            post = "dry-run"
        else:
            post = pubblica_carosello(file, didascalia, cfg.IG_USER_ID, cfg.FB_PAGE_ID,
                                      token_instagram(cfg), versione)
        # Se qualche diapositiva e' stata saltata nella conversione, i tipi non coincidono:
        # in quel caso il Reel usa durate di scheda per tutte (meglio di nessun Reel).
        tipi = [t for _, _, t in voci]
        if len(tipi) != len(file):
            tipi = ["scheda"] * len(file)
        _reel_schede(file, tipi, parole, didascalia, cfg, dry_run)
        return post
    except Exception as e:
        log.error(f"Instagram: errore inatteso ({e})")
        return None


def pubblica_reel_podcast(mp4, sezioni, didascalia, cfg, dry_run=False):
    """Reel con l'estratto del podcast (solo EM). Mai eccezioni. Id, 'dry-run' o None."""
    try:
        if not getattr(cfg, "IG_ABILITATO", True) or not getattr(cfg, "IG_REEL_PODCAST", True):
            return None
        if not mp4 or not os.path.exists(mp4):
            log.info("Instagram: nessun video del podcast, niente Reel dell'estratto")
            return None
        if not dry_run and not configurato(cfg):
            return None
        os.makedirs(cfg.IG_DIR, exist_ok=True)
        dest, durata = crea_estratto_podcast(mp4, os.path.join(cfg.IG_DIR, "reel-podcast.mp4"), sezioni)
        mb = os.path.getsize(dest) / 1e6
        if dry_run:
            with open(os.path.join(cfg.IG_DIR, "didascalia-podcast.txt"), "w", encoding="utf-8") as f:
                f.write(taglia_didascalia(didascalia) + "\n")
            log.info(f"Instagram (prova a vuoto): Reel del podcast {durata:.0f} s, {mb:.1f} MB in {dest}")
            return "dry-run"
        log.info(f"Instagram: Reel del podcast pronto ({durata:.0f} s, {mb:.1f} MB)")
        return pubblica_reel(dest, didascalia, cfg.IG_USER_ID, token_instagram(cfg),
                             getattr(cfg, "FB_API_VERSION", "v21.0"))
    except Exception as e:
        log.error(f"Instagram: Reel del podcast non creato ({e})")
        return None
