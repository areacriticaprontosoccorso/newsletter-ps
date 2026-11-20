"""
Pubblicazione su LinkedIn, sul profilo personale: il video del podcast con un testo.

API di LinkedIn (Videos API + Posts API), solo libreria standard. L'app LinkedIn ha i prodotti
self-serve «Share on LinkedIn» e «Sign In with LinkedIn using OpenID Connect» (nessuna revisione).
Servono tre secret, che linkedin_autorizza.py stampa:

    LINKEDIN_TOKEN      token di accesso (dura 60 giorni e NON si rinnova da solo)
    LINKEDIN_AUTORE     urn:li:person:... (il profilo che pubblica)
    LINKEDIN_SCADENZA   data di scadenza del token (AAAA-MM-GG)

Senza i secret non fa nulla. Mai eccezioni: un errore finisce nel log (ERROR, quindi nell'email di
avviso). Dieci giorni prima della scadenza del token il log scrive un ERROR apposta, cosi' arriva
l'avviso per email con le istruzioni per rinnovarlo.

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
from datetime import date

log = logging.getLogger("linkedin")

API = "https://api.linkedin.com/rest"
VERSIONE = "202608"            # LinkedIn-Version (AAAAMM): ogni versione vale circa un anno
TESTO_MAX = 2900               # limite di LinkedIn: 3000 caratteri
AVVISO_GIORNI = 10
ATTESA_S = 600


class ErroreLinkedIn(RuntimeError):
    pass


def configurato():
    return bool(os.environ.get("LINKEDIN_TOKEN", "").strip() and os.environ.get("LINKEDIN_AUTORE", "").strip())


def giorni_alla_scadenza(oggi=None):
    """Giorni che mancano alla scadenza del token (LINKEDIN_SCADENZA), o None se non e' indicata."""
    s = os.environ.get("LINKEDIN_SCADENZA", "").strip()
    try:
        return (date.fromisoformat(s) - (oggi or date.today())).days if s else None
    except ValueError:
        return None


def controlla_scadenza(oggi=None):
    """ERROR nel log (quindi email di avviso) se il token e' scaduto o scade entro AVVISO_GIORNI.
    True se il token e' ancora valido (o la scadenza non e' nota)."""
    g = giorni_alla_scadenza(oggi)
    if g is None:
        return True
    if g < 0:
        log.error("LinkedIn: il token e' scaduto il " + os.environ["LINKEDIN_SCADENZA"].strip() +
                  ": rifare linkedin_autorizza.py e aggiornare LINKEDIN_TOKEN e LINKEDIN_SCADENZA")
        return False
    if g <= AVVISO_GIORNI:
        log.error(f"LinkedIn: il token scade tra {g} giorni ({os.environ['LINKEDIN_SCADENZA'].strip()}): "
                  "rifare linkedin_autorizza.py e aggiornare LINKEDIN_TOKEN e LINKEDIN_SCADENZA")
    return True


def testo_little(testo):
    """Il testo dei post di LinkedIn e' in «little text format»: i caratteri riservati vanno preceduti
    da una barra rovesciata, altrimenti il post viene troncato o rifiutato. Hashtag compresi (#)."""
    t = re.sub(r"([\\|{}@\[\]()<>#*_~])", r"\\\1", (testo or "").strip())
    return t[:TESTO_MAX]


