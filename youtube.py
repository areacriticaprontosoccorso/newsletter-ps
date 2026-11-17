"""
Pubblicazione su YouTube: video del podcast e Reel come Shorts.

YouTube Data API v3, caricamento «resumable» (un'unica richiesta PUT con il file), solo libreria
standard. L'autenticazione e' OAuth con un refresh token che non scade (se la schermata di consenso
del progetto Google e' in «Produzione»); servono tre secret:

    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN   (youtube_autorizza.py li ottiene)

Senza i secret non fa nulla. Mai eccezioni: un errore finisce nel log (ERROR, quindi nell'email di
avviso), non fa fallire il run.

Limiti da conoscere:
  - quota: ogni caricamento costa 1600 unita' su 10.000 al giorno (circa 6 video al giorno);
  - un progetto Google non verificato da YouTube (modulo «API Services Audit») vede i video
    caricati dall'API bloccati come PRIVATI: il log lo segnala dopo ogni caricamento;
  - uno Short e' un video verticale fino a 3 minuti: basta il formato dei Reel (9:16) e «#Shorts».

Copia identica nei tre repo (newsletter-ps, newsletter-pocus, Newsletter-NIV).
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("youtube")

URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_CARICA = "https://www.googleapis.com/upload/youtube/v3/videos"
URL_VIDEO = "https://www.googleapis.com/youtube/v3/videos"
TITOLO_MAX = 100
DESCRIZIONE_MAX = 4900          # il limite e' 5000 byte: margine per i caratteri accentati
TENTATIVI = 3
PAUSA_S = 20


class ErroreYouTube(RuntimeError):
    pass


def configurato():
    return all(os.environ.get(k, "").strip() for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def _http(metodo, url, intestazioni=None, dati=None, timeout=120):
    """(codice, intestazioni, corpo come testo). Solleva ErroreYouTube con il motivo di Google."""
    req = urllib.request.Request(url, data=dati, method=metodo, headers=intestazioni or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", "replace")
        raise ErroreYouTube(f"HTTP {e.code}: {_motivo(corpo)}") from None
    except urllib.error.URLError as e:
        raise ErroreYouTube(f"rete: {e.reason}") from None


def _motivo(corpo):
    """Il messaggio di errore di Google in poche parole (con il 'reason' se c'e')."""
    try:
        e = json.loads(corpo).get("error", {})
        if isinstance(e, str):
            return f"{e}: {json.loads(corpo).get('error_description', '')}"[:300]
        motivi = ",".join(x.get("reason", "") for x in e.get("errors", []) if x.get("reason"))
        return f"{e.get('message', '')} [{motivi}]"[:300]
    except Exception:
        return corpo[:300]


def _access_token():
    corpo = urllib.parse.urlencode({
        "client_id": os.environ["YT_CLIENT_ID"].strip(),
        "client_secret": os.environ["YT_CLIENT_SECRET"].strip(),
        "refresh_token": os.environ["YT_REFRESH_TOKEN"].strip(),
        "grant_type": "refresh_token",
    }).encode()
    try:
        _, _, r = _http("POST", URL_TOKEN, {"Content-Type": "application/x-www-form-urlencoded"}, corpo, 30)
    except ErroreYouTube as e:
        if "invalid_grant" in str(e):
            raise ErroreYouTube("il token di YouTube non vale piu' (scaduto o revocato): rifare "
                                "youtube_autorizza.py e aggiornare YT_REFRESH_TOKEN") from None
        raise
    return json.loads(r)["access_token"]


def pulisci_titolo(testo, suffisso=""):
    """Una riga, senza < > (vietati da YouTube), entro 100 caratteri compreso il suffisso."""
    t = re.sub(r"[<>]", "", " ".join((testo or "").split()))
    t = re.sub(r"(\s*#\w+)+\s*$", "", t).strip()          # niente hashtag in coda al titolo
    spazio = TITOLO_MAX - len(suffisso)
    if len(t) > spazio:
        t = t[:spazio - 1].rstrip() + "…"
    return (t + suffisso).strip()


def pulisci_descrizione(testo):
    t = re.sub(r"[<>]", "", testo or "").strip()
    while len(t.encode("utf-8")) > DESCRIZIONE_MAX:
        t = t[:-50].rstrip()
    return t


def _stato_privacy(video_id, token):
    """'public', 'unlisted' o 'private' (None se non si riesce a leggerlo)."""
    try:
        _, _, r = _http("GET", f"{URL_VIDEO}?part=status&id={video_id}",
                        {"Authorization": f"Bearer {token}"}, None, 30)
        voci = json.loads(r).get("items", [])
        return voci[0]["status"]["privacyStatus"] if voci else None
    except Exception:
        return None


def pubblica_video(video, titolo, descrizione, privacy="public", tags=None, categoria="27",
                   sintetico=False, shorts=False):
    """Carica `video` su YouTube. `shorts`: aggiunge #Shorts alla descrizione. `sintetico`: dichiara
    il contenuto come generato con l'IA (la voce del podcast). Restituisce l'id del video o None."""
    try:
        if not configurato():
            log.info("YouTube: YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN mancanti - non pubblico")
            return None
        if not video or not os.path.exists(video):
            log.warning(f"YouTube: video non trovato ({video})")
            return None
        descrizione = pulisci_descrizione(descrizione)
        if shorts and "#shorts" not in descrizione.lower():
            descrizione = pulisci_descrizione(descrizione + "\n\n#Shorts")
        titolo = pulisci_titolo(titolo)
        stato = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "embeddable": True}
        if sintetico:
            stato["containsSyntheticMedia"] = True
        meta = {"snippet": {"title": titolo, "description": descrizione, "categoryId": str(categoria),
                            "tags": list(tags or [])[:15], "defaultLanguage": "it",
                            "defaultAudioLanguage": "it"},
                "status": stato}
        grandezza = os.path.getsize(video)
        ultimo = None
        for tentativo in range(1, TENTATIVI + 1):
            try:
                token = _access_token()
                _, testa, _ = _http(
                    "POST", f"{URL_CARICA}?uploadType=resumable&part=snippet,status",
                    {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(grandezza)},
                    json.dumps(meta).encode("utf-8"), 60)
                sede = next((v for k, v in testa.items() if k.lower() == "location"), None)
                if not sede:
                    raise ErroreYouTube("YouTube non ha restituito l'indirizzo di caricamento")
                with open(video, "rb") as f:
                    contenuto = f.read()
                _, _, r = _http("PUT", sede, {"Content-Type": "video/mp4", "Content-Length": str(grandezza)},
                                contenuto, 900)
                video_id = json.loads(r)["id"]
                log.info(f"YouTube: video pubblicato -> https://youtu.be/{video_id} ({grandezza / 1e6:.1f} MB)")
                visto = _stato_privacy(video_id, token)
                if visto and visto != privacy:
                    log.warning(f"YouTube: il video e' {visto.upper()} invece di {privacy.upper()}: un progetto Google "
                                "non verificato da YouTube (modulo «API Services Audit») ha i video dell'API "
                                "bloccati come privati; si rendono pubblici a mano da YouTube Studio")
                return video_id
            except ErroreYouTube as e:
                ultimo = e
                definitivo = any(x in str(e) for x in ("quotaExceeded", "uploadLimitExceeded", "forbidden",
                                                       "invalid_grant", "non vale piu", "insufficient"))
                if definitivo or tentativo == TENTATIVI:
                    break
                log.warning(f"YouTube: tentativo {tentativo} fallito ({str(e)[:160]}) - riprovo")
                time.sleep(PAUSA_S * tentativo)
        log.error(f"YouTube: video non pubblicato ({ultimo})")
        return None
    except Exception as e:
        log.error(f"YouTube: video non pubblicato ({e})")
        return None
