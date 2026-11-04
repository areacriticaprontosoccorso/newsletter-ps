"""Avvisi periodici dei corsi organizzati da Francesco Panero.

I corsi stanno in corsi.json (stesso formato in ogni repo, vedi README). A ogni
uscita compaiono i corsi non ancora conclusi, in cima alla mail, in un messaggio
Telegram e (POCUS ed EM) in un post Facebook programmato. Passata la data del
corso l'avviso sparisce da solo.

Solo libreria standard. Un errore in questo modulo NON deve mai bloccare la
newsletter: carica_corsi() non solleva eccezioni e in caso di problemi restituisce [].
"""
import base64
import html
import json
import logging
import mimetypes
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("corsi")

FILE_CORSI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corsi.json")
MAX_CORSI = 2            # al massimo due avvisi per uscita: i piu' vicini nel tempo
MAX_DESCRIZIONE = 400    # caratteri
MAX_IMMAGINE = 5 * 1024 * 1024   # byte: oltre, l'immagine viene ignorata (il corso resta)
ESTENSIONI_IMMAGINE = (".jpg", ".jpeg", ".png", ".gif")
CAPTION_TELEGRAM = 1024
# Riferimento per "ogni_settimane": le settimane si contano da questo lunedi'
# (o dal lunedi' della settimana di "mostra_dal", se indicato).
LUNEDI_RIFERIMENTO = date(2026, 1, 5)  # limite di Telegram per la didascalia di una foto

_MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
         "agosto", "settembre", "ottobre", "novembre", "dicembre"]
_GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def _avviso(msg):
    """Log e, su GitHub Actions, annotazione gialla visibile nel riepilogo del run
    (un corsi.json sbagliato non blocca la newsletter, ma deve vedersi)."""
    log.warning(msg)
    print(f"::warning title=corsi.json::{msg}", flush=True)


def oggi_roma():
    return datetime.now(ZoneInfo("Europe/Rome")).date()