def _http(metodo, url, intestazioni=None, dati=None, timeout=120):
    """(codice, intestazioni, corpo come testo). Solleva ErroreLinkedIn con il motivo."""
    req = urllib.request.Request(url, data=dati, method=metodo, headers=intestazioni or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", "replace")
        try:
            motivo = json.loads(corpo).get("message", corpo)
        except Exception:
            motivo = corpo
        raise ErroreLinkedIn(f"HTTP {e.code}: {str(motivo)[:300]}") from None
    except urllib.error.URLError as e:
        raise ErroreLinkedIn(f"rete: {e.reason}") from None


def _intestazioni(token, json_body=True):
    h = {"Authorization": f"Bearer {token}", "LinkedIn-Version": VERSIONE,
         "X-Restli-Protocol-Version": "2.0.0"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _carica_video(video, token, autore):
    """Videos API: initializeUpload, PUT delle parti, finalizeUpload. Restituisce urn:li:video:..."""
    grandezza = os.path.getsize(video)
    _, _, r = _http("POST", f"{API}/videos?action=initializeUpload", _intestazioni(token), json.dumps(
        {"initializeUploadRequest": {"owner": autore, "fileSizeBytes": grandezza,
                                     "uploadCaptions": False, "uploadThumbnail": False}}).encode(), 60)
    valore = json.loads(r)["value"]
    urn, istruzioni = valore["video"], valore["uploadInstructions"]
    etag = []
    with open(video, "rb") as f:
        for parte in istruzioni:
            f.seek(parte["firstByte"])
            pezzo = f.read(parte["lastByte"] - parte["firstByte"] + 1)
            _, testa, _ = _http("PUT", parte["uploadUrl"], {"Content-Type": "application/octet-stream"},
                                pezzo, 600)
            e = next((v for k, v in testa.items() if k.lower() == "etag"), None)
            if not e:
                raise ErroreLinkedIn("LinkedIn non ha restituito l'ETag di una parte del video")
            etag.append(e)
    _http("POST", f"{API}/videos?action=finalizeUpload", _intestazioni(token), json.dumps(
        {"finalizeUploadRequest": {"video": urn, "uploadToken": valore.get("uploadToken", ""),
                                   "uploadedPartIds": etag}}).encode(), 60)
    return urn


def _attendi_video(urn, token, attesa_max=ATTESA_S):
    """Aspetta che LinkedIn abbia elaborato il video (AVAILABLE). Solleva se fallisce."""
    scadenza = time.time() + attesa_max
    stato = ""
    while time.time() < scadenza:
        _, _, r = _http("GET", f"{API}/videos/{urllib.parse.quote(urn, safe='')}",
                        _intestazioni(token, False), None, 30)
        stato = json.loads(r).get("status", "")
        if stato == "AVAILABLE":
            return
        if stato in ("PROCESSING_FAILED", "FAILED"):
            raise ErroreLinkedIn(f"LinkedIn non ha elaborato il video ({stato})")
        time.sleep(10)
    log.warning(f"LinkedIn: video ancora in elaborazione dopo {attesa_max} s (stato '{stato}'): pubblico lo stesso")


def pubblica_video(video, titolo, testo):
    """Carica `video` e lo pubblica sul profilo con `testo`. id del post o None."""
    try:
        if not configurato():
            log.info("LinkedIn: LINKEDIN_TOKEN / LINKEDIN_AUTORE mancanti - non pubblico")
            return None
        if not controlla_scadenza():
            return None
        if not video or not os.path.exists(video):
            log.warning(f"LinkedIn: video non trovato ({video})")
            return None
        token = os.environ["LINKEDIN_TOKEN"].strip()
        autore = os.environ["LINKEDIN_AUTORE"].strip()
        urn = _carica_video(video, token, autore)
        _attendi_video(urn, token)
        post = {"author": autore, "commentary": testo_little(testo), "visibility": "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                                 "thirdPartyDistributionChannels": []},
                "content": {"media": {"title": (titolo or "")[:200], "id": urn}},
                "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
        _, testa, _ = _http("POST", f"{API}/posts", _intestazioni(token), json.dumps(post).encode("utf-8"), 60)
        pid = next((v for k, v in testa.items() if k.lower() == "x-restli-id"), "") or urn
        log.info(f"LinkedIn: video pubblicato -> {pid} ({os.path.getsize(video) / 1e6:.1f} MB)")
        return pid
    except ErroreLinkedIn as e:
        if "HTTP 401" in str(e):
            log.error("LinkedIn: token non valido o scaduto: rifare linkedin_autorizza.py e aggiornare "
                      f"LINKEDIN_TOKEN e LINKEDIN_SCADENZA ({e})")
        else:
            log.error(f"LinkedIn: video non pubblicato ({e})")
        return None
    except Exception as e:
        log.error(f"LinkedIn: video non pubblicato ({e})")
        return None
