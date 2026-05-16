"""
EM Weekly Digest — Versione RSS mono-destinatario
Pronto Soccorso San Giovanni Bosco, Torino

Differenze rispetto a newsletter.py (versione E-utilities):
- Usa RSS feed di PubMed (uniformi per tutte le riviste)
- Codice molto più snello, esecuzione più rapida (~30 secondi vs 2-3 min)
- Fallback automatico a 14 giorni se ci sono meno di 5 articoli rilevanti
- Filtro Opus esclude case report, errata, lettere, corrispondenza, commenti
- Invia SOLO a francesco.panero@aslcittaditorino.it

Comando: python newsletter_rss.py
"""

import os
import re
import sys
import json
import time
import logging
import smtplib
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

import config as cfg

DESTINATARIO = "francesco.panero@aslcittaditorino.it"

# Tipi da SCARTARE (Opus userà questa lista nel prompt di filtro)
TIPI_ESCLUSI = [
    "Case Reports",
    "Letter",
    "Comment",
    "Editorial Comment",
    "Published Erratum",
    "Correction",
    "Retraction",
    "News",
    "Biography",
    "Personal Narrative",
]


# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(cfg.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("newsletter_rss")


# ─── UTILITY ──────────────────────────────────────────────────────────────────

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
    """GET semplice con retry."""
    req = urllib.request.Request(url, headers={"User-Agent": cfg.NCBI_TOOL})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            log.warning(f"Tentativo {attempt+1}/3 fallito: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Fetch fallito: {url}")


# ─── PARSING RSS PUBMED ───────────────────────────────────────────────────────
#
# Struttura tipica di un item RSS PubMed:
#   <item>
#     <title>Titolo articolo</title>
#     <link>https://pubmed.ncbi.nlm.nih.gov/12345678/</link>
#     <description>...PMID:12345678... HTML escapato con abstract...</description>
#     <pubDate>Mon, 06 May 2026 06:00:00 -0400</pubDate>
#     <dc:creator>Smith J</dc:creator>
#     <dc:creator>Jones A</dc:creator>
#     <dc:identifier>doi:10.1056/NEJMoa1234567</dc:identifier>
#   </item>

# Namespace usato nei feed RSS PubMed
NS = {"dc": "http://purl.org/dc/elements/1.1/", "content": "http://purl.org/rss/1.0/modules/content/"}


def url_rss_pubmed(issn):
    """Costruisce URL feed RSS PubMed per una rivista (no leading zeros nell'ISSN)."""
    return f"https://pubmed.ncbi.nlm.nih.gov/rss/journals/{issn}/?limit=20&utm_campaign=journals"


def parse_pubdate(s):
    """Converte la pubDate RSS (RFC822) in datetime."""
    if not s:
        return None
    try:
        # Es: "Mon, 06 May 2026 06:00:00 -0400"
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s)
    except Exception:
        return None


def estrai_abstract_da_description(desc):
    """
    L'RSS PubMed mette l'abstract dentro <description> con HTML.
    Esempio: "<b>BACKGROUND:</b> ...<br/><b>METHODS:</b>...<br/>PMID:12345678"
    """
    if not desc:
        return ""
    # Strip HTML tag
    testo = re.sub(r"<[^>]+>", " ", desc)
    # Unescape entities
    testo = testo.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    # Rimuovi PMID e DOI in coda
    testo = re.sub(r"PMID:\s*\d+.*$", "", testo, flags=re.IGNORECASE)
    testo = re.sub(r"DOI:\s*[\w./-]+", "", testo, flags=re.IGNORECASE)
    # Normalizza whitespace
    testo = re.sub(r"\s+", " ", testo).strip()
    return testo[:2500]


def estrai_pmid(item):
    """Cerca il PMID nel link o nei dc:identifier."""
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
    """Cerca il DOI nei dc:identifier."""
    for ident in item.findall("dc:identifier", NS):
        if ident.text and ident.text.startswith("doi:"):
            return ident.text.replace("doi:", "").strip()
    return ""


def estrai_autori(item):
    """Compone la stringa autori da dc:creator multipli."""
    creators = item.findall("dc:creator", NS)
    nomi = [c.text for c in creators if c.text]
    if not nomi:
        return ""
    if len(nomi) > 3:
        return ", ".join(nomi[:3]) + " et al."
    return ", ".join(nomi)