def _data(valore):
    """'2026-11-14' -> date; None se assente o non valida."""
    if not valore:
        return None
    try:
        return datetime.strptime(str(valore).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _testo(valore, massimo=None):
    t = " ".join(str(valore or "").split())
    if massimo and len(t) > massimo:
        t = t[:massimo - 1].rstrip() + "…"
    return t


def _url(valore):
    u = str(valore or "").strip()
    return u if u.lower().startswith(("https://", "http://")) and " " not in u else ""


def _controlla_immagine(valore, cartella):
    """(percorso relativo, file assoluto, problema). Per un URL https: (None, None, '').
    Se c'e' un problema, il campo `problema` lo descrive."""
    v = str(valore or "").strip()
    if not v or v.lower().startswith(("https://", "http://")):
        return None, None, "" if _url(v) or not v else "URL immagine non valido"
    rel = v.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if (rel.startswith("/") or ".." in rel.split("/")
            or not rel.lower().endswith(ESTENSIONI_IMMAGINE)):
        return None, None, ("deve essere un percorso nel repo (es. locandine/corso.jpg; "
                            "formati jpg, png, gif) o un URL https")
    file = os.path.join(cartella, rel)
    if not os.path.isfile(file):
        return None, None, f"file '{rel}' non trovato nel repo"
    if os.path.getsize(file) > MAX_IMMAGINE:
        return None, None, (f"file '{rel}' troppo grande "
                            f"({os.path.getsize(file) // 1024} KB, massimo {MAX_IMMAGINE // 1024} KB)")
    return rel, file, ""


def _immagine(valore, cartella, base_url):
    """(file locale o None, URL pubblico o ''). `valore` e' un URL https oppure un
    percorso relativo al repo (es. 'locandine/corso.jpg'). Problemi -> (None, '')."""
    v = str(valore or "").strip()
    if not v:
        return None, ""
    rel, file, problema = _controlla_immagine(v, cartella)
    if problema:
        _avviso(f"immagine '{v}' ignorata ({problema}): l'avviso esce senza locandina")
        return None, ""
    if rel is None:                      # URL https
        return None, v
    url = f"{base_url.rstrip('/')}/{urllib.parse.quote(rel)}" if base_url else ""
    return file, url


def _settimana_di_uscita(oggi, inizio, mostra_dal, ogni, turno=None):
    """True se in questa settimana il corso va annunciato, con frequenza `ogni`
    settimane. L'ultima uscita prima del corso (7 giorni prima o meno) lo annuncia sempre.
    Con `turno` (0..ogni-1) le settimane si contano da una data fissa (lunedi' 5/1/2026),
    cosi' due corsi con turni diversi si alternano e non si incontrano mai."""
    if ogni <= 1 or 0 <= (inizio - oggi).days <= 7:
        return True
    lunedi = lambda d: d - timedelta(days=d.weekday())
    if turno is not None:
        return ((lunedi(oggi) - lunedi(LUNEDI_RIFERIMENTO)).days // 7) % ogni == turno
    rif = mostra_dal or LUNEDI_RIFERIMENTO
    return ((lunedi(oggi) - lunedi(rif)).days // 7) % ogni == 0


def _turno(valore, ogni):
    """Turno di alternanza (0..ogni-1); None se manca, False se non valido."""
    if valore in (None, ""):
        return None
    try:
        n = int(valore)
    except (TypeError, ValueError):
        return False
    return n if 0 <= n < ogni and str(valore).strip() == str(n) else False


def _ogni(valore):
    """Frequenza in settimane (1-8); None se il valore non e' valido."""
    if valore in (None, ""):
        return 1
    try:
        n = int(valore)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 8 and str(valore).strip() == str(n) else None


def carica_corsi(newsletter, oggi=None, percorso=None, massimo=MAX_CORSI, base_url="",
                 ignora_frequenza=False):
    """Corsi da annunciare oggi su `newsletter` ('pocus', 'niv' o 'em'), dal piu' vicino.
    Con `ignora_frequenza` si hanno tutti i corsi in corso di pubblicazione, a prescindere
    da `ogni_settimane` e `turno` (serve al video del podcast).
    Mai eccezioni: file mancante o rotto -> [] (con un avviso nel log)."""
    percorso = percorso or FILE_CORSI
    oggi = oggi or oggi_roma()
    try:
        if not os.path.exists(percorso):
            return []
        with open(percorso, encoding="utf-8") as f:
            dati = json.load(f)
        voci = dati.get("corsi", []) if isinstance(dati, dict) else dati
        if not isinstance(voci, list):
            raise ValueError("'corsi' deve essere un elenco")
    except Exception as e:
        _avviso(f"corsi.json non leggibile, nessun avviso corsi: {e}")
        return []

    risultato = []
    for i, v in enumerate(voci, 1):
        try:
            if not isinstance(v, dict) or v.get("attivo", True) is False:
                continue
            dove = v.get("newsletter")
            if dove is not None and newsletter not in (
                    [dove] if isinstance(dove, str) else list(dove)):
                continue
            titolo = _testo(v.get("titolo"), 160)
            inizio = _data(v.get("data_inizio"))
            if not titolo or not inizio:
                _avviso(f"voce {i} ignorata: servono 'titolo' e "
                        "'data_inizio' (AAAA-MM-GG)")
                continue
            fine = _data(v.get("data_fine")) or inizio
            if fine < inizio:
                _avviso(f"voce {i} ({titolo}): data_fine prima di data_inizio")
                fine = inizio
            mostra_dal = _data(v.get("mostra_dal"))
            if fine < oggi or (mostra_dal and oggi < mostra_dal):
                continue
            ogni = _ogni(v.get("ogni_settimane"))
            if ogni is None:
                _avviso(f"voce {i} ({titolo}): 'ogni_settimane' deve essere un numero da 1 a 8; "
                        "uso 1 (ogni settimana)")
                ogni = 1
            turno = _turno(v.get("turno"), ogni)
            if turno is False:
                _avviso(f"voce {i} ({titolo}): 'turno' deve essere un numero da 0 a {ogni - 1}; "
                        "lo ignoro")
                turno = None
            if not ignora_frequenza and not _settimana_di_uscita(
                    oggi, inizio, mostra_dal, ogni, turno):
                log.info(f"Corso '{titolo[:50]}': non in questa settimana (ogni {ogni} settimane)")
                continue
            iscr = _data(v.get("iscrizioni_fino"))
            img_file, img_url = _immagine(v.get("immagine"), os.path.dirname(
                os.path.abspath(percorso)), base_url)
            risultato.append({
                "titolo": titolo, "inizio": inizio, "fine": fine,
                "ora": _testo(v.get("ora"), 60),
                "luogo": _testo(v.get("luogo"), 160),
                "descrizione": _testo(v.get("descrizione"), MAX_DESCRIZIONE),
                "posti": _testo(v.get("posti"), 80),
                "link": _url(v.get("link")),
                "immagine_file": img_file, "immagine_url": img_url,
                "iscrizioni_fino": iscr if iscr and iscr >= oggi else None,
            })
        except Exception as e:
            _avviso(f"voce {i} ignorata: {e}")
    risultato.sort(key=lambda c: (c["inizio"], c["titolo"]))
    if len(risultato) > massimo:
        log.info(f"{len(risultato)} corsi in programma: ne annuncio i primi {massimo}")
    return risultato[:massimo]


def _g(d):
    return f"{d.day} {_MESI[d.month - 1]}"


def data_leggibile(c):
    """'sabato 14 novembre 2026' / '14–15 novembre 2026' / '30 novembre – 1 dicembre 2026'."""
    a, b = c["inizio"], c["fine"]
    if a == b:
        return f"{_GIORNI[a.weekday()]} {_g(a)} {a.year}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {_MESI[a.month - 1]} {a.year}"
    if a.year == b.year:
        return f"{_g(a)} – {_g(b)} {a.year}"
    return f"{_g(a)} {a.year} – {_g(b)} {b.year}"


def _esc(s):
    return html.escape(str(s), quote=True)


def html_corsi(corsi, colore="#0a6e8a", anteprima_locale=False):
    """Riga <tr> per la mail, da inserire sotto l'intestazione. '' se non ci sono corsi.
    anteprima_locale=True (dry run): la locandina e' incorporata nell'HTML, cosi' si
    vede anche prima che il file sia pubblicato su GitHub."""
    if not corsi:
        return ""
    etichetta = "Corso in programma" if len(corsi) == 1 else "Corsi in programma"
    schede = ""
    for c in corsi:
        meta = [f"<strong>{_esc(data_leggibile(c))}</strong>"]
        if c["ora"]:
            meta.append(_esc(c["ora"]))
        if c["luogo"]:
            meta.append(_esc(c["luogo"]))
        if c["posti"]:
            meta.append(_esc(c["posti"]))
        extra = ""
        if c["iscrizioni_fino"]:
            extra = (f'<div style="font-family:monospace;font-size:11px;color:#8a6d00;'
                     f'margin:6px 0 0;">Iscrizioni entro il {_esc(_g(c["iscrizioni_fino"]))} '
                     f'{c["iscrizioni_fino"].year}</div>')
        descr = (f'<div style="font-family:Georgia,serif;font-size:13.5px;color:#2a2a2a;'
                 f'line-height:1.55;margin:8px 0 0;">{_esc(c["descrizione"])}</div>'
                 if c["descrizione"] else "")
        bottone = (f'<table cellpadding="0" cellspacing="0" style="margin-top:12px;"><tr>'
                   f'<td bgcolor="{colore}" style="border-radius:4px;">'
                   f'<a href="{_esc(c["link"])}" style="display:inline-block;padding:9px 18px;'
                   f'font-family:Arial,sans-serif;font-size:13px;font-weight:700;color:#ffffff;'
                   f'text-decoration:none;">Programma e iscrizioni</a></td></tr></table>'
                   if c["link"] else "")
        src = c.get("immagine_url", "")
        if anteprima_locale and c.get("immagine_file"):
            tipo = mimetypes.guess_type(c["immagine_file"])[0] or "image/jpeg"
            with open(c["immagine_file"], "rb") as f:
                src = f"data:{tipo};base64," + base64.b64encode(f.read()).decode("ascii")
        locandina = ""
        if src:
            img = (f'<img src="{_esc(src)}" alt="Locandina: {_esc(c["titolo"])}" width="480" '
                   f'style="display:block;width:100%;max-width:480px;height:auto;border:0;'
                   f'border-radius:4px;margin:0 auto;" />')
            locandina = (f'<div style="margin:0 0 12px;">'
                         + (f'<a href="{_esc(c["link"])}">{img}</a>' if c["link"] else img)
                         + '</div>')
        schede += (f'<div style="margin:0 0 14px;padding-bottom:14px;'
                   f'border-bottom:1px solid #e3ddd0;">{locandina}'
                   f'<div style="font-family:Georgia,serif;font-size:18px;font-weight:700;'
                   f'color:#1a1a1a;line-height:1.3;">{_esc(c["titolo"])}</div>'
                   f'<div style="font-family:Arial,sans-serif;font-size:13px;color:#444;'
                   f'margin:6px 0 0;">{" &middot; ".join(meta)}</div>'
                   f'{extra}{descr}{bottone}</div>')
    return (f'<tr><td style="background:#fffdf7;padding:18px 32px 4px;'
            f'border-bottom:1px solid #e3ddd0;border-top:3px solid {colore};">'
            f'<div style="font-family:monospace;font-size:10px;letter-spacing:2px;'
            f'text-transform:uppercase;color:{colore};margin:0 0 10px;">{etichetta}</div>'
            f'{schede}</td></tr>')


def testo_telegram_corso(c):
    """Messaggio (HTML di Telegram) per un corso."""
    riga = ["📣 <b>Corso in programma</b>", "", f"<b>{_esc(c['titolo'])}</b>",
            f"📅 {_esc(data_leggibile(c))}" + (f" · {_esc(c['ora'])}" if c["ora"] else "")]
    if c["luogo"]:
        riga.append(f"📍 {_esc(c['luogo'])}")
    if c["posti"]:
        riga.append(f"👥 {_esc(c['posti'])}")
    if c["iscrizioni_fino"]:
        riga.append(f"⏳ Iscrizioni entro il {_esc(_g(c['iscrizioni_fino']))} "
                    f"{c['iscrizioni_fino'].year}")
    if c["descrizione"]:
        riga.append(f"\n{_esc(c['descrizione'])}")
    if c["link"]:
        riga.append(f'\n🔗 <a href="{_esc(c["link"])}">Programma e iscrizioni</a>')
    testo = "\n".join(riga)
    return testo if len(testo) <= 4096 else testo[:4090] + "\n…"


def telegram_photo(bot_token, chat_id, photo_url, caption=""):
    """sendPhoto per URL (Telegram scarica l'immagine da solo). True/False, mai eccezioni."""
    corpo = {"chat_id": chat_id, "photo": photo_url, "parse_mode": "HTML"}
    if caption:
        corpo["caption"] = caption
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendPhoto",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception as e:
        log.warning(f"Telegram sendPhoto fallito: {e}")
        return False


def invia_telegram(bot_token, chat_id, corsi, send_message, send_photo=telegram_photo,
                   pausa=1):
    """Invia un messaggio per corso, con la locandina se c'e'. Restituisce (riusciti, totale).
    Con locandina: foto con didascalia (max 1024 caratteri) oppure foto + testo. Se la
    foto non parte (es. file non ancora pubblicato) si invia il solo testo."""
    ok = 0
    for c in corsi or []:
        testo = testo_telegram_corso(c)
        url = c.get("immagine_url")
        inviato = False
        if url:
            if len(testo) <= CAPTION_TELEGRAM:
                inviato = send_photo(bot_token, chat_id, url, testo)
            elif send_photo(bot_token, chat_id, url, ""):
                time.sleep(pausa)
                inviato = send_message(testo)
        if not inviato:
            inviato = send_message(testo)
        ok += 1 if inviato else 0
        time.sleep(pausa)
    return ok, len(corsi or [])


def testo_facebook(c):
    """Testo semplice del post Facebook per un corso."""
    righe = [f"📣 CORSO — {c['titolo']}", "",
             f"📅 {data_leggibile(c)}" + (f" · {c['ora']}" if c["ora"] else "")]
    if c["luogo"]:
        righe.append(f"📍 {c['luogo']}")
    if c["posti"]:
        righe.append(f"👥 {c['posti']}")
    if c["iscrizioni_fino"]:
        righe.append(f"⏳ Iscrizioni entro il {_g(c['iscrizioni_fino'])} "
                     f"{c['iscrizioni_fino'].year}")
    if c["descrizione"]:
        righe += ["", c["descrizione"]]
    if c["link"]:
        righe += ["", f"Programma e iscrizioni: {c['link']}"]
    return "\n".join(righe)


def verifica(percorso=None):
    """Elenco dei problemi di corsi.json (vuoto = file a posto)."""
    percorso = percorso or FILE_CORSI
    if not os.path.exists(percorso):
        return []
    try:
        with open(percorso, encoding="utf-8") as f:
            dati = json.load(f)
    except Exception as e:
        return [f"file non leggibile: {e}"]
    voci = dati.get("corsi") if isinstance(dati, dict) else None
    if not isinstance(voci, list):
        return ["manca l'elenco \"corsi\""]
    problemi = []
    for i, v in enumerate(voci, 1):
        if not isinstance(v, dict):
            problemi.append(f"voce {i}: non e' un oggetto")
            continue
        nome = f"voce {i} ({_testo(v.get('titolo'), 40) or 'senza titolo'})"
        if not _testo(v.get("titolo")):
            problemi.append(f"{nome}: manca 'titolo'")
        for campo in ("data_fine", "mostra_dal", "iscrizioni_fino"):
            if v.get(campo) not in (None, "") and not _data(v.get(campo)):
                problemi.append(f"{nome}: '{campo}' deve essere AAAA-MM-GG")
        if not _data(v.get("data_inizio")):
            problemi.append(f"{nome}: manca 'data_inizio' valida")
        _, _, problema = _controlla_immagine(
            v.get("immagine"), os.path.dirname(os.path.abspath(percorso)))
        if problema:
            problemi.append(f"{nome}: 'immagine' {problema}")
        ogni = _ogni(v.get("ogni_settimane"))
        if ogni is None:
            problemi.append(f"{nome}: 'ogni_settimane' deve essere un numero da 1 a 8")
        elif _turno(v.get("turno"), ogni) is False:
            problemi.append(f"{nome}: 'turno' deve essere un numero da 0 a {ogni - 1} "
                            "(0 = prima settimana del ciclo, 1 = seconda...)")
        if v.get("link") and not _url(v.get("link")):
            problemi.append(f"{nome}: 'link' deve iniziare con https://")
        dove = v.get("newsletter")
        if dove is not None:
            for x in ([dove] if isinstance(dove, str) else list(dove)):
                if x not in ("pocus", "niv", "em"):
                    problemi.append(f"{nome}: newsletter '{x}' sconosciuta (usa pocus, niv, em)")
    return problemi


if __name__ == "__main__":
    import sys
    probl = verifica()
    for p in probl:
        print(f"::warning title=corsi.json::{p}")
        print(f"PROBLEMA: {p}")
    for nl in ("pocus", "niv", "em"):
        c = carica_corsi(nl)
        print(f"{nl}: {len(c)} corsi da annunciare oggi" + "".join(
            f"\n  - {x['titolo']} ({data_leggibile(x)})" for x in c))
    sys.exit(1 if probl else 0)
