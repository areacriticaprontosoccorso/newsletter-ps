"""
Avviso per email quando un invio fallisce o esce incompleto.

Si lancia come ultimo passo del workflow (`python avviso.py`, con `if: always()`).
Legge il log del run, cioe' solo le righe scritte DOPO l'avvio della newsletter
(NEWSLETTER_INIZIO, UTC: nello stesso file ci sono anche le righe dei test), e scrive a
AVVISI_EMAIL se trova errori o se il job e' fallito. Se tutto e' andato bene non invia nulla.

Cosa conta come problema:
  - righe ERROR e CRITICAL;
  - righe WARNING che dicono che qualcosa non e' uscito ("non pubblicato", "fallito", ...);
  - il job fallito (JOB_STATUS=failure o cancelled).

Spedizione: con GMAIL_TOKEN (OAuth, come la newsletter EM) usa l'API di Gmail, altrimenti
SMTP con GMAIL_USER e GMAIL_APP_PASSWORD (POCUS e NIV). Mai eccezioni: un avviso che non parte
non deve far fallire il run.

Copia identica nei tre repo (newsletter-ps, newsletter-pocus, Newsletter-NIV): se la modifichi,
modificala in tutti e tre.
"""
import base64
import json
import logging
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText

log = logging.getLogger("avviso")

RIGA_LOG = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[(\w+)\] (.*)$")
# Un WARNING che parla di un invio mancato e' un problema, gli altri sono rumore.
WARNING_GRAVE = re.compile(
    r"non pubblicat|non programmat|non inviat|non riuscit|non generat|fallit|impossibile|"
    r"saltat[oa] (?:per|a causa)|rinunc", re.I)
# ...ma i tentativi ripetuti (al terzo la cosa di solito riesce) non sono un problema.
WARNING_RUMORE = re.compile(r"tentativo|riprov", re.I)
MAX_RIGHE = 25
MAX_CARATTERI_RIGA = 300
DESTINATARIO_PREDEFINITO_ENV = "AVVISI_EMAIL"

# Mai nel testo di un'email: token e indirizzi di persone.
_SEGRETI = [
    (re.compile(r"(access_token|token|key|password)=[^&\s'\"]+", re.I), r"\1=***"),
    (re.compile(r"\bEAA[A-Za-z0-9]{20,}"), "***"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "***@***"),
]


def _pulisci(testo):
    for rx, sost in _SEGRETI:
        testo = rx.sub(sost, testo)
    return testo


def righe_problema(percorso_log, inizio):
    """Righe del log, dopo `inizio` ('AAAA-MM-GG HH:MM:SS'), che segnalano un problema.
    Senza `inizio` non si sa quali righe sono di questo run: ritorna []."""
    if not inizio or not percorso_log or not os.path.exists(percorso_log):
        return []
    trovate, viste = [], set()
    try:
        with open(percorso_log, encoding="utf-8", errors="replace") as f:
            for riga in f:
                m = RIGA_LOG.match(riga.rstrip("\n"))
                if not m or m.group(1) < inizio:
                    continue
                ora, livello, testo = m.groups()
                grave = livello in ("ERROR", "CRITICAL") or (livello == "WARNING" and WARNING_GRAVE.search(testo)
                                                 and not WARNING_RUMORE.search(testo))
                if not grave:
                    continue
                voce = f"{ora[11:]} [{livello}] {_pulisci(testo)[:MAX_CARATTERI_RIGA]}"
                if voce[9:] in viste:          # stessa riga ripetuta (tentativi)
                    continue
                viste.add(voce[9:])
                trovate.append(voce)
    except OSError as e:
        log.warning(f"Log non leggibile: {e}")
    return trovate


