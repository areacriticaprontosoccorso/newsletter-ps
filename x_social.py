"""
Pubblicazione su X (ex Twitter): un post con il video del Reel e un testo breve.

API v2 di X, autenticazione OAuth 1.0a con le quattro chiavi dell'app (statiche: vanno bene per un
workflow che gira una volta alla settimana, a differenza dei token OAuth 2.0 che scadono in due ore):

    X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET

Il video passa dal caricamento a pezzi (`/2/media/upload/initialize`, `/append`, `/finalize`, poi lo
stato se X lo elabora) e il post esce con `POST /2/tweets`. Solo libreria standard. Mai eccezioni:
un errore di X finisce nel log (ERROR, quindi nell'email di avviso), non fa fallire il run.
Costo: X addebita ogni post sui crediti dell'app (circa 0,015 $ per un post senza link).

Copia identica nei tre repo (newsletter-ps, newsletter-pocus, Newsletter-NIV).
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

log = logging.getLogger("x_social")

API = "https://api.x.com"
CHUNK = 4 * 1024 * 1024            # X accetta pezzi fino a 8 MB; 4 MB lasciano margine
MAX_CARATTERI = 280
ATTESA_ELABORAZIONE_S = 300
CHIAVI = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")


def _pct(s):
    return urllib.parse.quote(str(s), safe="~")        # RFC 3986, come vuole OAuth 1.0a


def firma_oauth1(metodo, url, parametri, chiave_app, segreto_app, token, segreto_token, nonce, timestamp):
    """Firma HMAC-SHA1 di una richiesta. `parametri`: quelli di query e di un corpo form-urlencoded
    (non quelli di un corpo JSON o multipart, che nella firma non entrano)."""
    tutti = dict(parametri)
    tutti.update({"oauth_consumer_key": chiave_app, "oauth_nonce": nonce, "oauth_signature_method": "HMAC-SHA1",
                  "oauth_timestamp": str(timestamp), "oauth_token": token, "oauth_version": "1.0"})
    stringa = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(tutti.items(), key=lambda kv: (_pct(kv[0]), _pct(kv[1]))))
    base = "&".join([metodo.upper(), _pct(url), _pct(stringa)])
    chiave = f"{_pct(segreto_app)}&{_pct(segreto_token)}"
    return base64.b64encode(hmac.new(chiave.encode(), base.encode(), hashlib.sha1).digest()).decode()


def intestazione_auth(metodo, url, parametri, chiavi, nonce=None, timestamp=None):
    nonce = nonce or secrets.token_hex(16)
    timestamp = timestamp or int(time.time())
    firma = firma_oauth1(metodo, url, parametri, chiavi["X_API_KEY"], chiavi["X_API_SECRET"],
                         chiavi["X_ACCESS_TOKEN"], chiavi["X_ACCESS_SECRET"], nonce, timestamp)
    campi = {"oauth_consumer_key": chiavi["X_API_KEY"], "oauth_nonce": nonce, "oauth_signature": firma,
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(timestamp),
             "oauth_token": chiavi["X_ACCESS_TOKEN"], "oauth_version": "1.0"}
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(campi.items()))


def _multipart(nome_campo, dati, campi):
    limite = uuid.uuid4().hex
    corpo = b""
    for k, v in campi.items():
        corpo += f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    corpo += (f'--{limite}\r\nContent-Disposition: form-data; name="{nome_campo}"; filename="pezzo.bin"\r\n'
              "Content-Type: application/octet-stream\r\n\r\n").encode() + dati + b"\r\n"
    corpo += f"--{limite}--\r\n".encode()
    return corpo, f"multipart/form-data; boundary={limite}"


def _chiama(metodo, percorso, chiavi, query=None, corpo_json=None, multipart=None, timeout=120):
    """Una chiamata all'API di X; restituisce il JSON ({} se la risposta e' vuota). Solleva RuntimeError."""
    url = API + percorso
    intest = {"Authorization": intestazione_auth(metodo, url, query or {}, chiavi)}
    dati = None
    if corpo_json is not None:
        dati, intest["Content-Type"] = json.dumps(corpo_json).encode("utf-8"), "application/json"
    elif multipart is not None:
        dati, intest["Content-Type"] = multipart
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, data=dati, headers=intest, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            testo = r.read().decode("utf-8")
            return json.loads(testo) if testo.strip() else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}") from None


def carica_video(percorso, chiavi):
    """Carica il video e restituisce il media_id, quando X ha finito di elaborarlo."""
    with open(percorso, "rb") as f:
        dati = f.read()
    ini = _chiama("POST", "/2/media/upload/initialize", chiavi,
                  corpo_json={"media_type": "video/mp4", "total_bytes": len(dati), "media_category": "tweet_video"})
    media_id = ini["data"]["id"]
    for n, inizio in enumerate(range(0, len(dati), CHUNK)):
        _chiama("POST", f"/2/media/upload/{media_id}/append", chiavi,
                multipart=_multipart("media", dati[inizio:inizio + CHUNK], {"segment_index": n}))
    fin = _chiama("POST", f"/2/media/upload/{media_id}/finalize", chiavi)
    info = (fin.get("data") or {}).get("processing_info")
    scadenza = time.time() + ATTESA_ELABORAZIONE_S
    while info and info.get("state") in ("pending", "in_progress"):
        if time.time() > scadenza:
            raise RuntimeError("X sta ancora elaborando il video dopo %d s" % ATTESA_ELABORAZIONE_S)
        time.sleep(max(1, int(info.get("check_after_secs", 5))))
        info = (_chiama("GET", "/2/media/upload", chiavi,
                        query={"command": "STATUS", "media_id": media_id}).get("data") or {}).get("processing_info")
    if info and info.get("state") == "failed":
        raise RuntimeError(f"X ha rifiutato il video: {str(info.get('error', info))[:200]}")
    return media_id


def pubblica_video(percorso, testo, chiavi):
    """Pubblica un post con il video. Restituisce l'id del post, o None (errore nel log). Mai eccezioni."""
    try:
        media_id = carica_video(percorso, chiavi)
        r = _chiama("POST", "/2/tweets", chiavi, corpo_json={"text": testo, "media": {"media_ids": [media_id]}})
        post = r["data"]["id"]
        log.info(f"X: post pubblicato -> {post}")
        return post
    except Exception as e:
        log.error(f"X: post non pubblicato ({e})")
        return None