def fetch_feed(rivista):
    """Scarica e parsifica il feed RSS di una rivista."""
    url = url_rss_pubmed(rivista["issn"])
    try:
        raw = fetch_url(url)
        root = ET.fromstring(raw)
    except Exception as e:
        log.error(f"  {rivista['nlmta']}: errore RSS {e}")
        return []

    articoli = []
    for item in root.findall(".//item"):
        titolo  = (item.findtext("title") or "").strip()
        link    = (item.findtext("link") or "").strip()
        desc    = item.findtext("description") or ""
        pubdate = parse_pubdate(item.findtext("pubDate"))

        pmid    = estrai_pmid(item)
        doi     = estrai_doi(item)
        autori  = estrai_autori(item)
        abstract = estrai_abstract_da_description(desc)

        if not pmid or not titolo:
            continue

        articoli.append({
            "pmid":     pmid,
            "titolo":   titolo.rstrip("."),
            "autori":   autori,
            "rivista":  rivista["nome"],
            "data":     pubdate.strftime("%Y %b %d") if pubdate else "",
            "pubdate_dt": pubdate,
            "doi":      doi,
            "abstract": abstract,
            "url":      link or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })

    log.info(f"  {rivista['nlmta']}: {len(articoli)} articoli dal feed")
    return articoli


def raccogli_candidati(giorni=7):
    """Scarica tutti i feed, filtra per data degli ultimi N giorni."""
    log.info(f"Lettura RSS PubMed: ultimi {giorni} giorni su {len(cfg.RIVISTE)} riviste")

    cutoff = datetime.now(timezone.utc) - timedelta(days=giorni)
    tutti = []

    for rivista in cfg.RIVISTE:
        feed = fetch_feed(rivista)
        # Filtra per data
        recenti = [
            a for a in feed
            if a["pubdate_dt"] and a["pubdate_dt"].astimezone(timezone.utc) >= cutoff
        ]
        log.info(f"    → {len(recenti)} pubblicati negli ultimi {giorni}g")
        tutti.extend(recenti)
        time.sleep(0.3)

    # Deduplica per PMID
    seen = set()
    unici = []
    for a in tutti:
        if a["pmid"] not in seen:
            seen.add(a["pmid"])
            unici.append(a)

    # Tieni solo quelli con abstract (Opus deve poter giudicare)
    con_abstract = [a for a in unici if a["abstract"] and len(a["abstract"]) > 100]
    log.info(f"Totale unici: {len(unici)}, con abstract: {len(con_abstract)}")
    return con_abstract


# ─── CLAUDE OPUS ──────────────────────────────────────────────────────────────