def costruisci(nome, stato_job, righe, url_run=""):
    """(oggetto, testo) dell'avviso, oppure None se non c'e' nulla da segnalare."""
    fallito = stato_job in ("failure", "cancelled")
    if not fallito and not righe:
        return None
    if fallito:
        oggetto = f"[{nome}] ERRORE: il run e' fallito"
        intro = "Il run della newsletter e' FALLITO."
    else:
        oggetto = f"[{nome}] Invio incompleto: {len(righe)} problemi nel log"
        intro = "Il run e' finito, ma qualcosa non e' uscito come previsto."
    parti = [intro, ""]
    if righe:
        parti.append("Righe del log da guardare:")
        mostrate = righe[:MAX_RIGHE]
        parti += [f"  {r}" for r in mostrate]
        if len(righe) > len(mostrate):
            parti.append(f"  ... e altre {len(righe) - len(mostrate)}")
    elif fallito:
        parti.append("Nessuna riga d'errore nel log del run: il job e' probabilmente caduto prima "
                     "della newsletter (installazione, test) o e' stato annullato.")
    if url_run:
        parti += ["", f"Run completo (con il log): {url_run}"]
    return oggetto, "\n".join(parti)


def _invia_gmail_api(msg, cfg_env):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    dati = json.loads(cfg_env["GMAIL_TOKEN"])
    creds = Credentials(token=dati["token"], refresh_token=dati["refresh_token"], token_uri=dati["token_uri"],
                        client_id=dati["client_id"], client_secret=dati["client_secret"], scopes=dati["scopes"])
    servizio = build("gmail", "v1", credentials=creds)
    grezzo = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    servizio.users().messages().send(userId="me", body={"raw": grezzo}).execute()


def _invia_smtp(msg, cfg_env):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(cfg_env["GMAIL_USER"], cfg_env["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)


def invia(oggetto, testo, ambiente=None):
    """Spedisce l'avviso. True se partito. Mai eccezioni."""
    amb = ambiente if ambiente is not None else os.environ
    try:
        mittente = amb.get("GMAIL_USER", "")
        destinatario = amb.get(DESTINATARIO_PREDEFINITO_ENV, "") or mittente
        if not mittente or not destinatario:
            log.warning("Avviso non inviato: mancano GMAIL_USER / AVVISI_EMAIL")
            return False
        msg = MIMEText(testo, "plain", "utf-8")
        msg["Subject"] = oggetto
        msg["From"] = mittente
        msg["To"] = destinatario
        if amb.get("GMAIL_TOKEN"):
            _invia_gmail_api(msg, amb)
        elif amb.get("GMAIL_APP_PASSWORD"):
            _invia_smtp(msg, amb)
        else:
            log.warning("Avviso non inviato: manca GMAIL_TOKEN oppure GMAIL_APP_PASSWORD")
            return False
        log.info("Avviso inviato")
        return True
    except Exception as e:
        log.warning(f"Avviso non inviato: {_pulisci(str(e))}")
        return False


def main(ambiente=None):
    amb = ambiente if ambiente is not None else os.environ
    if amb.get("DRY_RUN") or amb.get("SOLO_INSTAGRAM") or amb.get("SOLO_FACEBOOK"):
        log.info("Prova (dry_run / solo_*): nessun avviso")
        return 0
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import config as cfg
        percorso_log, nome = cfg.LOG_FILE, getattr(cfg, "NOME_AVVISO", None) or cfg.NOME_NEWSLETTER.split(" a cura")[0]
    except Exception as e:
        log.warning(f"config non leggibile ({e}): avviso senza log")
        percorso_log, nome = "", "Newsletter"
    righe = righe_problema(percorso_log, amb.get("NEWSLETTER_INIZIO", ""))
    url = ""
    if amb.get("GITHUB_RUN_ID") and amb.get("GITHUB_REPOSITORY"):
        url = f"{amb.get('GITHUB_SERVER_URL', 'https://github.com')}/{amb['GITHUB_REPOSITORY']}/actions/runs/{amb['GITHUB_RUN_ID']}"
    avviso = costruisci(nome, amb.get("JOB_STATUS", ""), righe, url)
    if not avviso:
        log.info("Nessun problema: nessun avviso")
        return 0
    invia(*avviso, ambiente=amb)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    raise SystemExit(main())
