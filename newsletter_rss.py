"""
EM Weekly Digest — Versione con lista destinatari (da secret DESTINATARI)
Pronto Soccorso San Giovanni Bosco, Torino

Comando: python newsletter_rss.py
"""

import re
import json
import time
import html
import logging
import base64
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

import config as cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(cfg.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("newsletter_rss")


def esc(s):
    """Escape per inserire testo dinamico nell'HTML dell'email."""
    return html.escape(str(s or ""), quote=True)


_RE_ESCLUSI = [re.compile(p, re.IGNORECASE) for p in cfg.ESCLUSIONI_TITOLO]


def escluso_per_titolo(titolo):
    """True se il titolo indica un tipo di pubblicazione da escludere."""
    # PubMed racchiude tra parentesi quadre i titoli tradotti da altre lingue.
    t = (titolo or "").strip().lstrip("[").strip()
    return any(r.search(t) for r in _RE_ESCLUSI)


def _estrai_json_array(testo):
    """Estrae il primo array JSON dalla risposta, tollerando i fence markdown."""
    t = re.sub(r"^```(?:json)?\s*", "", testo.strip())
    t = re.sub(r"\s*```$", "", t)
    inizio, fine = t.find("["), t.rfind("]")
    if inizio == -1 or fine == -1 or fine < inizio:
        raise ValueError(f"Nessun array JSON nella risposta: {t[:200]}")
    return json.loads(t[inizio:fine + 1])


def numero_settimana():
    now = datetime.now()
    mesi = ["Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno",
            "Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
    return {
        "settimana": now.isocalendar()[1],
        "anno":      now.year,
        "giorno":    now.day,
        "mese":      mesi[now.month - 1],
    }


def fetch_url(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": cfg.NCBI_TOOL})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            log.warning(f"Tentativo {attempt+1}/3 fallito: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Fetch fallito: {url}")


NS = {"dc": "http://purl.org/dc/elements/1.1/"}


def url_rss_pubmed(issn, limit=None):
    n = limit or cfg.RSS_LIMIT_DEFAULT
    return f"https://pubmed.ncbi.nlm.nih.gov/rss/journals/{issn}/?limit={n}&utm_campaign=journals"


def parse_pubdate(s):
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s)
    except Exception:
        return None


def estrai_abstract_da_description(desc):
    if not desc:
        return ""
    testo = re.sub(r"<[^>]+>", " ", desc)
    testo = html.unescape(testo)
    testo = re.sub(r"PMID:\s*\d+.*$", "", testo, flags=re.IGNORECASE)
    testo = re.sub(r"DOI:\s*[\w./-]+", "", testo, flags=re.IGNORECASE)
    testo = re.sub(r"\s+", " ", testo).strip()
    return testo[:2500]


def estrai_pmid(item):
    link_el = item.find("link")
    if link_el is not None and link_el.text:
        m = re.search(r"/(\d{7,9})/?", link_el.text)
        if m:
            return m.group(1)
    for ident in item.findall("dc:identifier", NS):
        if ident.text and ident.text.startswith("pmid:"):
            return ident.text.replace("pmid:", "").strip()
    return ""


def estrai_doi(item):
    for ident in item.findall("dc:identifier", NS):
        if ident.text and ident.text.startswith("doi:"):
            return ident.text.replace("doi:", "").strip()
    return ""


def estrai_autori(item):
    creators = item.findall("dc:creator", NS)
    nomi = [c.text for c in creators if c.text]
    if not nomi:
        return ""
    if len(nomi) > 3:
        return ", ".join(nomi[:3]) + " et al."
    return ", ".join(nomi)


def fetch_feed(rivista):
    url = url_rss_pubmed(rivista["issn"], rivista.get("limit"))
    try:
        raw = fetch_url(url)
        root = ET.fromstring(raw)
    except Exception as e:
        log.error(f"  {rivista['nlmta']}: errore RSS {e}")
        return []
    articoli = []
    for item in root.findall(".//item"):
        titolo   = (item.findtext("title") or "").strip()
        link     = (item.findtext("link") or "").strip()
        desc     = item.findtext("description") or ""
        pubdate  = parse_pubdate(item.findtext("pubDate"))
        pmid     = estrai_pmid(item)
        doi      = estrai_doi(item)
        autori   = estrai_autori(item)
        abstract = estrai_abstract_da_description(desc)
        if not pmid or not titolo:
            continue
        articoli.append({
            "pmid":       pmid,
            "titolo":     titolo.rstrip("."),
            "autori":     autori,
            "rivista":    rivista["nome"],
            "data":       pubdate.strftime("%Y %b %d") if pubdate else "",
            "pubdate_dt": pubdate,
            "doi":        doi,
            "abstract":   abstract,
            "url":        link or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    if not articoli:
        # Causa quasi sempre un ISSN errato: PubMed risponde 200 con un feed vuoto,
        # quindi senza questo controllo la rivista sparirebbe in silenzio.
        log.error(
            f"  {rivista['nlmta']}: FEED VUOTO - verificare l'ISSN {rivista['issn']} "
            "(per le riviste solo online provare l'eISSN)"
        )
    else:
        log.info(f"  {rivista['nlmta']}: {len(articoli)} articoli dal feed")
    return articoli


def raccogli_candidati(giorni=None):
    giorni = giorni or cfg.GIORNI_RICERCA
    log.info(f"Lettura RSS PubMed: ultimi {giorni} giorni su {len(cfg.RIVISTE)} riviste")
    cutoff = datetime.now(timezone.utc) - timedelta(days=giorni)
    tutti = []
    for rivista in cfg.RIVISTE:
        feed = fetch_feed(rivista)
        recenti = [
            a for a in feed
            if a["pubdate_dt"] and a["pubdate_dt"].astimezone(timezone.utc) >= cutoff
        ]
        log.info(f"    -> {len(recenti)} pubblicati negli ultimi {giorni}g")
        tutti.extend(recenti)
        time.sleep(0.3)
    seen = set()
    unici = []
    for a in tutti:
        if a["pmid"] not in seen:
            seen.add(a["pmid"])
            unici.append(a)
    candidati = []
    scartati_titolo = scartati_abstract = 0
    for a in unici:
        if escluso_per_titolo(a["titolo"]):
            scartati_titolo += 1
            log.info(f"    [scarto/tipo] {a['titolo'][:90]}")
            continue
        if not a["abstract"] or len(a["abstract"]) < cfg.ABSTRACT_MIN_CHARS:
            scartati_abstract += 1
            log.info(f"    [scarto/abstract {len(a['abstract'] or '')}c] {a['titolo'][:90]}")
            continue
        candidati.append(a)

    log.info(
        f"Unici {len(unici)} -> scartati {scartati_titolo} per tipo, "
        f"{scartati_abstract} per abstract -> {len(candidati)} candidati"
    )
    return candidati


def chiama_claude(prompt, max_tokens=1500, system=None, prefill=None):
    """prefill: testo con cui far iniziare la risposta (es. "[" per forzare il JSON).
    Viene riconcatenato in testa al risultato, perche' l'API restituisce solo la
    continuazione."""
    messaggi = [{"role": "user", "content": prompt}]
    if prefill:
        messaggi.append({"role": "assistant", "content": prefill})
    corpo = {
        "model":      cfg.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        # Sonnet 5 ha l'adaptive thinking attivo di default: lo disattiviamo,
        # cosi' la risposta e' solo testo e max_tokens non viene speso in thinking.
        "thinking":   {"type": "disabled"},
        "messages":   messaggi,
    }
    if system:
        corpo["system"] = system
    payload = json.dumps(corpo).encode("utf-8")
    # Retry con backoff su rate-limit (429) e errori server transitori (5xx).
    ultimo_errore = None
    for attempt in range(4):
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         cfg.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
            # Estrai il primo blocco di tipo "text" (Sonnet 5 puo' anteporre
            # blocchi "thinking": content[0] non e' garantito essere testo).
            blocchi = data.get("content", [])
            testo = next((b.get("text", "") for b in blocchi if b.get("type") == "text"), "")
            if not testo:
                raise RuntimeError(f"Nessun blocco di testo nella risposta API: {str(data)[:300]}")
            return (prefill or "") + testo.strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            ultimo_errore = f"Anthropic API errore {e.code}: {body[:400]}"
            if e.code == 429 or 500 <= e.code < 600:
                attesa = 2 ** attempt
                log.warning(f"{ultimo_errore} — retry tra {attesa}s ({attempt+1}/4)")
                time.sleep(attesa)
                continue
            raise RuntimeError(ultimo_errore)
        except urllib.error.URLError as e:
            ultimo_errore = f"Anthropic API errore di rete: {e}"
            attesa = 2 ** attempt
            log.warning(f"{ultimo_errore} — retry tra {attesa}s ({attempt+1}/4)")
            time.sleep(attesa)
    raise RuntimeError(ultimo_errore or "Anthropic API: fallito dopo i retry")


def filtra_top_articoli(candidati):
    if len(candidati) <= cfg.ARTICOLI_FINALI:
        return candidati

    # Tetto ai candidati inviati al modello: con molte riviste il prompt cresce in
    # fretta e una lista troppo lunga peggiora la selezione oltre che i costi.
    if len(candidati) > cfg.MAX_CANDIDATI_PROMPT:
        log.warning(
            f"{len(candidati)} candidati: ne invio al filtro i "
            f"{cfg.MAX_CANDIDATI_PROMPT} piu' recenti"
        )
        candidati = sorted(
            candidati,
            key=lambda a: a["pubdate_dt"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:cfg.MAX_CANDIDATI_PROMPT]

    blocchi = []
    for a in candidati:
        blocchi.append(
            f"PMID: {a['pmid']}\n"
            f"RIVISTA: {a['rivista']} ({a['data']})\n"
            f"TITOLO: {a['titolo']}\n"
            f"ABSTRACT: {a['abstract'][:700]}"
        )
    prompt = cfg.PROMPT_FILTRO_RILEVANZA.format(
        n=cfg.ARTICOLI_FINALI,
        articoli="\n\n---\n\n".join(blocchi),
    )
    log.info(f"Claude filtra {len(candidati)} candidati -> max {cfg.ARTICOLI_FINALI}")

    map_pmid = {a["pmid"]: a for a in candidati}
    selezionati = []
    scartati_diversita = []
    conteggio_temi = {}
    try:
        risposta = chiama_claude(
            prompt,
            max_tokens=cfg.MAX_TOKENS_FILTRO,
            system=cfg.SYSTEM_FILTRO,
            prefill="[",
        )
        for voce in _estrai_json_array(risposta):
            if len(selezionati) >= cfg.ARTICOLI_FINALI:
                break
            if not isinstance(voce, dict):
                continue
            pmid   = str(voce.get("pmid", "")).strip()
            tema   = (str(voce.get("tema", "")).strip().lower() or "n/d")
            perche = str(voce.get("perche", "")).strip()

            # Il PMID deve esistere tra i candidati: blocca le allucinazioni.
            if pmid not in map_pmid:
                log.warning(f"    PMID non tra i candidati, ignorato: {pmid!r}")
                continue
            if any(a["pmid"] == pmid for a in selezionati):
                continue
            # Vincolo di diversita' applicato in codice, non solo nel prompt.
            if conteggio_temi.get(tema, 0) >= cfg.MAX_PER_TEMA:
                log.info(f"    [{pmid}] rinviato in riserva: tema '{tema}' gia' saturo")
                scartati_diversita.append(map_pmid[pmid])
                continue

            conteggio_temi[tema] = conteggio_temi.get(tema, 0) + 1
            selezionati.append(map_pmid[pmid])
            log.info(f"    [{pmid}] {tema} -- {perche}")
    except Exception as e:
        log.error(f"Filtro Claude fallito ({e}); si procede con il fallback per data")

    # Il prompt autorizza a restituire meno di ARTICOLI_FINALI se la settimana e'
    # povera: si riempie solo sotto MINIMO_ARTICOLI, e prima con gli articoli messi
    # in riserva dal vincolo di diversita', poi con i piu' recenti.
    if len(selezionati) < cfg.MINIMO_ARTICOLI:
        log.warning(
            f"Solo {len(selezionati)} articoli selezionati (minimo {cfg.MINIMO_ARTICOLI}): "
            "completo con riserva e poi per data"
        )
        gia = {a["pmid"] for a in selezionati}
        riserva = [a for a in scartati_diversita if a["pmid"] not in gia]
        pmid_riserva = {a["pmid"] for a in riserva}
        altri = [
            a for a in candidati
            if a["pmid"] not in gia and a["pmid"] not in pmid_riserva
        ]
        altri.sort(
            key=lambda a: a["pubdate_dt"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for a in riserva + altri:
            if len(selezionati) >= cfg.MINIMO_ARTICOLI:
                break
            selezionati.append(a)
            gia.add(a["pmid"])

    log.info(f"Selezione finale: {len(selezionati)} articoli")
    return selezionati


def _voce_sintesi(voce):
    """Normalizza e valida un oggetto della risposta di sintesi.
    Restituisce (pmid, dati) oppure None se la voce e' inutilizzabile."""
    if not isinstance(voce, dict):
        return None
    pmid = str(voce.get("pmid", "")).strip()
    if not pmid:
        return None
    # Il tipo alimenta un badge HTML: se il modello inventa un valore fuori
    # whitelist lo si azzera, cosi' il badge semplicemente non compare.
    tipo = str(voce.get("tipo", "")).strip().lower()
    if tipo and tipo not in cfg.TIPI_ARTICOLO:
        log.warning(f"    PMID {pmid}: tipo '{tipo}' fuori whitelist, ignorato")
        tipo = ""
    return pmid, {
        "sintesi_it": str(voce.get("sintesi", "")).strip(),
        "rilevanza":  str(voce.get("rilevanza", "")).strip(),
        "limite":     str(voce.get("limite", "")).strip(),
        "tipo":       tipo,
    }


def _sintesi_vuota(art):
    art["sintesi_it"] = art.get("sintesi_it") or ""
    art["rilevanza"]  = art.get("rilevanza")  or ""
    art["limite"]     = art.get("limite")     or ""
    art["tipo"]       = art.get("tipo")       or ""
    return art


def sintetizza_articolo(art):
    """Sintesi di un singolo articolo (fallback)."""
    prompt = cfg.PROMPT_SINTESI.format(
        pmid=art["pmid"],
        titolo=art["titolo"],
        autori=art["autori"],
        rivista=art["rivista"],
        data=art["data"],
        abstract=art["abstract"][:2000] if art["abstract"] else "(non disponibile)",
    )
    try:
        risposta = chiama_claude(
            prompt,
            max_tokens=cfg.MAX_TOKENS_SINTESI_SINGOLA,
            system=cfg.SYSTEM_SINTESI,
            prefill="[",
        )
        voci = _estrai_json_array(risposta)
        esito = _voce_sintesi(voci[0]) if voci else None
        if not esito or not esito[1]["sintesi_it"]:
            raise ValueError("risposta priva di sintesi utilizzabile")
        # Si usa sempre il PMID dell'articolo, non quello riportato dal modello.
        art.update(esito[1])
    except Exception as e:
        log.error(f"Sintesi fallita PMID {art['pmid']}: {e}")
        _sintesi_vuota(art)
    return art


def sintetizza_articoli(articoli):
    """Sintetizza tutti gli articoli in UNA chiamata API.
    Se dalla risposta manca qualche articolo, recupera i mancanti con
    chiamate singole (fallback)."""
    blocchi = []
    for a in articoli:
        blocchi.append(
            f"PMID: {a['pmid']}\n"
            f"Titolo: {a['titolo']}\n"
            f"Autori: {a['autori']}\n"
            f"Rivista: {a['rivista']} ({a['data']})\n"
            f"Abstract: {a['abstract'][:2000] if a['abstract'] else '(non disponibile)'}"
        )
    prompt = cfg.PROMPT_SINTESI_MULTI.format(articoli="\n\n---\n\n".join(blocchi))
    log.info(f"Sintesi unica di {len(articoli)} articoli con Claude...")

    per_pmid = {}
    try:
        risposta = chiama_claude(
            prompt,
            max_tokens=cfg.MAX_TOKENS_SINTESI_MULTI,
            system=cfg.SYSTEM_SINTESI,
            prefill="[",
        )
        for voce in _estrai_json_array(risposta):
            esito = _voce_sintesi(voce)
            if esito and esito[1]["sintesi_it"]:
                per_pmid[esito[0]] = esito[1]
        log.info(f"Sintesi ricevute per {len(per_pmid)}/{len(articoli)} articoli")
    except Exception as e:
        log.error(f"Sintesi multipla fallita: {e}")

    for art in articoli:
        dati = per_pmid.get(art["pmid"])
        if dati:
            art.update(dati)
        else:
            log.warning(f"PMID {art['pmid']} assente dalla sintesi multipla - fallback singolo")
            sintetizza_articolo(art)
            time.sleep(1)
    return articoli


def build_html(articoli):
    wl = numero_settimana()
    arts_html = ""
    for i, a in enumerate(articoli):
        doi_link = (
            f'&nbsp;|&nbsp;<a href="https://doi.org/{esc(a["doi"])}" '
            f'style="font-family:monospace;font-size:11px;color:#0a4d68;text-decoration:none;">&#x2197; DOI</a>'
        ) if a.get("doi") else ""
        meta_tipo = cfg.TIPI_ARTICOLO.get(a.get("tipo") or "")
        badge_html = (
            f'<span style="font-family:monospace;font-size:9px;font-weight:700;'
            f'letter-spacing:1px;text-transform:uppercase;color:#ffffff;'
            f'background:{meta_tipo["colore"]};padding:3px 7px;border-radius:3px;'
            f'margin-left:10px;">{esc(meta_tipo["label"])}</span>'
        ) if meta_tipo else ""
        limite_txt = (a.get("limite") or "").strip()
        limite_html = "" if (not limite_txt or limite_txt == cfg.LIMITE_NON_DESUMIBILE) else f"""
            <div style="font-family:Georgia,serif;font-size:12.5px;color:#6f6152;
                        line-height:1.55;margin:0 0 12px;padding:9px 14px;
                        background:#fbf9f4;border-left:3px solid #c9bda6;">
              <span style="font-family:monospace;font-size:9px;letter-spacing:1.5px;
                           text-transform:uppercase;color:#a08c6b;">Limite</span><br/>
              {esc(limite_txt)}
            </div>"""
        sintesi_html = ""
        if a.get("sintesi_it"):
            rilevanza_html = (
                f'<br/><strong style="color:{cfg.COLOR_ACCENT};">{esc(a["rilevanza"])}</strong>'
                if a.get("rilevanza") else ""
            )
            sintesi_html = f"""
            <div style="background:#f7f4ef;border-left:3px solid {cfg.COLOR_ACCENT};
                        padding:12px 16px;font-family:Georgia,serif;font-size:14px;
                        color:#2a2a2a;line-height:1.6;margin-bottom:12px;">
              {esc(a['sintesi_it'])}{rilevanza_html}
            </div>"""
        abstract_html = ""
        if a.get("abstract"):
            abstract_html = f"""
            <details style="margin-bottom:10px;">
              <summary style="font-family:monospace;font-size:10px;color:#0a4d68;
                             cursor:pointer;letter-spacing:1px;text-transform:uppercase;
                             list-style:none;">&#x25B8; Abstract originale (EN)</summary>
              <p style="font-family:Georgia,serif;font-size:12px;color:#666;
                        line-height:1.65;margin-top:8px;padding:10px 12px;
                        background:#fafafa;border:1px solid #eee;">{esc(a['abstract'])}</p>
            </details>"""
        arts_html += f"""
        <tr>
          <td style="padding:28px 32px 24px;border-bottom:1px solid #e8e3db;">
            <div style="margin-bottom:10px;">
              <span style="font-family:monospace;font-size:12px;color:{cfg.COLOR_ACCENT};font-weight:700;">{str(i+1).zfill(2)}</span>
              <span style="font-family:monospace;font-size:11px;color:#aaa;margin-left:8px;">{esc(a['rivista'])} &middot; {esc(a['data'])}</span>{badge_html}
            </div>
            <a href="{esc(a['url'])}" style="font-family:Georgia,serif;font-size:19px;font-weight:700;
                                        color:#1a1a1a;text-decoration:none;line-height:1.35;
                                        display:block;margin-bottom:6px;">{esc(a['titolo'])}</a>
            <div style="font-family:monospace;font-size:12px;color:#999;font-style:italic;margin-bottom:14px;">{esc(a['autori'])}</div>
            {sintesi_html}
            {limite_html}
            {abstract_html}
            <div>
              <a href="{esc(a['url'])}" style="font-family:monospace;font-size:11px;color:#0a4d68;text-decoration:none;">&#x2197; PubMed {esc(a['pmid'])}</a>
              {doi_link}
            </div>
          </td>
        </tr>"""
    logo_html = (
        f'<img src="{cfg.LOGO_URL}" alt="Pronto Soccorso Area Critica" '
        f'style="display:block;height:84px;width:auto;margin-bottom:14px;'
        f'background:#ffffff;padding:6px 10px;border-radius:6px;" />'
    ) if getattr(cfg, "LOGO_URL", "") else ""
    _nomi = [r["nlmta"] for r in cfg.RIVISTE]
    riviste_str = " &middot; ".join(_nomi[:6])
    if len(_nomi) > 6:
        riviste_str += f" &middot; e altre {len(_nomi) - 6}"
    return f"""<!DOCTYPE html>
<html lang="it">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{esc(cfg.NOME_NEWSLETTER)}</title></head>
<body style="margin:0;padding:0;background:#f0ebe3;">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f0ebe3">
  <tr><td align="center" style="padding:32px 16px;">
    <table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">
      <tr>
        <td style="background:{cfg.COLOR_DARK};padding:0;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="background:{cfg.COLOR_ACCENT};height:4px;"></td></tr>
            <tr>
              <td style="padding:28px 32px 24px;">
                {logo_html}
                <div style="font-family:monospace;font-size:10px;color:#777;letter-spacing:3px;text-transform:uppercase;margin-bottom:8px;">
                  {esc(cfg.NOME_SERVIZIO)}
                </div>
                <h1 style="font-family:Georgia,serif;font-size:32px;color:#ffffff;margin:0 0 6px;font-weight:700;letter-spacing:-0.5px;">
                  Emergency Medicine<br/>
                  <em style="color:{cfg.COLOR_ACCENT};font-style:italic;">Weekly Digest a cura di Francesco Panero </em>
                </h1>
                <div style="font-family:monospace;font-size:11px;color:#666;">
                  Settimana {wl['settimana']} &middot; {wl['giorno']} {wl['mese']} {wl['anno']} &middot; {len(articoli)} articoli
                </div>
              </td>
              <td style="padding:28px 32px 24px;text-align:right;vertical-align:top;">
                <div style="font-family:monospace;font-size:52px;font-weight:700;color:#2a2a2a;letter-spacing:-3px;line-height:1;">
                  {str(wl['settimana']).zfill(2)}
                </div>
                <div style="font-family:monospace;font-size:10px;color:#555;letter-spacing:3px;">WEEK</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
      <tr>
        <td style="background:#f7f4ef;padding:12px 32px;border-bottom:2px solid {cfg.COLOR_DARK};">
          <span style="font-family:monospace;font-size:10px;color:#888;letter-spacing:1px;">{riviste_str}</span>
        </td>
      </tr>
      <tr><td style="background:#ffffff;"><table width="100%" cellpadding="0" cellspacing="0">{arts_html}</table></td></tr>
      <tr>
        <td style="background:{cfg.COLOR_DARK};padding:22px 32px;">
          <p style="font-family:monospace;font-size:10px;color:#555;margin:0;line-height:1.8;">
            Generato con {esc(cfg.ANTHROPIC_MODEL)} (Anthropic) a cura di Francesco Panero &middot; Fonte dati: PubMed RSS feeds<br/>
            Le sintesi sono prodotte da AI e devono essere verificate prima dell'applicazione clinica.<br/>
            Per cancellarsi rispondere con oggetto UNSUBSCRIBE.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table></body></html>"""


def invia_email(oggetto, html_body):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_json = cfg.GMAIL_TOKEN
    if not token_json:
        log.error("GMAIL_TOKEN non trovato nei secrets")
        return False
    token_data = json.loads(token_json)

    creds = Credentials(
        token=token_data['token'],
        refresh_token=token_data['refresh_token'],
        token_uri=token_data['token_uri'],
        client_id=token_data['client_id'],
        client_secret=token_data['client_secret'],
        scopes=token_data['scopes'],
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = oggetto
    msg["From"]    = f"EM Weekly Digest <{cfg.GMAIL_USER}>"
    # Destinatari in Bcc: nessuno vede gli indirizzi degli altri.
    # In To mettiamo il mittente stesso (alcuni client segnalano come spam
    # le email senza alcun To).
    msg["To"]      = cfg.GMAIL_USER
    msg["Bcc"]     = ", ".join(cfg.DESTINATARI)

    msg.attach(MIMEText(f"EM Weekly Digest — {oggetto}\nApri in HTML.", "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        service = build('gmail', 'v1', credentials=creds)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        log.info(f"Email inviata a {len(cfg.DESTINATARI)} destinatari (Bcc)")
        return True
    except Exception as e:
        log.error(f"Invio fallito: {e}")
        return False


def main():
    cfg.valida_config()
    wl = numero_settimana()
    log.info(f"=== EM Weekly Digest — settimana {wl['settimana']}/{wl['anno']} ===")
    log.info(f"=== Destinatari: {len(cfg.DESTINATARI)} (da secret) ===")

    candidati = raccogli_candidati(giorni=cfg.GIORNI_RICERCA)

    if len(candidati) < cfg.ARTICOLI_FINALI + 3:
        log.warning(
            f"Solo {len(candidati)} candidati a {cfg.GIORNI_RICERCA} giorni - "
            f"estendo a {cfg.GIORNI_RICERCA_ESTESO}"
        )
        candidati = raccogli_candidati(giorni=cfg.GIORNI_RICERCA_ESTESO)

    if not candidati:
        log.error("Nessun articolo trovato nemmeno a 14 giorni")
        return False

    selezionati = filtra_top_articoli(candidati)
    log.info(f"Selezionati {len(selezionati)} articoli finali")

    if not selezionati:
        log.error("Filtro non ha selezionato nessun articolo")
        return False

    selezionati = sintetizza_articoli(selezionati)

    # Una newsletter senza sintesi in italiano non va spedita: meglio un run rosso,
    # che si nota e si indaga, di un digest svuotato che erode la fiducia dei lettori.
    con_sintesi = [a for a in selezionati if a.get("sintesi_it")]
    if len(con_sintesi) < cfg.MINIMO_ARTICOLI:
        log.error(
            f"Solo {len(con_sintesi)}/{len(selezionati)} articoli hanno una sintesi "
            f"(minimo {cfg.MINIMO_ARTICOLI}): INVIO ANNULLATO. "
            "Controllare gli errori API qui sopra."
        )
        return False
    selezionati = con_sintesi

    html_body = build_html(selezionati)

    oggetto = f"EM Weekly Digest — Settimana {wl['settimana']}/{wl['anno']}"
    ok = invia_email(oggetto, html_body)
    log.info("=== OK ===" if ok else "=== FALLITO ===")
    return ok


if __name__ == "__main__":
    main()
