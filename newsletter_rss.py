"""
EM Weekly Digest — Versione con lista destinatari (da secret DESTINATARI)
Pronto Soccorso San Giovanni Bosco, Torino

Pipeline: RSS (+ confronto/sostituzione con ESearch) -> efetch -> pre-filtro
-> screening -> graduatoria -> sintesi di tutti gli articoli in UNA chiamata
-> badge e lint -> HTML/PNG/email/Facebook -> archivio.
3 chiamate API a settimana. I comportamenti si accendono e spengono da config.py.
Il podcast settimanale e il post Facebook del lunedi' sono in podcast.py.

Comando: python newsletter_rss.py
"""

import re
import json
import time
import html
import logging
from collections import Counter
from zoneinfo import ZoneInfo
import uuid
import pathlib
import tempfile
import shutil
import os
import sys
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.audio import MIMEAudio
from email.mime.base import MIMEBase
from email import encoders
import io
from datetime import datetime, timedelta, timezone, time as dt_time

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


# Campi ammessi negli oggetti JSON restituiti dal modello (filtro e sintesi).
# Servono al recupero per oggetto: tenerli allineati con i prompt di config.py.
_CHIAVI_JSON = ("pmid", "sintesi", "rilevanza", "limite", "tipo",
                "tema", "categoria", "perche")
_K = "|".join(_CHIAVI_JSON)
_RE_INIZIO_OGGETTO = re.compile(r'\{\s*"(?:' + _K + r')"\s*:')
_RE_CAMPO = re.compile(r'(?:^|[{,]|(?<="))\s*"(' + _K + r')"\s*:\s*')


def _ripara_oggetto(blocco):
    """Ricostruisce un oggetto JSON le cui stringhe contengono virgolette doppie
    non escapate (es. un nome di trial tra virgolette). Il confine tra un campo e
    il successivo e' la chiave nota che segue, non la virgoletta."""
    corpo = blocco.strip()[1:-1]
    campi = list(_RE_CAMPO.finditer(corpo))
    if not campi:
        return None
    ob = {}
    for k, m in enumerate(campi):
        fine = campi[k + 1].start() if k + 1 < len(campi) else len(corpo)
        valore = corpo[m.end():fine].strip().rstrip(",").rstrip()
        try:
            if len(valore) >= 2 and valore[0] == '"' and valore[-1] == '"':
                interno = re.sub(r'(?<!\\)"', r'\\"', valore[1:-1])
                ob[m.group(1)] = json.loads('"' + interno + '"', strict=False)
            else:
                ob[m.group(1)] = json.loads(valore)
        except ValueError:
            return None
    return ob


def _recupera_oggetti(t):
    """Recupero per oggetto da un array JSON non valido. Gli oggetti troncati
    (risposta tagliata da max_tokens) vengono scartati: l'articolo mancante
    verra' rifatto dal fallback singolo, gli altri si salvano."""
    inizi = [m.start() for m in _RE_INIZIO_OGGETTO.finditer(t)]
    oggetti = []
    for i, a in enumerate(inizi):
        b = inizi[i + 1] if i + 1 < len(inizi) else len(t)
        blocco = re.sub(r'[\s,\]]+$', '', t[a:b])
        if not blocco.endswith("}"):
            continue
        try:
            oggetti.append(json.loads(blocco, strict=False))
        except ValueError:
            ob = _ripara_oggetto(blocco)
            if ob:
                oggetti.append(ob)
    return oggetti


def _estrai_json_array(testo):
    """Estrae il primo array JSON dalla risposta, tollerando i fence markdown.
    Se il JSON non e' valido (tipicamente virgolette doppie non escapate in un
    testo) non scarta l'intera risposta: logga il punto d'errore e recupera gli
    oggetti uno per uno, cosi' il fallback singolo scatta solo per quelli davvero
    irrecuperabili invece che per tutti."""
    t = re.sub(r"^```(?:json)?\s*", "", testo.strip())
    t = re.sub(r"\s*```$", "", t)
    inizio, fine = t.find("["), t.rfind("]")
    if inizio == -1:
        raise ValueError(f"Nessun array JSON nella risposta: {t[:200]}")
    candidato = t[inizio:fine + 1] if fine > inizio else t[inizio:]
    try:
        return json.loads(candidato)
    except json.JSONDecodeError as e:
        a = max(0, e.pos - 150)
        log.warning(
            f"JSON non valido ({e.msg}, posizione {e.pos} di {len(candidato)}): "
            f"...{candidato[a:e.pos + 150]!r}..."
        )
        recuperati = _recupera_oggetti(t[inizio:])
        if not recuperati:
            raise
        log.warning(f"    recuperati {len(recuperati)} oggetti dalla risposta non valida")
        return recuperati


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
        root = ET.fromstring(fetch_url(url))
    except Exception as e:
        log.error(f"  {rivista['nlmta']}: errore RSS {e}")
        return []

    # Un feed senza <item> arriva come HTTP 200 con XML valido: fetch_url non
    # solleva nulla e non ritenta. PubMed pero' restituisce feed vuoti anche in
    # modo transitorio (il 26/08/2026 quattro riviste consolidate insieme), per
    # cui vale un secondo tentativo prima di darla per persa.
    if not root.findall(".//item"):
        time.sleep(3)
        try:
            root = ET.fromstring(fetch_url(url))
            if root.findall(".//item"):
                log.info(f"  {rivista['nlmta']}: feed vuoto al primo tentativo, "
                         "recuperato al secondo")
        except Exception as e:
            log.warning(f"  {rivista['nlmta']}: secondo tentativo fallito ({e})")
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
            "gruppo":     rivista.get("gruppo", "spec"),
            "data":       pubdate.strftime("%Y %b %d") if pubdate else "",
            "pubdate_dt": pubdate,
            "doi":        doi,
            "abstract":   abstract,
            "url":        link or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    if not articoli:
        # Due cause possibili, e il messaggio non deve suggerirne una sola:
        # un ISSN errato (feed sempre vuoto) o un'indisponibilita' temporanea di
        # PubMed (feed vuoto solo oggi). Se la rivista ha prodotto articoli nelle
        # settimane precedenti, l'ISSN e' giusto e si tratta della seconda.
        log.error(
            f"  {rivista['nlmta']}: FEED VUOTO (ISSN {rivista['issn']}). "
            "Se la rivista funzionava nei run precedenti e' un'indisponibilita' "
            "temporanea di PubMed; se non ha mai prodotto articoli, verificare "
            "l'ISSN (per le riviste solo online provare l'eISSN)."
        )
    else:
        log.info(f"  {rivista['nlmta']}: {len(articoli)} articoli dal feed")
    return articoli


def _parse_abstract(art_el):
    """Ricompone l'abstract, che PubMed espone spesso in sezioni etichettate."""
    parti = []
    for el in art_el.findall("./Abstract/AbstractText"):
        testo = "".join(el.itertext()).strip()
        if not testo:
            continue
        etichetta = (el.get("Label") or "").strip()
        parti.append(f"{etichetta}: {testo}" if etichetta else testo)
    return re.sub(r"\s+", " ", " ".join(parti)).strip()


_MESI_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _mese_numero(m):
    if not m:
        return None
    m = m.strip()
    if m.isdigit():
        return int(m) if 1 <= int(m) <= 12 else None
    for i, ab in enumerate(_MESI_ABBR, 1):
        if m[:3].lower() == ab.lower():
            return i
    return None


def _data_articolo(art_el):
    """(testo, datetime) dalla data elettronica dell'articolo o, in mancanza, da quella
    del fascicolo. Il testo ha lo stesso formato dell'RSS ("2026 Oct 05")."""
    y = m = d = None
    ad = art_el.find("./ArticleDate")
    if ad is not None:
        y, m, d = ad.findtext("Year"), ad.findtext("Month"), ad.findtext("Day")
    if not y:
        pd = art_el.find("./Journal/JournalIssue/PubDate")
        if pd is not None:
            y, m, d = pd.findtext("Year"), pd.findtext("Month"), pd.findtext("Day")
    if not y or not y.isdigit():
        return "", None
    mese = _mese_numero(m)
    giorno = int(d) if d and d.isdigit() else None
    testo = y
    if mese:
        testo += " " + _MESI_ABBR[mese - 1]
        if giorno:
            testo += f" {giorno:02d}"
    try:
        dt = datetime(int(y), mese or 1, giorno or 1, tzinfo=timezone.utc)
    except ValueError:
        dt = None
    return testo, dt


def _autori_efetch(art_el):
    nomi = []
    for au in art_el.findall("./AuthorList/Author"):
        coll = au.findtext("CollectiveName")
        if coll:
            nomi.append(coll.strip())
            continue
        cognome = (au.findtext("LastName") or "").strip()
        iniziali = (au.findtext("Initials") or "").strip()
        if cognome:
            nomi.append(f"{cognome} {iniziali}".strip())
    if not nomi:
        return ""
    return ", ".join(nomi[:3]) + (" et al." if len(nomi) > 3 else "")