def chiavi_da_config(cfg):
    """Le quattro chiavi di cfg, oppure None se ne manca una."""
    chiavi = {k: (getattr(cfg, k, "") or "").strip() for k in CHIAVI}
    return chiavi if all(chiavi.values()) else None


def testo_post(didascalia, cfg):
    """Testo del post: la prima riga della didascalia (titolo e settimana) e pochi hashtag, entro 280
    caratteri. Niente link: X fa pagare di piu' i post che ne contengono."""
    titolo = (didascalia or "").strip().split("\n")[0].strip()
    hashtag = getattr(cfg, "X_HASHTAG", "") or " ".join(str(getattr(cfg, "IG_HASHTAG", "")).split()[:3])
    invito = (getattr(cfg, "X_INVITO", "") or "").strip()
    coda = "\n\n".join(p for p in (invito, hashtag.strip()) if p)
    testo = titolo
    spazio = MAX_CARATTERI - len(coda) - (2 if coda else 0)
    if len(testo) > spazio:
        testo = testo[:max(0, spazio - 1)].rstrip() + "…"
    return (testo + ("\n\n" + coda if coda else "")).strip()


def pubblica_reel(video, didascalia, cfg, dry_run=False):
    """Il Reel delle schede anche su X (X_REEL = True). Mai eccezioni. Id, 'dry-run' o None."""
    try:
        if not getattr(cfg, "X_REEL", False) or getattr(cfg, "SOLO_INSTAGRAM", False):
            return None
        testo = testo_post(didascalia, cfg)
        if dry_run:
            with open(os.path.join(cfg.IG_DIR, "testo-x.txt"), "w", encoding="utf-8") as f:
                f.write(testo + "\n")
            log.info(f"X (prova a vuoto): post con il Reel, testo in {cfg.IG_DIR}/testo-x.txt")
            return "dry-run"
        chiavi = chiavi_da_config(cfg)
        if not chiavi:
            log.info("X: chiavi mancanti (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET) - non pubblico")
            return None
        return pubblica_video(video, testo, chiavi)
    except Exception as e:
        log.error(f"X: post non pubblicato ({e})")
        return None