def chiama_opus(prompt, max_tokens=1500):
    payload = json.dumps({
        "model":      cfg.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages":   [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": cfg.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["content"][0]["text"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"Anthropic API errore {e.code}: {body[:400]}")


PROMPT_FILTRO_RSS = """Sei un medico di Pronto Soccorso italiano.

Dalla lista qui sotto, seleziona i 5 articoli più rilevanti per la pratica clinica
in Pronto Soccorso, Medicina d'Urgenza, Rianimazione e Terapia Intensiva.

ESCLUDI TASSATIVAMENTE questi tipi di pubblicazione (riconoscibili dal titolo o dall'abstract):
- Case reports e case series singoli
- Letters to the editor, lettere, corrispondenza
- Comments, editorial comments su altri studi
- Errata, correzioni, retraction
- News, biografie, narrative personali

PRIVILEGIA:
- Trial clinici e studi originali su patologie d'urgenza (sepsi, trauma, ACS, stroke, ARDS, etc.)
- Meta-analisi e revisioni sistematiche
- Linee guida e position paper su gestione acuta
- Editoriali che presentano novità per la pratica acuta
- Aggiornamenti su rianimazione cardiopolmonare e cure critiche

ESCLUDI temi non pertinenti al PS:
- Cardiologia interventistica pura (non d'urgenza)
- Chirurgia elettiva
- Oncologia ambulatoriale
- Endocrinologia cronica
- Ricerca di base senza implicazioni cliniche immediate

ARTICOLI CANDIDATI:
{articoli}

Restituisci SOLO una lista di 5 PMID, uno per riga, in ordine di rilevanza decrescente.
Nessun commento, nessuna spiegazione, solo i 5 PMID.

Esempio output:
12345678
23456789
34567890
45678901
56789012"""


def filtra_top_articoli(candidati):
    if len(candidati) <= cfg.ARTICOLI_FINALI:
        log.info(f"Solo {len(candidati)} candidati: skip filtro")
        return candidati

    blocchi = []
    for a in candidati:
        blocchi.append(
            f"PMID: {a['pmid']}\n"
            f"RIVISTA: {a['rivista']} ({a['data']})\n"
            f"TITOLO: {a['titolo']}\n"
            f"ABSTRACT: {a['abstract'][:700]}"
        )
    prompt = PROMPT_FILTRO_RSS.format(articoli="\n\n---\n\n".join(blocchi))
    log.info(f"Opus filtra {len(candidati)} → {cfg.ARTICOLI_FINALI}")
    risposta = chiama_opus(prompt, max_tokens=200)
    pmids_sel = re.findall(r"\b\d{7,9}\b", risposta)[:cfg.ARTICOLI_FINALI]
    log.info(f"Opus selezionati: {pmids_sel}")
    map_pmid = {a["pmid"]: a for a in candidati}
    selezionati = [map_pmid[p] for p in pmids_sel if p in map_pmid]
    return selezionati


def sintetizza_articolo(art):
    prompt = cfg.PROMPT_SINTESI.format(
        titolo=art["titolo"],
        autori=art["autori"],
        rivista=art["rivista"],
        data=art["data"],
        tipo="(non disponibile dal feed RSS)",
        abstract=art["abstract"][:2000] if art["abstract"] else "(abstract non disponibile)",
    )
    try:
        risposta = chiama_opus(prompt, max_tokens=600)
        sintesi_m   = re.search(r"^SINTESI:\s*([\s\S]+?)(?=\nRILEVANZA:)", risposta, re.MULTILINE)
        rilevanza_m = re.search(r"^RILEVANZA:\s*(.+)", risposta, re.MULTILINE)
        art["sintesi_it"] = sintesi_m.group(1).strip() if sintesi_m else risposta[:400]
        art["rilevanza"]  = rilevanza_m.group(1).strip() if rilevanza_m else ""
    except Exception as e:
        log.error(f"Sintesi fallita PMID {art['pmid']}: {e}")
        art["sintesi_it"] = ""
        art["rilevanza"]  = ""
    return art


# ─── HTML ─────────────────────────────────────────────────────────────────────

def build_html(articoli):
    wl = numero_settimana()
    arts_html = ""
    for i, a in enumerate(articoli):
        doi_link = (
            f'&nbsp;|&nbsp;<a href="https://doi.org/{a["doi"]}" '
            f'style="font-family:monospace;font-size:11px;color:#0a4d68;text-decoration:none;">&#x2197; DOI</a>'
        ) if a.get("doi") else ""

        sintesi_html = ""
        if a.get("sintesi_it"):
            rilevanza_html = (
                f'<br/><strong style="color:{cfg.COLOR_ACCENT};">{a["rilevanza"]}</strong>'
                if a.get("rilevanza") else ""
            )
            sintesi_html = f"""
            <div style="background:#f7f4ef;border-left:3px solid {cfg.COLOR_ACCENT};
                        padding:12px 16px;font-family:Georgia,serif;font-size:14px;
                        color:#2a2a2a;line-height:1.6;margin-bottom:12px;">
              {a['sintesi_it']}{rilevanza_html}
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
                        background:#fafafa;border:1px solid #eee;">{a['abstract']}</p>
            </details>"""

        arts_html += f"""
        <tr>
          <td style="padding:28px 32px 24px;border-bottom:1px solid #e8e3db;">
            <div style="margin-bottom:10px;">
              <span style="font-family:monospace;font-size:12px;color:{cfg.COLOR_ACCENT};font-weight:700;">{str(i+1).zfill(2)}</span>
              <span style="font-family:monospace;font-size:11px;color:#aaa;margin-left:8px;">{a['rivista']} &middot; {a['data']}</span>
            </div>
            <a href="{a['url']}" style="font-family:Georgia,serif;font-size:19px;font-weight:700;
                                        color:#1a1a1a;text-decoration:none;line-height:1.35;
                                        display:block;margin-bottom:6px;">{a['titolo']}</a>
            <div style="font-family:monospace;font-size:12px;color:#999;font-style:italic;margin-bottom:14px;">{a['autori']}</div>
            {sintesi_html}
            {abstract_html}
            <div>
              <a href="{a['url']}" style="font-family:monospace;font-size:11px;color:#0a4d68;text-decoration:none;">&#x2197; PubMed {a['pmid']}</a>
              {doi_link}
            </div>
          </td>
        </tr>"""

    riviste_str = " &middot; ".join(r["nlmta"] for r in cfg.RIVISTE[:6]) + " &middot; e altre 6"

    return f"""<!DOCTYPE html>
<html lang="it">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{cfg.NOME_NEWSLETTER}</title></head>
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
                <div style="font-family:monospace;font-size:10px;color:#777;letter-spacing:3px;text-transform:uppercase;margin-bottom:8px;">
                  {cfg.NOME_SERVIZIO}
                </div>
                <h1 style="font-family:Georgia,serif;font-size:32px;color:#ffffff;margin:0 0 6px;font-weight:700;letter-spacing:-0.5px;">
                  Emergency Medicine<br/>
                  <em style="color:{cfg.COLOR_ACCENT};font-style:italic;">Weekly Digest</em>
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
            Generato con Claude Opus 4.5 (Anthropic) &middot; Fonte dati: PubMed RSS feeds<br/>
            Le sintesi sono prodotte da AI e devono essere verificate prima dell'applicazione clinica.<br/>
            <strong style="color:#888;">VERSIONE TEST &mdash; invio singolo a {DESTINATARIO}</strong>
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table></body></html>"""


# ─── EMAIL ────────────────────────────────────────────────────────────────────

def invia_email(oggetto, html):
    import base64
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    # Carica token
    token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gmail_token.json')
    with open(token_file) as f:
        token_data = json.load(f)

    creds = Credentials(
        token=token_data['token'],
        refresh_token=token_data['refresh_token'],
        token_uri=token_data['token_uri'],
        client_id=token_data['client_id'],
        client_secret=token_data['client_secret'],
        scopes=token_data['scopes'],
    )

    # Costruisci messaggio
    msg = MIMEMultipart("alternative")
    msg["Subject"] = oggetto
    msg["From"]    = f"EM Weekly Digest <{cfg.GMAIL_USER}>"
    msg["To"]      = DESTINATARIO
    msg.attach(MIMEText(f"EM Weekly Digest — {oggetto}\nApri in HTML.", "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    # Invia via Gmail API
    try:
        service = build('gmail', 'v1', credentials=creds)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        log.info("Email inviata via Gmail API")
        return True
    except Exception as e:
        log.error(f"Invio fallito: {e}")
        return False


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    cfg.valida_config()
    wl = numero_settimana()
    log.info(f"=== EM Weekly Digest RSS TEST — settimana {wl['settimana']}/{wl['anno']} ===")
    log.info(f"=== Destinatario unico: {DESTINATARIO} ===")

    # 1. Raccolta candidati - prima passata 7 giorni
    candidati = raccogli_candidati(giorni=7)

    # 2. FALLBACK automatico a 14 giorni se troppo pochi
    if len(candidati) < cfg.ARTICOLI_FINALI + 3:  # serve un buffer per il filtro
        log.warning(f"Solo {len(candidati)} candidati a 7 giorni — estendo a 14 giorni")
        candidati = raccogli_candidati(giorni=14)

    if not candidati:
        log.error("Nessun articolo trovato nemmeno a 14 giorni")
        return False

    # 3. Filtro rilevanza Opus (esclude case report, lettere, etc.)
    selezionati = filtra_top_articoli(candidati)
    log.info(f"Selezionati {len(selezionati)} articoli finali")

    if not selezionati:
        log.error("Filtro Opus non ha selezionato nessun articolo")
        return False

    # 4. Sintesi italiana
    log.info("Sintesi con Claude Opus…")
    for i, art in enumerate(selezionati):
        log.info(f"  Sintesi {i+1}/{len(selezionati)}: PMID {art['pmid']}")
        selezionati[i] = sintetizza_articolo(art)
        time.sleep(1)

    # 5. HTML
    html = build_html(selezionati)
    filename = f"digest_rss_settimana_{wl['settimana']}_{wl['anno']}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Copia locale: {filename}")

    # 6. Invio
    oggetto = f"EM Weekly Digest — Settimana {wl['settimana']}/{wl['anno']}"
    ok = invia_email(oggetto, html)
    log.info("=== OK ===" if ok else "=== FALLITO ===")
    return ok


if __name__ == "__main__":
    main()