def _efetch_lotto(pmids):
    """Una richiesta efetch per un lotto di PMID. Solleva se non riesce.
    Restituisce, per PMID: abstract, pubtypes e i metadati che servono a costruire
    un articolo anche quando non viene dall'RSS (titolo, autori, rivista, data, doi)."""
    campi = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": cfg.NCBI_TOOL,
    }
    if cfg.NCBI_EMAIL:
        campi["email"] = cfg.NCBI_EMAIL
    req = urllib.request.Request(
        cfg.EFETCH_URL,
        data=urllib.parse.urlencode(campi).encode("utf-8"),
        headers={"User-Agent": f"{cfg.NCBI_TOOL} (PubMed digest)"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg.EFETCH_TIMEOUT) as r:
        xml = r.read()

    dettagli = {}
    radice = ET.fromstring(xml)
    for art in radice.findall(".//PubmedArticle"):
        pmid_el = art.find("./MedlineCitation/PMID")
        art_el = art.find("./MedlineCitation/Article")
        if pmid_el is None or art_el is None or not pmid_el.text:
            continue
        tipi = [
            (t.text or "").strip()
            for t in art_el.findall("./PublicationTypeList/PublicationType")
        ]
        titolo_el = art_el.find("./ArticleTitle")
        titolo = ("".join(titolo_el.itertext()).strip().rstrip(".")
                  if titolo_el is not None else "")
        doi = ""
        for aid in art.findall("./PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi" and aid.text:
                doi = aid.text.strip()
                break
        data_txt, data_dt = _data_articolo(art_el)
        dettagli[pmid_el.text.strip()] = {
            "abstract": _parse_abstract(art_el),
            "pubtypes": [t for t in tipi if t],
            "titolo": titolo,
            "autori": _autori_efetch(art_el),
            "nlmta": (art.findtext("./MedlineCitation/MedlineJournalInfo/MedlineTA") or "").strip(),
            "data": data_txt,
            "pubdate_dt": data_dt,
            "doi": doi,
        }
    return dettagli


def _efetch_tutti(pmids):
    """efetch a lotti con retry. In caso di errore persistente degrada in silenzio:
    restituisce quello che e' riuscito a recuperare."""
    dettagli = {}
    for i in range(0, len(pmids), cfg.EFETCH_BATCH):
        lotto = pmids[i:i + cfg.EFETCH_BATCH]
        for tentativo in range(cfg.EFETCH_RETRY + 1):
            try:
                dettagli.update(_efetch_lotto(lotto))
                break
            except Exception as e:
                ultimo = tentativo == cfg.EFETCH_RETRY
                log.warning(
                    f"efetch lotto {i // cfg.EFETCH_BATCH + 1} "
                    f"tentativo {tentativo + 1}/{cfg.EFETCH_RETRY + 1} fallito: {e}"
                    + ("" if ultimo else " - riprovo")
                )
                if ultimo:
                    log.error("efetch non disponibile: proseguo con i dati disponibili")
                else:
                    time.sleep(2 * (tentativo + 1))
        time.sleep(0.4)   # NCBI: max 3 richieste/secondo senza chiave API
    return dettagli


def arricchisci_con_efetch(articoli):
    """Sostituisce l'abstract del feed con quello vero e aggiunge i PublicationType.
    In caso di errore degrada in silenzio: gli articoli restano com'erano e il
    filtro sui titoli fa da rete di sicurezza."""
    pmids = [a["pmid"] for a in articoli if a.get("pmid")]
    if not pmids:
        return articoli

    dettagli = _efetch_tutti(pmids)

    recuperati = 0
    for a in articoli:
        d = dettagli.get(a["pmid"])
        if not d:
            continue
        a["pubtypes"] = d["pubtypes"]
        # Si sostituisce solo se efetch porta piu' testo: mai un peggioramento.
        if len(d["abstract"]) > len(a.get("abstract") or ""):
            if len(a.get("abstract") or "") < cfg.ABSTRACT_MIN_CHARS <= len(d["abstract"]):
                recuperati += 1
            a["abstract"] = d["abstract"]

    log.info(
        f"efetch: dettagli per {len(dettagli)}/{len(pmids)} PMID, "
        f"{recuperati} articoli recuperati con abstract prima assente"
    )
    return articoli


def escluso_per_pubtype(articolo):
    """(True, tipo) se un PublicationType e' nella lista di esclusione."""
    for t in articolo.get("pubtypes") or []:
        if t in cfg.PUBTYPE_ESCLUSI:
            return True, t
    return False, ""


CONTESTO_RUN = {}   # dati della corsa che finiscono nell'archivio
_PER_NLMTA = {r["nlmta"].lower(): r for r in cfg.RIVISTE}


def _articoli_da_rss(giorni):
    """Articoli unici dei feed RSS pubblicati negli ultimi `giorni`."""
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
        # Feed pieno e tutto recente = molto probabilmente troncato dal limit.
        limite = rivista.get("limit") or cfg.RSS_LIMIT_DEFAULT
        if feed and len(recenti) >= limite:
            log.warning(
                f"    {rivista['nlmta']}: {len(recenti)} recenti su {limite} nel feed: "
                "probabile troncamento, alzare il campo 'limit' in config.RIVISTE"
            )
        tutti.extend(recenti)
        time.sleep(0.3)
    seen = set()
    unici = []
    for a in tutti:
        if a["pmid"] not in seen:
            seen.add(a["pmid"])
            unici.append(a)
    return unici


def esearch_pmids(giorni):
    """PMID delle riviste target inseriti in PubMed negli ultimi `giorni`, con i
    pubtype indesiderati gia' esclusi dalla query. Solleva se non riesce."""
    campi = cfg.parametri_esearch(giorni)
    ultimo = None
    for tentativo in range(3):
        try:
            req = urllib.request.Request(
                cfg.ESEARCH_URL,
                data=urllib.parse.urlencode(campi).encode("utf-8"),
                headers={"User-Agent": f"{cfg.NCBI_TOOL} (PubMed digest)"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=cfg.ESEARCH_TIMEOUT) as r:
                dati = json.loads(r.read().decode("utf-8"))
            res = dati.get("esearchresult", {})
            if res.get("ERROR"):
                raise RuntimeError(res["ERROR"])
            # Una rivista non riconosciuta non da' errore: finisce qui, e va vista.
            if res.get("errorlist") or res.get("warninglist"):
                log.warning(f"ESearch: avvisi di PubMed {res.get('errorlist') or ''} "
                            f"{res.get('warninglist') or ''}")
            ids = [str(x) for x in res.get("idlist", [])]
            totale = int(res.get("count", len(ids)))
            if totale > len(ids):
                log.warning(f"ESearch: {totale} risultati ma retmax={cfg.ESEARCH_RETMAX}: "
                            "alzare ESEARCH_RETMAX")
            return ids
        except Exception as e:
            ultimo = e
            log.warning(f"ESearch tentativo {tentativo + 1}/3 fallito: {e}")
            time.sleep(2 * (tentativo + 1))
    raise RuntimeError(f"ESearch fallita: {ultimo}")


def _articolo_da_dettagli(pmid, d):
    """Articolo completo a partire dai soli dati efetch (percorso ESearch)."""
    riv = _PER_NLMTA.get((d.get("nlmta") or "").lower())
    if not riv or not d.get("titolo"):
        return None
    return {
        "pmid":       pmid,
        "titolo":     d["titolo"],
        "autori":     d.get("autori", ""),
        "rivista":    riv["nome"],
        "gruppo":     riv.get("gruppo", "spec"),
        "data":       d.get("data", ""),
        "pubdate_dt": d.get("pubdate_dt"),
        "doi":        d.get("doi", ""),
        "abstract":   d.get("abstract", ""),
        "pubtypes":   d.get("pubtypes", []),
        "url":        f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "da_esearch": True,
    }


def confronta_con_esearch(unici_rss, giorni):
    """Modalita' 'confronto': l'RSS resta la fonte, ma si logga cosa ha perso rispetto
    a una ricerca diretta. Non blocca mai la pipeline."""
    try:
        ids = esearch_pmids(giorni)
        rss = {a["pmid"] for a in unici_rss}
        persi = [p for p in ids if p not in rss]
        log.info(
            f"ESearch (edat ultimi {giorni}g): {len(ids)} PMID, {len(ids) - len(persi)} "
            f"gia' nell'RSS, {len(persi)} PERSI dall'RSS"
        )
        dettaglio = _efetch_tutti(persi) if persi else {}
        per_rivista = Counter(
            (dettaglio.get(p, {}).get("nlmta") or "?") for p in persi
        )
        if per_rivista:
            log.info("ESearch - persi dall'RSS per rivista: " + ", ".join(
                f"{k}={v}" for k, v in per_rivista.most_common()))
        for p in persi[:40]:
            d = dettaglio.get(p, {})
            log.info(f"    [persi RSS] {p} {d.get('nlmta', '?')}: "
                     f"{(d.get('titolo') or '')[:90]}")
        CONTESTO_RUN["esearch"] = {
            "modo": "confronto", "pmid_esearch": len(ids), "persi_dall_rss": len(persi),
            "persi_per_rivista": dict(per_rivista),
        }
    except Exception as e:
        log.warning(f"Confronto con ESearch non riuscito: {e}")


def raccogli_candidati(giorni=None):
    giorni = giorni or cfg.GIORNI_RICERCA
    modo = getattr(cfg, "ESEARCH_MODO", "off")
    unici = None

    if modo == "sostituisci":
        try:
            ids = esearch_pmids(giorni)
            dettagli = _efetch_tutti(ids)
            unici = [x for x in (_articolo_da_dettagli(p, dettagli[p])
                                 for p in ids if p in dettagli) if x]
            log.info(f"ESearch: {len(ids)} PMID -> {len(unici)} articoli utilizzabili")
            if not unici:
                raise RuntimeError("nessun articolo utilizzabile")
            CONTESTO_RUN["esearch"] = {"modo": "sostituisci", "pmid_esearch": len(ids)}
        except Exception as e:
            log.error(f"ESearch non utilizzabile ({e}): ripiego sull'RSS")
            unici = None

    if unici is None:
        unici = _articoli_da_rss(giorni)
        if modo == "confronto":
            confronta_con_esearch(unici, giorni)
        # Abstract veri e PublicationType da E-utilities, prima di filtrare.
        unici = arricchisci_con_efetch(unici)

    candidati = []
    scartati_pubtype = scartati_titolo = scartati_abstract = scartati_protocollo = 0
    for a in unici:
        # 1. PublicationType: criterio primario, etichette ufficiali di PubMed.
        escluso, tipo = escluso_per_pubtype(a)
        if escluso:
            scartati_pubtype += 1
            log.info(f"    [scarto/pubtype {tipo}] {a['titolo'][:80]}")
            continue
        # 2. Titolo: rete di sicurezza per i record su cui efetch non ha risposto.
        if escluso_per_titolo(a["titolo"]):
            scartati_titolo += 1
            log.info(f"    [scarto/titolo] {a['titolo'][:90]}")
            continue
        if not a["abstract"] or len(a["abstract"]) < cfg.ABSTRACT_MIN_CHARS:
            scartati_abstract += 1
            log.info(f"    [scarto/abstract {len(a['abstract'] or '')}c] {a['titolo'][:90]}")
            continue
        # 3. Disegno dal testo: il PublicationType RCT arriva settimane dopo la
        #    pubblicazione, sugli articoli freschi c'e' solo "Journal Article".
        a["disegno"], a["disegno_fonte"] = cfg.rileva_disegno(
            a["titolo"], a["abstract"], a.get("pubtypes"))
        if a["disegno"] in cfg.DISEGNI_DA_ESCLUDERE:
            scartati_protocollo += 1
            log.info(f"    [scarto/{a['disegno']}] {a['titolo'][:90]}")
            continue
        candidati.append(a)

    log.info(
        f"Unici {len(unici)} -> scartati {scartati_pubtype} per pubtype, "
        f"{scartati_titolo} per titolo, {scartati_abstract} per abstract, "
        f"{scartati_protocollo} protocolli -> {len(candidati)} candidati"
    )
    conteggio = Counter(a["disegno"] for a in candidati)
    log.info("Disegni dei candidati: " + ", ".join(
        f"{cfg.ETICHETTA_DISEGNO.get(k, k)}={v}" for k, v in conteggio.most_common()))
    return candidati


USO_API = {"chiamate": 0, "token_in": 0, "token_out": 0}


def chiama_claude(prompt, max_tokens=1500, system=None, thinking=False, model=None):
    # NB: NON passare "temperature" e NON far terminare la conversazione con un
    # turno "assistant": questo modello rifiuta entrambi con HTTP 400 (verificato
    # il 03/08/2026). Il formato JSON e' garantito dal prompt e da
    # _estrai_json_array, che tollera fence markdown e testo di contorno.
    messaggi = [{"role": "user", "content": prompt}]
    corpo = {
        "model":      model or cfg.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages":   messaggi,
    }
    if not thinking:
        # Sonnet 5 ha l'adaptive thinking attivo di default: lo disattiviamo,
        # cosi' la risposta e' solo testo e max_tokens non viene speso in thinking.
        corpo["thinking"] = {"type": "disabled"}
    # Con thinking=True la chiave si omette: il ragionamento adattivo resta attivo e
    # max_tokens deve lasciargli spazio (MAX_TOKENS_FILTRO_FINALE).
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
            uso = data.get("usage") or {}
            USO_API["chiamate"] += 1
            USO_API["token_in"] += int(uso.get("input_tokens") or 0)
            USO_API["token_out"] += int(uso.get("output_tokens") or 0)
            # Estrai il primo blocco di tipo "text" (Sonnet 5 puo' anteporre
            # blocchi "thinking": content[0] non e' garantito essere testo).
            blocchi = data.get("content", [])
            testo = next((b.get("text", "") for b in blocchi if b.get("type") == "text"), "")
            if not testo:
                raise RuntimeError(
                    f"Nessun blocco di testo nella risposta API (stop_reason={data.get('stop_reason')}, "
                    f"blocchi={[b.get('type') for b in blocchi]}, token_out={uso.get('output_tokens')})")
            return testo.strip()
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


def componi_digest(graduatoria):
    """Dai fino ad ARTICOLI_RICHIESTI in graduatoria estrae ARTICOLI_FINALI,
    garantendo almeno MIN_EM_GEN articoli da riviste di urgenza o generaliste.
    Gli scambi avvengono solo fra articoli gia' selezionati dal modello: non si
    reintroduce mai materiale non vagliato."""
    scelti = graduatoria[:cfg.ARTICOLI_FINALI]
    panchina = graduatoria[cfg.ARTICOLI_FINALI:]

    def prioritario(a):
        return a.get("gruppo") in cfg.GRUPPI_PRIORITARI

    mancanti = cfg.MIN_EM_GEN - sum(1 for a in scelti if prioritario(a))
    while mancanti > 0:
        # candidato in entrata: il primo em/gen in panchina (ordine = rilevanza)
        entra = next((a for a in panchina if prioritario(a)), None)
        # candidato in uscita: lo specialistico peggio piazzato fra i scelti
        esce = next((a for a in reversed(scelti) if not prioritario(a)), None)
        if entra is None or esce is None:
            log.warning(
                f"Quota riviste urgenza/generaliste non raggiunta: mancano "
                f"{mancanti} - nessuno scambio possibile in graduatoria"
            )
            break
        log.info(
            f"    scambio per quota AREA: entra {entra['pmid']} ({entra['rivista']}), "
            f"esce {esce['pmid']} ({esce['rivista']})"
        )
        scelti[scelti.index(esce)] = entra
        panchina.remove(entra)
        mancanti -= 1

    # si ripristina l'ordine di rilevanza deciso dal modello
    scelti.sort(key=lambda a: graduatoria.index(a))
    n_prior = sum(1 for a in scelti if prioritario(a))
    log.info(f"Composizione: {n_prior} da riviste urgenza/generaliste su {len(scelti)}")
    return scelti


def _ts(a):
    dt = a.get("pubdate_dt")
    return dt.timestamp() if dt else 0


def ordina_per_solidita(candidati):
    """Disegni piu' solidi per primi (RCT, meta-analisi...), a parita' i piu' recenti.
    Serve al taglio quando i candidati superano il tetto: meglio perdere un
    trasversale che un trial."""
    return sorted(
        candidati,
        key=lambda a: (cfg.PRIORITA_DISEGNO.get(a.get("disegno"), 7), -_ts(a)),
    )


def _blocco_candidato(a, abstract_max):
    d = a.get("disegno") or cfg.DISEGNO_NON_DETERMINATO
    disegno = cfg.ETICHETTA_DISEGNO.get(d, d)
    if a.get("disegno_fonte") in ("pubtype", "testo"):
        disegno += f" (da {a['disegno_fonte']})"
    return cfg.FORMATO_CANDIDATO.format(
        pmid=a["pmid"],
        rivista=a["rivista"],
        data=a["data"],
        area=cfg.ETICHETTA_GRUPPO.get(a.get("gruppo"), "specialistica"),
        tipo=", ".join(a.get("pubtypes") or []) or "non disponibile",
        disegno=disegno,
        titolo=a["titolo"],
        abstract=cfg.compatta_abstract(a["abstract"], abstract_max),
    )


def screening(candidati):
    """1° passaggio: tutti i candidati, abstract compattato -> ARTICOLI_SCREENING
    finalisti. Solleva se la risposta non e' utilizzabile."""
    pool = candidati
    if len(pool) > cfg.MAX_CANDIDATI_SCREENING:
        log.warning(
            f"{len(pool)} candidati: allo screening ne invio {cfg.MAX_CANDIDATI_SCREENING}, "
            "scelti per solidita' del disegno e poi per data"
        )
        pool = ordina_per_solidita(pool)[:cfg.MAX_CANDIDATI_SCREENING]
    blocchi = [_blocco_candidato(a, cfg.ABSTRACT_MAX_SCREENING) for a in pool]
    prompt = cfg.PROMPT_SCREENING.format(
        n=cfg.ARTICOLI_SCREENING, articoli="\n\n---\n\n".join(blocchi))
    log.info(f"Screening: {len(pool)} candidati -> {cfg.ARTICOLI_SCREENING} finalisti")
    risposta = chiama_claude(
        prompt, max_tokens=cfg.MAX_TOKENS_SCREENING, system=cfg.SYSTEM_SCREENING)
    mappa = {a["pmid"]: a for a in pool}
    scelti = []
    for voce in _estrai_json_array(risposta):
        if not isinstance(voce, dict):
            continue
        pmid = str(voce.get("pmid", "")).strip()
        if pmid not in mappa:
            log.warning(f"    screening: PMID non tra i candidati, ignorato: {pmid!r}")
            continue
        if any(a["pmid"] == pmid for a in scelti):
            continue
        mappa[pmid]["tema"] = cfg.normalizza_tema(voce.get("tema"))
        scelti.append(mappa[pmid])
        if len(scelti) >= cfg.ARTICOLI_SCREENING:
            break
    log.info(f"Screening: {len(scelti)} finalisti: "
             + ", ".join(f"{a['pmid']}[{a['tema']}]" for a in scelti))
    return scelti


def graduatoria_finale(pool, usa_finale):
    """Graduatoria di ARTICOLI_RICHIESTI voci. Nel 2° passaggio (usa_finale) abstract
    quasi interi e ragionamento attivo; se l'API rifiuta il ragionamento si riprova
    senza. Restituisce l'array JSON gia' decodificato."""
    abstract_max = cfg.ABSTRACT_MAX_FILTRO_FINALE if usa_finale else cfg.ABSTRACT_MAX_FILTRO
    thinking = bool(usa_finale and cfg.FILTRO_FINALE_THINKING)
    blocchi = [_blocco_candidato(a, abstract_max) for a in pool]
    prompt = cfg.PROMPT_FILTRO_RILEVANZA.format(
        n=cfg.ARTICOLI_RICHIESTI,
        n_finali=cfg.ARTICOLI_FINALI,
        articoli="\n\n---\n\n".join(blocchi),
    )
    log.info(
        f"Claude ordina {len(pool)} candidati -> {cfg.ARTICOLI_RICHIESTI} in "
        f"graduatoria, {cfg.ARTICOLI_FINALI} pubblicati "
        f"({'2° passaggio' if usa_finale else 'passaggio unico'}, ragionamento {'attivo' if thinking else 'spento'})"
    )
    if cfg.DRY_RUN:
        log.info("--- CANDIDATI IN GRADUATORIA ---")
        for a in pool:
            tipi = ", ".join(a.get("pubtypes") or []) or "tipo n/d"
            log.info(f"    {a['pmid']} [{a['rivista']}] ({tipi}; {a.get('disegno')}) "
                     f"{a['titolo'][:100]}")
        log.info("--- FINE CANDIDATI ---")
    if thinking:
        try:
            risposta = chiama_claude(
                prompt, max_tokens=cfg.MAX_TOKENS_FILTRO_FINALE,
                system=cfg.SYSTEM_FILTRO, thinking=True)
        except Exception as e:
            log.warning(f"Graduatoria con ragionamento fallita ({e}): riprovo senza")
            risposta = chiama_claude(
                prompt, max_tokens=cfg.MAX_TOKENS_FILTRO, system=cfg.SYSTEM_FILTRO)
    else:
        risposta = chiama_claude(
            prompt, max_tokens=cfg.MAX_TOKENS_FILTRO, system=cfg.SYSTEM_FILTRO)
    return _estrai_json_array(risposta)


def _applica_graduatoria(voci, mappa):
    """Dalla risposta del modello ai selezionati, applicando in codice il vincolo di
    diversita' sui temi (il vocabolario e' chiuso; 'altro' non conta)."""
    selezionati, in_riserva, conteggio, graduatoria = [], [], {}, []
    for voce in voci:
        if len(selezionati) >= cfg.ARTICOLI_RICHIESTI:
            break
        if not isinstance(voce, dict):
            continue
        pmid   = str(voce.get("pmid", "")).strip()
        tema   = cfg.normalizza_tema(voce.get("tema"))
        perche = str(voce.get("perche", "")).strip()

        # Il PMID deve esistere tra i candidati: blocca le allucinazioni.
        if pmid not in mappa:
            log.warning(f"    PMID non tra i candidati, ignorato: {pmid!r}")
            continue
        if any(a["pmid"] == pmid for a in selezionati + in_riserva):
            continue
        a = mappa[pmid]
        a["tema"], a["perche"] = tema, perche
        graduatoria.append({"pmid": pmid, "tema": tema, "perche": perche})
        # Vincolo di diversita' applicato in codice, non solo nel prompt.
        if tema not in cfg.TEMI_NON_VINCOLATI and conteggio.get(tema, 0) >= cfg.MAX_PER_TEMA:
            log.info(f"    [{pmid}] rinviato in riserva: tema '{tema}' gia' saturo")
            in_riserva.append(a)
            continue
        conteggio[tema] = conteggio.get(tema, 0) + 1
        selezionati.append(a)
        log.info(f"    [{pmid}] {tema} -- {perche}")
    return selezionati, in_riserva, graduatoria


def filtra_top_articoli(candidati):
    """Restituisce gli articoli da pubblicare."""
    if len(candidati) <= cfg.ARTICOLI_FINALI:
        return candidati

    # Passaggio 1 (screening) solo se i candidati sono troppi per una lettura accurata;
    # altrimenti si va direttamente alla graduatoria con abstract e ragionamento pieni.
    pool, usa_finale = None, False
    if cfg.FILTRO_DUE_PASSAGGI:
        if len(candidati) <= cfg.ARTICOLI_SCREENING:
            pool, usa_finale = candidati, True
        else:
            try:
                pool = screening(candidati)
                if len(pool) < cfg.ARTICOLI_FINALI:
                    log.warning(f"Screening: solo {len(pool)} finalisti, passaggio unico")
                    pool = None
                else:
                    usa_finale = True
            except Exception as e:
                log.error(f"Screening fallito ({e}): passaggio unico")
                pool = None
    if pool is None:
        # Tetto ai candidati inviati al modello: una lista troppo lunga peggiora la
        # selezione oltre che i costi. Il taglio e' per solidita' del disegno.
        pool = candidati
        if len(pool) > cfg.MAX_CANDIDATI_PROMPT:
            log.warning(
                f"{len(pool)} candidati: ne invio al filtro i {cfg.MAX_CANDIDATI_PROMPT} "
                "con il disegno piu' solido"
            )
            pool = ordina_per_solidita(pool)[:cfg.MAX_CANDIDATI_PROMPT]

    mappa = {a["pmid"]: a for a in pool}
    selezionati, scartati_diversita = [], []
    try:
        voci = graduatoria_finale(pool, usa_finale)
        selezionati, scartati_diversita, grad = _applica_graduatoria(voci, mappa)
        CONTESTO_RUN["graduatoria"] = grad
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
        riserva_div = [a for a in scartati_diversita if a["pmid"] not in gia]
        pmid_riserva = {a["pmid"] for a in riserva_div}
        altri = [
            a for a in pool
            if a["pmid"] not in gia and a["pmid"] not in pmid_riserva
        ]
        altri.sort(key=_ts, reverse=True)
        for a in riserva_div + altri:
            if len(selezionati) >= cfg.MINIMO_ARTICOLI:
                break
            selezionati.append(a)
            gia.add(a["pmid"])

    selezionati = componi_digest(selezionati)
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
        "titolo_it":  str(voce.get("titolo_it", "")).strip(),
        "sintesi_it": str(voce.get("sintesi", "")).strip(),
        "rilevanza":  str(voce.get("rilevanza", "")).strip(),
        "limite":     str(voce.get("limite", "")).strip(),
        "tipo":       tipo,
    }


def forza_tipo_revisione(art):
    """Il badge delle sintesi di letteratura non si chiede al modello: si deduce dai
    PublicationType di PubMed, che sono esatti, o in mancanza dal disegno riconosciuto
    nel testo. Il modello tendeva a etichettarle "esplorativo" anche con istruzione
    esplicita."""
    tipi = set(art.get("pubtypes") or [])
    disegno = art.get("disegno")
    if "Meta-Analysis" in tipi or disegno == "meta-analisi":
        return art
    if (tipi & cfg.PUBTYPE_REVISIONE) or disegno in cfg.DISEGNI_REVISIONE:
        if art.get("tipo") != "revisione":
            log.info(
                f"    PMID {art['pmid']}: tipo '{art.get('tipo') or 'vuoto'}' -> "
                "'revisione' (imposto da PublicationType/disegno)"
            )
        art["tipo"] = "revisione"
    return art


def applica_badge(art):
    """Regole deterministiche sul badge: revisione imposta, e 'cambia-pratica' solo
    per i disegni abilitati in config (RCT e meta-analisi)."""
    forza_tipo_revisione(art)
    nuovo = cfg.declassa_badge(art.get("tipo"), art.get("disegno"))
    if nuovo != art.get("tipo"):
        log.info(
            f"    PMID {art['pmid']}: badge '{art.get('tipo')}' -> '{nuovo}' "
            f"(disegno: {art.get('disegno')})"
        )
        art["tipo_originale"] = art.get("tipo")
        art["tipo"] = nuovo
    return art


def _sintesi_vuota(art):
    art["titolo_it"]  = art.get("titolo_it")  or ""
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
        abstract=(art["abstract"][:cfg.ABSTRACT_MAX_SINTESI]
                  if art["abstract"] else "(non disponibile)"),
    )
    try:
        risposta = chiama_claude(
            prompt,
            max_tokens=cfg.MAX_TOKENS_SINTESI_SINGOLA,
            system=cfg.SYSTEM_SINTESI,
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


def registra_lint(art):
    """Controlli deterministici sulla scheda: nessuna chiamata API, solo log e archivio."""
    art["lint"] = []
    if cfg.LINT_ABILITATO and art.get("sintesi_it"):
        art["lint"] = cfg.controlla_lint(
            art["sintesi_it"], art.get("rilevanza", ""), art.get("limite", ""),
            art.get("abstract", ""))
        if art["lint"]:
            log.warning(f"    PMID {art['pmid']}: lint ({len(art['lint'])}): {art['lint'][:3]}")
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
            f"Abstract: {a['abstract'][:cfg.ABSTRACT_MAX_SINTESI] if a['abstract'] else '(non disponibile)'}"
        )
    prompt = cfg.PROMPT_SINTESI_MULTI.format(articoli="\n\n---\n\n".join(blocchi))
    log.info(f"Sintesi unica di {len(articoli)} articoli con Claude...")

    per_pmid = {}
    try:
        risposta = chiama_claude(
            prompt,
            max_tokens=cfg.MAX_TOKENS_SINTESI_MULTI,
            system=cfg.SYSTEM_SINTESI,
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
            applica_badge(art)
        else:
            log.warning(f"PMID {art['pmid']} assente dalla sintesi multipla - fallback singolo")
            sintetizza_articolo(art)
            applica_badge(art)
            time.sleep(1)
    for art in articoli:
        registra_lint(art)
    return articoli


# ═══════════════════════════════════════════════════════════════════════════════
# ARCHIVIO SETTIMANALE
# ═══════════════════════════════════════════════════════════════════════════════
def salva_archivio(wl, pubblicati):
    """Selezione, estrazione, sintesi ed esiti dei controlli in un JSON per settimana:
    il workflow lo carica come artifact. Mai bloccante."""
    if not cfg.ARCHIVIO_ABILITATO:
        return None
    try:
        os.makedirs(cfg.ARCHIVIO_DIR, exist_ok=True)
        nome = cfg.ARCHIVIO_FILE.format(
            settimana=str(wl["settimana"]).zfill(2), anno=wl["anno"])
        percorso = os.path.join(cfg.ARCHIVIO_DIR, nome)

        def pulito(a):
            return {k: v for k, v in a.items() if k != "pubdate_dt"}

        doc = {
            "settimana": wl["settimana"],
            "anno": wl["anno"],
            "generato_utc": datetime.now(timezone.utc).isoformat(),
            "modello": cfg.ANTHROPIC_MODEL,
            "dry_run": cfg.DRY_RUN,
            "impostazioni": {
                "filtro_due_passaggi": cfg.FILTRO_DUE_PASSAGGI,
                "lint": cfg.LINT_ABILITATO,
                "esearch": getattr(cfg, "ESEARCH_MODO", "off"),
            },
            "candidati": CONTESTO_RUN.get("candidati"),
            "graduatoria": CONTESTO_RUN.get("graduatoria", []),
            "esearch": CONTESTO_RUN.get("esearch"),
            "uso_api": dict(USO_API),
            "pubblicati": [pulito(a) for a in pubblicati],
        }
        with open(percorso, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, default=str)
        log.info(f"Archivio scritto in {percorso}")
        return percorso
    except Exception as e:
        log.warning(f"Archivio non scritto: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# TITOLI NELL'HTML: italiano sopra, originale inglese sotto
# ═══════════════════════════════════════════════════════════════════════════════
def _titolo_it(a):
    return (a.get("titolo_it") or "").strip() if cfg.TITOLO_TRADOTTO else ""


def titolo_html_email(a):
    it = _titolo_it(a)
    if not it:
        return (
            f'<a href="{esc(a["url"])}" style="font-family:Georgia,serif;font-size:19px;'
            f'font-weight:700;color:#1a1a1a;text-decoration:none;line-height:1.35;'
            f'display:block;margin-bottom:6px;">{esc(a["titolo"])}</a>'
        )
    return (
        f'<a href="{esc(a["url"])}" style="font-family:Georgia,serif;font-size:19px;'
        f'font-weight:700;color:#1a1a1a;text-decoration:none;line-height:1.35;'
        f'display:block;margin-bottom:4px;">{esc(it)}</a>'
        f'<div style="font-family:Georgia,serif;font-size:13px;font-style:italic;'
        f'color:#8a8a8a;line-height:1.4;margin-bottom:8px;">{esc(a["titolo"])}</div>'
    )


def titolo_html_scheda(a):
    it = _titolo_it(a)
    if not it:
        return (
            f'<div style="font-family:Georgia,serif;font-size:23px;font-weight:700;'
            f'color:#1a1a1a;line-height:1.32;margin-bottom:8px;">{esc(a["titolo"])}</div>'
        )
    return (
        f'<div style="font-family:Georgia,serif;font-size:23px;font-weight:700;'
        f'color:#1a1a1a;line-height:1.32;margin-bottom:6px;">{esc(it)}</div>'
        f'<div style="font-family:Georgia,serif;font-size:14px;font-style:italic;'
        f'color:#8a8a8a;line-height:1.4;margin-bottom:10px;">{esc(a["titolo"])}</div>'
    )


def build_html_scheda(a, indice, wl):
    """Pagina HTML autonoma con un singolo articolo, pensata per lo screenshot.
    Esclude l'abstract originale: la scheda resta di altezza leggibile."""
    meta_tipo = cfg.TIPI_ARTICOLO.get(a.get("tipo") or "")
    badge_html = (
        f'<span style="font-family:monospace;font-size:11px;font-weight:700;'
        f'letter-spacing:1.5px;text-transform:uppercase;color:#ffffff;'
        f'background:{meta_tipo["colore"]};padding:4px 9px;border-radius:3px;'
        f'margin-left:12px;">{esc(meta_tipo["label"])}</span>'
    ) if meta_tipo else ""

    limite_txt = (a.get("limite") or "").strip()
    limite_html = "" if (not limite_txt or limite_txt == cfg.LIMITE_NON_DESUMIBILE) else f"""
        <div style="font-family:Georgia,serif;font-size:14px;color:#6f6152;line-height:1.55;
                    margin:0 0 18px;padding:12px 18px;background:#fbf9f4;
                    border-left:3px solid #c9bda6;">
          <span style="font-family:monospace;font-size:10px;letter-spacing:1.5px;
                       text-transform:uppercase;color:#a08c6b;">Limite</span><br/>
          {esc(limite_txt)}
        </div>"""

    rilevanza_html = (
        f'<br/><br/><strong style="color:{cfg.COLOR_ACCENT};">{esc(a["rilevanza"])}</strong>'
        if a.get("rilevanza") else ""
    )
    sintesi_html = f"""
        <div style="background:#f7f4ef;border-left:3px solid {cfg.COLOR_ACCENT};
                    padding:16px 20px;font-family:Georgia,serif;font-size:15.5px;
                    color:#2a2a2a;line-height:1.65;margin-bottom:18px;">
          {esc(a['sintesi_it'])}{rilevanza_html}
        </div>""" if a.get("sintesi_it") else ""

    doi_txt = f' &nbsp;&middot;&nbsp; DOI {esc(a["doi"])}' if a.get("doi") else ""

    return f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#ffffff;width:{cfg.IMG_LARGHEZZA}px;">
  <div style="background:{cfg.COLOR_ACCENT};height:5px;"></div>
  <div style="background:{cfg.COLOR_DARK};padding:16px 34px;">
    <div style="font-family:monospace;font-size:10px;color:#8a8a8a;letter-spacing:3px;
                text-transform:uppercase;">{esc(cfg.NOME_SERVIZIO)}</div>
    <div style="font-family:monospace;font-size:11px;color:#cfcfcf;letter-spacing:1px;
                margin-top:6px;">
      {esc(cfg.NOME_NEWSLETTER)} &middot; Settimana {esc(wl['settimana'])}/{esc(wl['anno'])}
    </div>
  </div>
  <div style="padding:26px 34px 30px;">
    <div style="margin-bottom:14px;">
      <span style="font-family:monospace;font-size:15px;color:{cfg.COLOR_ACCENT};
                   font-weight:700;">{str(indice).zfill(2)}</span>
      <span style="font-family:monospace;font-size:12px;color:#aaa;margin-left:10px;">{esc(a['rivista'])} &middot; {esc(a['data'])}</span>{badge_html}
    </div>
    {titolo_html_scheda(a)}
    <div style="font-family:monospace;font-size:12.5px;color:#999;font-style:italic;
                margin-bottom:20px;">{esc(a['autori'])}</div>
    {sintesi_html}
    {limite_html}
    <div style="font-family:monospace;font-size:11px;color:#0a4d68;border-top:1px solid #e8e3db;
                padding-top:12px;">PubMed {esc(a['pmid'])}{doi_txt}</div>
  </div>
</body></html>"""


def genera_immagini(articoli):
    """Una scheda PNG per articolo, con Chromium headless via Playwright.
    Non e' mai bloccante: se qualcosa fallisce si prosegue senza immagini."""
    if not cfg.IMMAGINI_ABILITATE:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Playwright non installato: nessuna immagine generata")
        return []

    wl = numero_settimana()
    os.makedirs(cfg.IMG_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp()
    percorsi = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            # Viewport basso di partenza: lo screenshot full_page prende il
            # massimo fra contenuto e viewport, quindi un viewport alto
            # aggiungerebbe spazio bianco in fondo a ogni scheda.
            pagina = browser.new_page(
                viewport={"width": cfg.IMG_LARGHEZZA, "height": 200},
                device_scale_factor=cfg.IMG_SCALA,
            )
            for i, a in enumerate(articoli, 1):
                f = os.path.join(tmp, f"scheda{i}.html")
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(build_html_scheda(a, i, wl))
                pagina.goto(pathlib.Path(f).as_uri(),
                            wait_until="networkidle", timeout=cfg.IMG_TIMEOUT_MS)
                # Si misura l'altezza reale e vi si adatta il viewport: la scheda
                # risulta ritagliata esattamente sul contenuto.
                altezza = max(int(pagina.evaluate(
                    "Math.ceil(document.documentElement.scrollHeight)"
                )), 200)
                pagina.set_viewport_size(
                    {"width": cfg.IMG_LARGHEZZA, "height": altezza}
                )
                nome = f"digest-s{str(wl['settimana']).zfill(2)}-{str(i).zfill(2)}.png"
                dest = os.path.join(cfg.IMG_DIR, nome)
                pagina.screenshot(path=dest, full_page=True)
                percorsi.append(dest)
                kb = os.path.getsize(dest) // 1024
                log.info(
                    f"    immagine {i}/{len(articoli)}: {nome} "
                    f"({cfg.IMG_LARGHEZZA}x{altezza} CSS, {kb} KB)"
                )
            browser.close()
    except Exception as e:
        log.error(f"Generazione immagini interrotta: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tot_mb = sum(os.path.getsize(p) for p in percorsi) / 1e6
    log.info(f"Immagini generate: {len(percorsi)}/{len(articoli)} ({tot_mb:.1f} MB)")
    if tot_mb > cfg.IMG_MAX_MB:
        log.warning(f"Allegati oltre {cfg.IMG_MAX_MB} MB: non verranno allegati")
        return []
    return percorsi


def build_html(articoli, podcast_allegato=None):
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
            {titolo_html_email(a)}
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
    loghi = [l for l in getattr(cfg, "LOGHI", []) if l.get("url")]
    h = getattr(cfg, "LOGO_ALTEZZA", 100)
    logo_html = (
        '<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:14px;"><tr>'
        '<td style="background:#ffffff;padding:6px 10px;border-radius:6px;">'
        + "".join(
            f'<img src="{esc(l["url"])}" alt="{esc(l.get("alt", ""))}" height="{h}" '
            f'style="display:inline-block;height:{h}px;width:auto;border:0;vertical-align:middle;'
            f'{"margin-left:16px;" if k else ""}" />'
            for k, l in enumerate(loghi))
        + '</td></tr></table>'
    ) if loghi else ""
    riga_podcast = (
        f'      <tr><td style="background:#fbf3e4;padding:14px 32px;border-bottom:1px solid #e8dcc6;">'
        f'<span style="font-family:monospace;font-size:10px;font-weight:700;letter-spacing:2px;'
        f'color:{cfg.COLOR_ACCENT};text-transform:uppercase;">Podcast</span>'
        f'<span style="font-family:Georgia,serif;font-size:15px;color:#2a2a2a;"> &nbsp;'
        f'{len(articoli)} articoli in due minuti: '
        f'{"il video è in allegato" if str(podcast_allegato).endswith(".mp4") else "lo trovi in allegato"}, '
        f'<em>{esc(os.path.basename(podcast_allegato))}</em>.</span></td></tr>'
    ) if podcast_allegato else ""
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
{riga_podcast}
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


def _multipart(campi, file_campo=None, file_path=None):
    """Costruisce un corpo multipart/form-data senza dipendenze esterne."""
    conf = uuid.uuid4().hex
    parti = []
    for k, v in campi.items():
        parti.append(
            f'--{conf}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
            .encode("utf-8")
        )
    if file_campo and file_path:
        with open(file_path, "rb") as f:
            dati = f.read()
        nome = os.path.basename(file_path)
        parti.append(
            f'--{conf}\r\nContent-Disposition: form-data; name="{file_campo}"; '
            f'filename="{nome}"\r\nContent-Type: image/png\r\n\r\n'.encode("utf-8")
        )
        parti.append(dati)
        parti.append(b"\r\n")
    parti.append(f"--{conf}--\r\n".encode("utf-8"))
    return b"".join(parti), f"multipart/form-data; boundary={conf}"


def _fb_post(percorso_api, campi, file_path=None):
    url = f"https://graph.facebook.com/{cfg.FB_API_VERSION}/{percorso_api}"
    campi = dict(campi, access_token=cfg.FB_PAGE_TOKEN)
    if file_path:
        corpo, ctype = _multipart(campi, "source", file_path)
    else:
        corpo = urllib.parse.urlencode(campi).encode("utf-8")
        ctype = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=corpo,
                                 headers={"Content-Type": ctype}, method="POST")
    with urllib.request.urlopen(req, timeout=cfg.FB_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def testo_post_facebook(articoli, wl):
    """Didascalia del post: intestazione, elenco numerato, chiusura."""
    righe = [cfg.FB_INTESTAZIONE.format(
        settimana=wl["settimana"], anno=wl["anno"], n=len(articoli))]
    for i, a in enumerate(articoli, 1):
        meta = cfg.TIPI_ARTICOLO.get(a.get("tipo") or "")
        etichetta = f" [{meta['label'].upper()}]" if meta else ""
        righe.append(
            f"{str(i).zfill(2)}{etichetta} {_titolo_it(a) or a['titolo']}\n"
            f"{a['rivista']} — pubmed.ncbi.nlm.nih.gov/{a['pmid']}"
        )
    righe.append(cfg.FB_CHIUSURA)
    return "\n\n".join(righe)


def prossimi_slot_feriali(n):
    """Le prossime n date feriali all'ora configurata, fuso di Roma.
    Salta gli slot gia' passati o troppo vicini: l'API rifiuta orari a meno di
    10 minuti da adesso."""
    tz = ZoneInfo(cfg.FB_FUSO)
    adesso = datetime.now(tz)
    slot, giorno = [], adesso.date()
    while len(slot) < n:
        quando = datetime.combine(giorno, dt_time(cfg.FB_ORA, cfg.FB_MINUTO), tzinfo=tz)
        if giorno.weekday() in cfg.FB_GIORNI_FERIALI and \
           quando > adesso + timedelta(minutes=15):
            slot.append(quando)
        giorno += timedelta(days=1)
    return slot


def testo_post_articolo(a, indice, totale, wl):
    meta = cfg.TIPI_ARTICOLO.get(a.get("tipo") or "")
    limite = (a.get("limite") or "").strip()
    return cfg.POST_GIORNALIERO.format(
        settimana=wl["settimana"], anno=wl["anno"], i=indice, tot=totale,
        titolo=_titolo_it(a) or a["titolo"], rivista=a["rivista"], data=a["data"],
        badge=f" · {meta['label'].upper()}" if meta else "",
        sintesi=a.get("sintesi_it") or "",
        rilevanza=a.get("rilevanza") or "",
        limite=f"\n\nLimite: {limite}" if limite and limite != cfg.LIMITE_NON_DESUMIBILE else "",
        pmid=a["pmid"],
    )


def salva_primo_post(immagine, art, indice, totale, quando, wl):
    """Con il podcast attivo il post del primo articolo non si programma qui:
    se ne salvano i dati e lo pubblica 'podcast.py pubblica' (col video del
    podcast, o con questa immagine se il podcast non e' pronto)."""
    os.makedirs(cfg.PODCAST_DIR, exist_ok=True)
    dati = {
        "message": testo_post_articolo(art, indice, totale, wl),
        "immagine": immagine,
        "quando": quando.isoformat(),
        "settimana": wl["settimana"],
        "anno": wl["anno"],
        "titolo": art["titolo"],
        "totale": totale,
    }
    with open(cfg.PODCAST_PRIMO_POST, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)
    log.info(f"    post 1/{totale} rinviato al podcast "
             f"({quando.strftime('%a %d/%m %H:%M')}): {cfg.PODCAST_PRIMO_POST}")


def pubblica_facebook_programmata(immagini, articoli, wl):
    """Un post per articolo, uno per giorno feriale, tutti creati adesso e
    programmati. A pubblicarli e' Facebook. Con il podcast attivo il primo post
    (lunedi') lo pubblica podcast.py, insieme al video."""
    coppie = list(zip(immagini, articoli))
    slot = prossimi_slot_feriali(len(coppie))
    podcast = getattr(cfg, "PODCAST_ABILITATO", False)
    ok = 0
    creati = []
    for k, ((percorso, art), quando) in enumerate(zip(coppie, slot), 1):
        if podcast and k == 1:
            try:
                salva_primo_post(percorso, art, k, len(coppie), quando, wl)
                ok += 1
                continue
            except Exception as e:
                # se non si riesce a rinviarlo, il primo post esce come sempre
                log.error(f"    rinvio del post 1 al podcast fallito ({e}): lo programmo ora")
                podcast = False
        try:
            # La foto si carica non pubblicata, poi il post di feed la allega
            # e viene programmato: e' il percorso documentato per lo scheduling.
            r = _fb_post(f"{cfg.FB_PAGE_ID}/photos", {"published": "false"},
                         file_path=percorso)
            campi = {
                "message": testo_post_articolo(art, k, len(coppie), wl),
                "attached_media[0]": json.dumps({"media_fbid": r["id"]}),
                "published": "false",
                "scheduled_publish_time": str(int(quando.timestamp())),
            }
            p = _fb_post(f"{cfg.FB_PAGE_ID}/feed", campi)
            log.info(
                f"    post {k}/{len(coppie)} programmato per "
                f"{quando.strftime('%a %d/%m %H:%M')} -> {p.get('id')}"
            )
            if p.get("id"):
                creati.append((p["id"], art))
            ok += 1
        except urllib.error.HTTPError as e:
            log.error(f"    post {k} HTTP {e.code}: "
                      f"{e.read().decode('utf-8', 'replace')[:300]}")
        except Exception as e:
            log.error(f"    post {k} fallito: {e}")

    log.info(f"Facebook: {ok}/{len(coppie)} post programmati"
             + (" (il primo lo pubblica podcast.py)" if podcast else ""))
    # Il messaggio per il gruppo linka il primo post: col podcast lo scrive podcast.py.
    if creati and not podcast:
        try:
            scrivi_post_gruppo(creati[0][0], creati[0][1], wl)
        except Exception as e:
            # Il messaggio per il gruppo e' un di piu': un suo errore non deve far
            # fallire il run dopo che i post sono gia' stati programmati.
            log.warning(f"Messaggio per il gruppo non scritto: {e}")
    if ok and slot[-1].date() - slot[0].date() > timedelta(days=6):
        log.warning(
            "Gli slot superano i 6 giorni: l'ultimo post cadrebbe dopo il digest "
            "successivo. Valutare di anticipare FB_ORA o la corsa settimanale."
        )
    return ok > 0


def link_post(post_id):
    """Da '<page_id>_<post_id>' all'URL pubblico del post."""
    parti = str(post_id).split("_")
    if len(parti) == 2:
        return f"https://www.facebook.com/{parti[0]}/posts/{parti[1]}"
    return f"https://www.facebook.com/{post_id}"


def scrivi_post_gruppo(primo_id, primo_art, wl, link=None):
    """Prepara il messaggio da incollare a mano nel gruppo Facebook."""
    testo = cfg.POST_GRUPPO.format(
        nome=cfg.NOME_NEWSLETTER.split(" a cura")[0],
        settimana=wl["settimana"], anno=wl["anno"],
        primo_titolo=primo_art["titolo"],
        link=link or link_post(primo_id),
        pagina=cfg.FB_NOME_PAGINA,
    )
    with open(cfg.FB_FILE_GRUPPO, "w", encoding="utf-8") as f:
        f.write(testo)
    log.info(f"Messaggio per il gruppo scritto in {cfg.FB_FILE_GRUPPO}")
    return testo


def rinvia_primo_post_al_podcast(immagini, articoli, wl):
    """Con FB_SCHEDE = False le schede non vanno su Facebook: si prepara solo il post
    del lunedì, che 'podcast.py pubblica' farà uscire col video del podcast."""
    if not getattr(cfg, "PODCAST_ABILITATO", False):
        log.info("Facebook: schede disattivate (FB_SCHEDE) e podcast spento: nessun post")
        return False
    slot = prossimi_slot_feriali(1)
    try:
        salva_primo_post(immagini[0] if immagini else None, articoli[0], 1,
                         len(articoli), slot[0], wl)
    except Exception as e:
        log.error(f"Post del podcast non preparato: {e}")
        return False
    log.info("Facebook: schede non pubblicate (FB_SCHEDE = False); "
             "il lunedì esce solo il video del podcast")
    return True


def pubblica_facebook(immagini, articoli, wl):
    """Un unico post multi-foto sulla Pagina. Mai bloccante."""
    if not cfg.FB_ABILITATO:
        return False
    if not (cfg.FB_PAGE_ID and cfg.FB_PAGE_TOKEN):
        log.warning("Facebook non configurato (FB_PAGE_ID/FB_PAGE_TOKEN): salto")
        return False
    if not getattr(cfg, "FB_SCHEDE", True):
        return rinvia_primo_post_al_podcast(immagini, articoli, wl)
    if not immagini:
        log.warning("Nessuna immagine da pubblicare su Facebook")
        return False

    if cfg.FB_UN_POST_AL_GIORNO:
        return pubblica_facebook_programmata(immagini, articoli, wl)

    try:
        # 1. Le foto si caricano NON pubblicate: restituiscono un id da allegare.
        media = []
        for p in immagini:
            r = _fb_post(f"{cfg.FB_PAGE_ID}/photos", {"published": "false"}, file_path=p)
            media.append(r["id"])
            log.info(f"    foto caricata: {os.path.basename(p)} -> {r['id']}")

        # 2. Un solo post di feed che le raccoglie tutte.
        campi = {"message": testo_post_facebook(articoli, wl)}
        for k, mid in enumerate(media):
            campi[f"attached_media[{k}]"] = json.dumps({"media_fbid": mid})
        r = _fb_post(f"{cfg.FB_PAGE_ID}/feed", campi)
        log.info(f"Post Facebook pubblicato: {r.get('id')} ({len(media)} foto)")
        return True
    except urllib.error.HTTPError as e:
        log.error(f"Facebook HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
    except Exception as e:
        log.error(f"Pubblicazione Facebook fallita: {e}")
    return False


def genera_podcast(archivio=None):
    """Esegue 'podcast.py genera' in un processo separato, con un tempo massimo
    (cfg.PODCAST_TIMEOUT_MAIL): se la voce si blocca il processo viene chiuso e la
    newsletter parte comunque. Restituisce {"mp4": percorso, "mp3": percorso} (solo i
    file esistenti) o None."""
    if not getattr(cfg, "PODCAST_ABILITATO", False):
        return None
    if not cfg.PODCAST_DIPENDENZE_OK:
        log.warning("Podcast saltato: dipendenze non installate (vedi il passo del workflow)")
        return None
    script = os.path.join(cfg.DIR_BASE, "podcast.py")
    if not os.path.exists(script):
        return None
    try:
        os.remove(cfg.PODCAST_ESITO)          # mai leggere l'esito di una corsa precedente
    except OSError:
        pass
    comando = [sys.executable, script, "genera"] + (["--archivio", archivio] if archivio else [])
    log.info(f"Generazione del podcast (al massimo {cfg.PODCAST_TIMEOUT_MAIL // 60} minuti)...")
    inizio = time.time()
    try:
        subprocess.run(comando, cwd=cfg.DIR_BASE, timeout=cfg.PODCAST_TIMEOUT_MAIL, check=False)
    except subprocess.TimeoutExpired:
        log.warning(f"Podcast interrotto dopo {cfg.PODCAST_TIMEOUT_MAIL // 60} minuti: "
                    "la newsletter parte senza")
        return None
    except Exception as e:
        log.warning(f"Podcast non avviato: {e}")
        return None
    try:
        with open(cfg.PODCAST_ESITO, encoding="utf-8") as f:
            esito = json.load(f)
    except Exception:
        esito = {}
    file = {k: esito[k] for k in ("mp4", "mp3") if esito.get(k) and os.path.exists(esito[k])}
    if esito.get("ok") and file:
        log.info(f"Podcast pronto in {(time.time() - inizio) / 60:.0f} minuti: "
                 + ", ".join(file.values()))
        return file
    log.warning(f"Podcast non disponibile ({esito.get('errore') or 'esito mancante'}): "
                "la newsletter parte senza")
    return None


def allegati_podcast(podcast, immagini):
    """File del podcast da allegare alla mail, in ordine di preferenza
    (cfg.PODCAST_MAIL_ALLEGATI: prima il video, poi l'MP3), scartando quelli che
    porterebbero gli allegati oltre cfg.MAIL_MAX_MB."""
    if not podcast:
        return []
    base = sum(os.path.getsize(p) for p in immagini or [] if os.path.exists(p))
    scelti = []
    for tipo in cfg.PODCAST_MAIL_ALLEGATI:
        p = podcast.get(tipo)
        if not p:
            continue
        mb = (base + os.path.getsize(p)) / 1e6
        if mb > cfg.MAIL_MAX_MB:
            log.warning(f"Podcast {tipo} non allegabile: allegati a {mb:.1f} MB, "
                        f"massimo {cfg.MAIL_MAX_MB}")
            continue
        scelti.append(p)
    return scelti


def invia_email(oggetto, html_body, allegati=None, media=None):
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

    # "mixed" contiene l'alternative (testo + HTML) piu' gli allegati PNG.
    msg = MIMEMultipart("mixed")
    msg["Subject"] = oggetto
    msg["From"]    = f"EM Weekly Digest <{cfg.GMAIL_USER}>"
    # Destinatari in Bcc: nessuno vede gli indirizzi degli altri.
    # In To mettiamo il mittente stesso (alcuni client segnalano come spam
    # le email senza alcun To).
    msg["To"]      = cfg.GMAIL_USER
    msg["Bcc"]     = ", ".join(cfg.DESTINATARI)

    corpo = MIMEMultipart("alternative")
    corpo.attach(MIMEText(f"EM Weekly Digest — {oggetto}\nApri in HTML.", "plain", "utf-8"))
    corpo.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(corpo)

    for percorso in allegati or []:
        try:
            with open(percorso, "rb") as f:
                img = MIMEImage(f.read(), _subtype="png")
            img.add_header("Content-Disposition", "attachment",
                           filename=os.path.basename(percorso))
            msg.attach(img)
        except Exception as e:
            log.warning(f"Allegato saltato ({percorso}): {e}")

    if media:   # il podcast: video MP4 o audio MP3
        try:
            with open(media, "rb") as f:
                dati = f.read()
            if media.endswith(".mp4"):
                parte = MIMEBase("video", "mp4")
                parte.set_payload(dati)
                encoders.encode_base64(parte)
            else:
                parte = MIMEAudio(dati, _subtype="mpeg")
            parte.add_header("Content-Disposition", "attachment",
                             filename=os.path.basename(media))
            msg.attach(parte)
        except Exception as e:
            log.warning(f"Podcast non allegato ({media}): {e}")
            media = None

    try:
        from googleapiclient.http import MediaIoBaseUpload
        service = build('gmail', 'v1', credentials=creds)
        # Caricamento come message/rfc822 (endpoint /upload): il campo 'raw' nel JSON
        # regge solo pochi MB, troppo poco con il video del podcast. Così si arriva
        # al limite di Gmail (25 MB).
        dati = msg.as_bytes()
        corpo_upload = MediaIoBaseUpload(io.BytesIO(dati), mimetype="message/rfc822",
                                         resumable=True)
        service.users().messages().send(userId='me', body={},
                                        media_body=corpo_upload).execute()
        log.info(
            f"Email inviata a {len(cfg.DESTINATARI)} destinatari (Bcc), "
            f"{len(allegati or [])} immagini allegate"
            + (f", podcast {os.path.basename(media)}" if media else "")
            + f" ({len(dati) / 1e6:.1f} MB)"
        )
        return True
    except Exception as e:
        log.error(f"Invio fallito: {e}")
        return False


def main():
    cfg.valida_config()
    wl = numero_settimana()
    log.info(f"=== EM Weekly Digest — settimana {wl['settimana']}/{wl['anno']} ===")
    if cfg.DRY_RUN:
        log.warning("=== MODALITA' DRY RUN ATTIVA: nessun invio, nessuna pubblicazione ===")
    elif cfg.SOLO_FACEBOOK:
        log.warning("=== MODALITA' SOLO FACEBOOK: si pubblica su Facebook, niente email ===")
    else:
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

    CONTESTO_RUN["candidati"] = len(candidati)
    selezionati = filtra_top_articoli(candidati)
    log.info(f"Selezionati {len(selezionati)} articoli finali")

    if not selezionati:
        log.error("Filtro non ha selezionato nessun articolo")
        return False

    selezionati = sintetizza_articoli(selezionati)
    archivio = salva_archivio(wl, selezionati)
    log.info(
        f"Uso API: {USO_API['chiamate']} chiamate, {USO_API['token_in']} token in, "
        f"{USO_API['token_out']} token out"
    )

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

    oggetto = f"EM Weekly Digest — Settimana {wl['settimana']}/{wl['anno']}"

    log.info("Generazione schede PNG...")
    immagini = genera_immagini(selezionati)

    # Il podcast si genera prima della mail (gli servono archivio e schede), così
    # l'MP3 parte allegato. Mai bloccante: se manca, la mail esce come sempre.
    podcast = genera_podcast(archivio)
    candidati_podcast = allegati_podcast(podcast, immagini)
    html_body = build_html(selezionati,
                           podcast_allegato=candidati_podcast[0] if candidati_podcast else None)

    if cfg.DRY_RUN:
        with open(cfg.DRY_RUN_FILE, "w", encoding="utf-8") as f:
            f.write(html_body)
        log.info("=== DRY RUN: nessuna email inviata ===")
        log.info(f"Anteprima HTML scritta in {cfg.DRY_RUN_FILE}")
        log.info(f"Schede PNG in {cfg.IMG_DIR}/: {len(immagini)}")
        log.info("Podcast da allegare: "
                 + (", poi ".join(candidati_podcast) if candidati_podcast
                    else "nessuno (la mail partirebbe senza)"))
        # In prova a vuoto il post non parte: si salva solo la didascalia,
        # cosi' la si puo' rileggere (e usare per un post manuale nel gruppo).
        # L'anteprima deve mostrare esattamente cio' che verra' pubblicato: con
        # un post al giorno le didascalie sono cinque, una per articolo, non
        # l'elenco unico.
        with open("post_facebook.txt", "w", encoding="utf-8") as f:
            if not getattr(cfg, "FB_SCHEDE", True):
                f.write("Schede degli articoli NON pubblicate su Facebook (FB_SCHEDE = False).\n"
                        "Il lunedì esce solo il video del podcast: anteprima in "
                        "post_facebook_podcast.txt.\n")
                log.info("Facebook: schede disattivate, solo il post del podcast")
                if getattr(cfg, "PODCAST_ABILITATO", False):
                    try:
                        salva_primo_post(immagini[0] if immagini else None, selezionati[0], 1,
                                         len(selezionati), prossimi_slot_feriali(1)[0], wl)
                    except Exception as e:
                        log.warning(f"Post del lunedi' per il podcast non salvato: {e}")
            elif cfg.FB_UN_POST_AL_GIORNO:
                slot = prossimi_slot_feriali(len(selezionati))
                for k, (a, quando) in enumerate(zip(selezionati, slot), 1):
                    f.write(f"{'=' * 70}\nPOST {k}/{len(selezionati)} — "
                            f"programmato per {quando.strftime('%A %d/%m alle %H:%M')}\n"
                            f"immagine: digest-s{str(wl['settimana']).zfill(2)}-"
                            f"{str(k).zfill(2)}.png\n{'=' * 70}\n\n")
                    f.write(testo_post_articolo(a, k, len(selezionati), wl))
                    f.write("\n\n")
                log.info(f"Didascalie Facebook ({len(selezionati)} post) in post_facebook.txt")
                # Anche in prova il post del lunedi' passa al podcast: cosi'
                # 'podcast.py pubblica' ne scrive l'anteprima col video.
                if getattr(cfg, "PODCAST_ABILITATO", False) and immagini and slot:
                    try:
                        salva_primo_post(immagini[0], selezionati[0], 1,
                                         len(selezionati), slot[0], wl)
                    except Exception as e:
                        log.warning(f"Post del lunedi' per il podcast non salvato: {e}")
            else:
                f.write(testo_post_facebook(selezionati, wl))
                log.info("Didascalia Facebook (post unico) in post_facebook.txt")
        # In prova a vuoto i post non esistono ancora: il link andrà inserito a
        # mano dopo la pubblicazione vera.
        try:
            scrivi_post_gruppo(None, selezionati[0], wl,
                               link="[link disponibile dopo la pubblicazione]")
        except Exception as e:
            log.warning(f"Messaggio per il gruppo non scritto: {e}")
        log.info("--- SELEZIONE FINALE ---")
        for i, a in enumerate(selezionati, 1):
            tipi = ", ".join(a.get("pubtypes") or []) or "tipo n/d"
            log.info(f"  {i:02d}. [{a['pmid']}] {a['rivista']} - {tipi}")
            log.info(f"      {a['titolo'][:120]}")
            log.info(f"      badge: {a.get('tipo') or 'nessuno'}")
            log.info(f"      rilevanza: {(a.get('rilevanza') or '')[:160]}")
            log.info(f"      limite: {(a.get('limite') or '')[:160]}")
            log.info(f"      disegno: {a.get('disegno')} ({a.get('disegno_fonte')}) | "
                     f"tema: {a.get('tema')} | tipo originale: {a.get('tipo_originale') or '-'}")
            log.info(f"      titolo_it: {(a.get('titolo_it') or '')[:120]}")
            log.info(f"      lint: {a.get('lint') or 'nessun problema'}")
        log.info("=== OK (dry run) ===")
        return True

    if cfg.SOLO_FACEBOOK:
        log.warning("=== MODALITA' SOLO FACEBOOK: nessuna email inviata ===")
        ok = True
    else:
        # Prima col video, poi con l'MP3, infine senza podcast. L'invio è atomico:
        # se fallisce non è partito nulla e il tentativo successivo non duplica.
        ok = False
        for media in candidati_podcast + [None]:
            if media is None and candidati_podcast:
                log.warning("Invio con il podcast fallito: riprovo senza allegarlo")
            ok = invia_email(oggetto, build_html(selezionati, podcast_allegato=media),
                             allegati=immagini, media=media)
            if ok:
                break

    # Facebook dopo l'email: un errore qui non deve impedire l'invio del digest.
    pubblica_facebook(immagini, selezionati, wl)
    log.info("=== OK ===" if ok else "=== FALLITO ===")
    return ok


if __name__ == "__main__":
    main()
