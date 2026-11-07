"""Pubblicazione su Instagram: un carosello per ogni uscita (modulo condiviso EM/POCUS/NIV).

Il carosello e' fatto di diapositive 4:5 (1080x1350, JPEG: l'API di Instagram non accetta
PNG): copertina del podcast (solo EM), schede degli articoli, locandine dei corsi.
Le schede hanno altezze diverse e le locandine sono verticali (1:1,41): ognuna viene
messa per intero, senza ritagli, su un fondo 4:5 (bande ai lati).

L'API vuole indirizzi pubblici per le immagini. Le diapositive si caricano quindi come foto
NON pubblicate sulla Pagina Facebook collegata (stesso token delle altre pubblicazioni) e si
usa l'indirizzo che Facebook restituisce. Nulla compare sulla Pagina.

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


# ─────────────────────────── punto d'ingresso unico ───────────────────────────

def pubblica(voci, didascalia, cfg, dry_run=False):
    """voci: [(percorso, sfondo, tipo)] come in `limita_diapositive`. Prepara le diapositive e le
    pubblica (o, in prova a vuoto, le salva in cfg.IG_DIR con la didascalia). Mai eccezioni.
    Restituisce l'id del post, 'dry-run' oppure None."""
    try:
        if not getattr(cfg, "IG_ABILITATO", True):
            return None
        voci = limita_diapositive(voci)
        if not voci:
            log.info("Instagram: niente da pubblicare")
            return None
        # Token dedicato a Instagram (IG_PAGE_TOKEN) se c'e', cosi' FB_PAGE_TOKEN, che serve ai post
        # Facebook, non va toccato; altrimenti si usa quello della Pagina.
        page_id = getattr(cfg, "FB_PAGE_ID", "")
        token = getattr(cfg, "IG_PAGE_TOKEN", "") or getattr(cfg, "FB_PAGE_TOKEN", "")
        configurato = bool(getattr(cfg, "IG_USER_ID", "") and page_id and token)
        if not dry_run and not configurato:
            log.warning("Instagram: IG_USER_ID, FB_PAGE_ID o FB_PAGE_TOKEN mancanti - non pubblico")
            return None
        file = prepara_diapositive([(p, s) for p, s, _ in voci], cfg.IG_DIR)
        if not file:
            log.warning("Instagram: nessuna diapositiva pronta - non pubblico")
            return None
        if dry_run:
            with open(os.path.join(cfg.IG_DIR, "didascalia.txt"), "w", encoding="utf-8") as f:
                f.write(taglia_didascalia(didascalia) + "\n")
            log.info(f"Instagram (prova a vuoto): {len(file)} diapositive e didascalia in {cfg.IG_DIR}/")
            return "dry-run"
        return pubblica_carosello(file, didascalia, cfg.IG_USER_ID, page_id, token,
                                  getattr(cfg, "FB_API_VERSION", "v21.0"))
    except Exception as e:
        log.error(f"Instagram: errore inatteso ({e})")
        return None
