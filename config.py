"""
EM Weekly Digest — Configurazione centralizzata
Pronto Soccorso San Giovanni Bosco, Torino

Tutti i parametri configurabili in un solo posto.
Le credenziali vere stanno in variabili d'ambiente (.env / dashboard PythonAnywhere).
"""

import os

# ═══════════════════════════════════════════════════════════════════════════════
# CREDENZIALI (lette da variabili d'ambiente — MAI hardcoded)
# ═══════════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL    = "claude-haiku-4-5-20251001"


GMAIL_USER         = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RESPONSABILE_EMAIL = "francesco.panero@aslcittaditorino.it"
MCP_AUTH_TOKEN     = ""
GOOGLE_SHEET_ID    = ""
GOOGLE_CREDS_FILE  = "google_credentials.json"
NCBI_EMAIL         = os.environ.get("NCBI_EMAIL", "francesco.panero@aslcittaditorino.it")
NCBI_TOOL          = "em_weekly_digest_torino"



# ═══════════════════════════════════════════════════════════════════════════════
# RIVISTE TARGET (12 — ordinate per impact factor decrescente)
# ═══════════════════════════════════════════════════════════════════════════════

RIVISTE = [
    {"nome": "New England Journal of Medicine", "nlmta": "N Engl J Med",       "issn": "0028-4793"},
    {"nome": "The Lancet",                      "nlmta": "Lancet",             "issn": "0140-6736"},
    {"nome": "JAMA",                            "nlmta": "JAMA",               "issn": "0098-7484"},
    {"nome": "BMJ",                             "nlmta": "BMJ",                "issn": "0959-8138"},
    {"nome": "Circulation",                     "nlmta": "Circulation",        "issn": "0009-7322"},
    {"nome": "Chest",                           "nlmta": "Chest",              "issn": "0012-3692"},
    {"nome": "Annals of Emergency Medicine",    "nlmta": "Ann Emerg Med",      "issn": "0196-0644"},
    {"nome": "Critical Care Medicine",          "nlmta": "Crit Care Med",      "issn": "0090-3493"},
    {"nome": "Intensive Care Medicine",         "nlmta": "Intensive Care Med", "issn": "0342-4642"},
    {"nome": "Resuscitation",                   "nlmta": "Resuscitation",      "issn": "0300-9572"},
    {"nome": "Academic Emergency Medicine",     "nlmta": "Acad Emerg Med",     "issn": "1069-6563"},
    {"nome": "Emergency Medicine Journal",      "nlmta": "Emerg Med J",        "issn": "1472-0205"},
]


# ═══════════════════════════════════════════════════════════════════════════════
# PARAMETRI PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

GIORNI_RICERCA    = 7   # finestra temporale: ultimi 7 giorni (settimana)
ARTICOLI_PER_RIVISTA = 5  # candidati da ogni rivista (prima del filtro rilevanza)
ARTICOLI_FINALI   = 5   # numero articoli nel digest finale

# Tipi di pubblicazione PubMed da includere (esclusi case report)
TIPI_PUBMED = [
    "Journal Article",
    "Randomized Controlled Trial",
    "Clinical Trial",
    "Meta-Analysis",
    "Systematic Review",
    "Review",
    "Editorial",
    "Guideline",
    "Practice Guideline",
    "Observational Study",
]

# Schedulazione
ORARIO_BOZZA      = "07:00"  # mercoledì
ORARIO_INVIO      = "09:00"  # mercoledì
TIMEOUT_APPROVAZIONE_MIN = 90  # se non approvi entro 90 min, niente invio

# Branding
NOME_NEWSLETTER   = "EM Weekly Digest"
NOME_SERVIZIO     = "Pronto Soccorso · San Giovanni Bosco · Torino"
COLOR_ACCENT      = "#c41e3a"  # rosso
COLOR_DARK        = "#1a1a1a"


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT CLAUDE OPUS
# ═══════════════════════════════════════════════════════════════════════════════

PROMPT_FILTRO_RILEVANZA = """Sei un medico di Pronto Soccorso italiano. Devi selezionare i 5 articoli
più rilevanti per la pratica clinica in Pronto Soccorso, Medicina d'Urgenza,
Rianimazione e Terapia Intensiva, escludendo:
- articoli di sotto-specialità non pertinenti (es. cardiologia interventistica pura,
  chirurgia elettiva, oncologia ambulatoriale)
- editoriali generici non legati alla pratica acuta
- corrispondenza, lettere, errata corrige

Privilegiare:
- studi clinici e trial su patologie d'urgenza (sepsi, trauma, ACS, stroke, ARDS, etc.)
- linee guida e review su gestione acuta
- novità terapeutiche/diagnostiche applicabili in PS o ICU
- aggiornamenti su rianimazione cardiopolmonare e cure critiche

Ecco la lista di articoli candidati. Per ognuno hai PMID, titolo, rivista e abstract.

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


PROMPT_SINTESI = """Sei un medico di Pronto Soccorso italiano esperto in letteratura scientifica.

Analizza questo articolo e produci in italiano:
1. SINTESI: 3-4 frasi che rispondano a — domanda clinica, risultato principale, impatto per la pratica in PS/ICU
2. RILEVANZA: una sola frase sulla rilevanza pratica per il Pronto Soccorso o Area Critica

Articolo:
Titolo: {titolo}
Autori: {autori}
Rivista: {rivista} ({data})
Tipo pubblicazione: {tipo}
Abstract: {abstract}

Rispondi SOLO in questo formato:
SINTESI: [testo]
RILEVANZA: [testo]"""


# ═══════════════════════════════════════════════════════════════════════════════
# PATH FILE
# ═══════════════════════════════════════════════════════════════════════════════

DIR_BASE      = os.path.dirname(os.path.abspath(__file__))
LOG_FILE      = os.path.join(DIR_BASE, "newsletter.log")
STATO_FILE    = os.path.join(DIR_BASE, "stato_corrente.json")  # bozza pendente
ARCHIVIO_DIR  = os.path.join(DIR_BASE, "archivio")  # log invii precedenti


def valida_config():
    mancanti = []
    if not ANTHROPIC_API_KEY: mancanti.append("ANTHROPIC_API_KEY")
    if not GMAIL_USER:        mancanti.append("GMAIL_USER")
    if not GMAIL_APP_PASSWORD:mancanti.append("GMAIL_APP_PASSWORD")
    if mancanti:
        raise RuntimeError(
            f"Variabili d'ambiente mancanti: {', '.join(mancanti)}.\n"
            "Configurale prima di eseguire lo script."
        )
    return True
