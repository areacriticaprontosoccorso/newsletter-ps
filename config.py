"""
EM Weekly Digest — Configurazione centralizzata
Pronto Soccorso San Giovanni Bosco, Torino

Tutti i parametri configurabili in un solo posto.
Le credenziali vere stanno in variabili d'ambiente (secrets GitHub Actions).
"""

import os

# ═══════════════════════════════════════════════════════════════════════════════
# CREDENZIALI (lette da variabili d'ambiente — MAI hardcoded)
# ═══════════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL    = "claude-sonnet-5"

GMAIL_USER         = os.environ.get("GMAIL_USER", "")
# Invio tramite OAuth2: il token completo (JSON) sta in GMAIL_TOKEN.
GMAIL_TOKEN        = os.environ.get("GMAIL_TOKEN", "")
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

# Schedulazione (trigger esterno via cron-job.org -> workflow_dispatch)
# Lunedì 13:00 ora di Roma. Il fuso/DST è gestito da cron-job.org, non da GitHub.
GIORNO_INVIO      = "lunedì"
ORARIO_INVIO      = "13:00"  # ora di Roma; impostare cosi' su cron-job.org

# Branding
NOME_NEWSLETTER   = "EM Weekly Digest"
NOME_SERVIZIO     = "Pronto Soccorso · San Giovanni Bosco · Torino"
COLOR_ACCENT      = "#c41e3a"  # rosso
COLOR_DARK        = "#1a1a1a"


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT CLAUDE
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


PROMPT_SINTESI = """Sei un medico di Pronto Soccorso italiano, esperto di letteratura scientifica
e di traduzione medico-scientifica dall'inglese all'italiano.

Analizza l'articolo e produci un testo IN ITALIANO, con linguaggio medico-scientifico
preciso, del registro usato nelle riviste italiane di area critica.

REGOLE DI TRADUZIONE (obbligatorie):
- Traduci il SIGNIFICATO clinico, mai parola per parola. Vietati i calchi dall'inglese.
- Evita i falsi amici: "severe"=grave (non "severo"); "evidence"=prove/evidenze
  (non "evidenza"); "eventually"=infine (non "eventualmente"); "actual"=effettivo/reale
  (non "attuale"); "to administer"=somministrare; "rate"=tasso; "significant"
  (statistico)=statisticamente significativo; "mortality"=mortalita.
- Usa la terminologia clinica italiana corrente: stroke=ictus, seizure=crisi epilettica,
  bleeding=sanguinamento/emorragia, airway=vie aeree, ward=reparto,
  critically ill=pazienti critici, drug=farmaco, physician=medico, wound=ferita.
- Lascia in inglese SOLO i termini realmente in uso in clinica italiana: ARDS, shock,
  outcome, endpoint, follow-up, weaning, screening, setting, cut-off; usa "basale" per baseline.
- Riporta con precisione le misure statistiche: odds ratio (OR), hazard ratio (HR),
  rischio relativo (RR), intervallo di confidenza (IC) al 95%, valore di p. NON alterare
  numeri, dosi, unita di misura, percentuali.
- Mantieni in forma originale le scale validate (GCS, SOFA, qSOFA, NEWS2, CURB-65).
- Espandi ogni acronimo alla prima comparsa, poi usa la sigla.
- Attieniti SOLO ai dati dell'abstract: non aggiungere, non inferire, non inventare.

Produci:
1. SINTESI: 3-4 frasi che rispondano a - quesito clinico, disegno e popolazione dello studio,
   risultato principale (con i numeri chiave), impatto per la pratica in PS/Area Critica.
2. RILEVANZA: una sola frase sulla ricaduta pratica per il Pronto Soccorso o l'Area Critica.

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
    if not GMAIL_TOKEN:       mancanti.append("GMAIL_TOKEN")
    if mancanti:
        raise RuntimeError(
            f"Variabili d'ambiente mancanti: {', '.join(mancanti)}.\n"
            "Configurale prima di eseguire lo script."
        )
    return True
