"""
EM Weekly Digest — Configurazione centralizzata
Pronto Soccorso San Giovanni Bosco, Torino
Tutti i parametri configurabili in un solo posto.
Le credenziali vere stanno in variabili d'ambiente (secrets GitHub Actions).
"""
import os

# ═══════════════════════════════════════════════════════════════════════════════
# CREDENZIALI E DESTINATARI (letti da variabili d'ambiente — MAI hardcoded)
# ═══════════════════════════════════════════════════════════════════════════════
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL    = "claude-sonnet-5"
GMAIL_USER         = os.environ.get("GMAIL_USER", "")
# Invio tramite OAuth2: il token completo (JSON) sta in GMAIL_TOKEN.
GMAIL_TOKEN        = os.environ.get("GMAIL_TOKEN", "")
# Lista destinatari: secret DESTINATARI, indirizzi separati da virgola.
# Es.: "a@example.it, b@example.it, c@example.it"
DESTINATARI        = [
    e.strip() for e in os.environ.get("DESTINATARI", "").split(",") if e.strip()
]

# Modalità prova a vuoto: esegue tutta la pipeline (feed, efetch, filtro, sintesi)
# ma NON invia l'email; scrive l'HTML su file e logga la selezione per esteso.
# Attivazione: DRY_RUN=1 (accettati anche true/yes/si).
DRY_RUN      = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "si")
DRY_RUN_FILE = "anteprima_digest.html"

# ═══════════════════════════════════════════════════════════════════════════════
# IMMAGINI DEGLI ARTICOLI
# ═══════════════════════════════════════════════════════════════════════════════
# Una scheda PNG per articolo, allegata all'email e utile per la condivisione.
# Gli abstract in inglese sono esclusi: senza, la scheda resta di altezza leggibile.
IMMAGINI_ABILITATE = True
IMG_DIR        = "immagini"
IMG_LARGHEZZA  = 720   # px CSS della scheda
IMG_SCALA      = 2     # device scale factor: 2 = testo nitido su schermi retina
IMG_TIMEOUT_MS = 20000
IMG_MAX_MB     = 20    # oltre questa soglia gli allegati vengono omessi
NCBI_TOOL          = "em_weekly_digest_torino"  # User-Agent per i feed PubMed

# ═══════════════════════════════════════════════════════════════════════════════
# RIVISTE TARGET (15)
# "gruppo": em = medicina d'urgenza | gen = medicina generale | spec = specialistica.
# Serve al vincolo di composizione: il digest deve contenere almeno
# MIN_EM_GEN articoli da riviste em o gen, per non scivolare tutto sulle
# specialistiche, che pubblicano molto di più.
# ═══════════════════════════════════════════════════════════════════════════════
RIVISTE = [
    {"nome": "New England Journal of Medicine", "nlmta": "N Engl J Med",       "issn": "0028-4793", "gruppo": "gen"},
    {"nome": "The Lancet",                      "nlmta": "Lancet",             "issn": "0140-6736", "gruppo": "gen"},
    {"nome": "JAMA",                            "nlmta": "JAMA",               "issn": "0098-7484", "gruppo": "gen"},
    {"nome": "BMJ",                             "nlmta": "BMJ",                "issn": "0959-8138", "gruppo": "gen"},
    {"nome": "Circulation",                     "nlmta": "Circulation",        "issn": "0009-7322", "gruppo": "spec"},
    {"nome": "Chest",                           "nlmta": "Chest",              "issn": "0012-3692", "gruppo": "spec"},
    {"nome": "Annals of Emergency Medicine",    "nlmta": "Ann Emerg Med",      "issn": "0196-0644", "gruppo": "em"},
    {"nome": "Critical Care Medicine",          "nlmta": "Crit Care Med",      "issn": "0090-3493", "gruppo": "spec"},
    {"nome": "Intensive Care Medicine",         "nlmta": "Intensive Care Med", "issn": "0342-4642", "gruppo": "spec"},
    {"nome": "Resuscitation",                   "nlmta": "Resuscitation",      "issn": "0300-9572", "gruppo": "em"},
    {"nome": "Academic Emergency Medicine",     "nlmta": "Acad Emerg Med",     "issn": "1069-6563", "gruppo": "em"},
    {"nome": "Emergency Medicine Journal",      "nlmta": "Emerg Med J",        "issn": "1472-0205", "gruppo": "em"},
    # Aggiunte: colmano i buchi su neurologia vascolare e terapia intensiva open access.
    {"nome": "Stroke",                          "nlmta": "Stroke",             "issn": "0039-2499", "gruppo": "spec"},
    # Critical Care è solo online: se il feed torna vuoto, usare l'eISSN 1466-609X.
    {"nome": "Critical Care",                   "nlmta": "Crit Care",          "issn": "1364-8535", "gruppo": "spec"},
    # Annals of Intensive Care è solo online e ha un unico ISSN.
    {"nome": "Annals of Intensive Care",        "nlmta": "Ann Intensive Care", "issn": "2110-5820", "gruppo": "spec"},
]

# ═══════════════════════════════════════════════════════════════════════════════
# PARAMETRI PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════
GIORNI_RICERCA  = 7   # finestra temporale: ultimi 7 giorni (settimana)
GIORNI_RICERCA_ESTESO = 14  # fallback se la settimana è troppo povera
ARTICOLI_FINALI = 5   # numero articoli nel digest finale
ARTICOLI_RICHIESTI = 8  # quanti chiederne al modello: i 3 in più sono la riserva
                        # da cui il codice attinge per rispettare i vincoli
MIN_EM_GEN = 2        # minimo di articoli da riviste "em" o "gen" nel digest
GRUPPI_PRIORITARI = {"em", "gen"}
ETICHETTA_GRUPPO = {"em": "urgenza", "gen": "generale", "spec": "specialistica"}
MINIMO_ARTICOLI = 3   # sotto questa soglia scatta il fallback di riempimento
MAX_PER_TEMA    = 2   # max articoli sullo stesso tema clinico nello stesso digest
MAX_CANDIDATI_PROMPT = 150  # tetto di candidati inviati al filtro (protegge i token)

# Token per tipo di chiamata.
# NB: NON reintrodurre il parametro "temperature": è deprecato per questo modello
# e l'API risponde 400 (verificato sul run del 03/08/2026).
MAX_TOKENS_FILTRO          = 800
MAX_TOKENS_SINTESI_MULTI   = 4000
MAX_TOKENS_SINTESI_SINGOLA = 800

# Finestra RSS per rivista: PubMed accetta 15/20/50/100. Le riviste ad alto volume
# vanno alzate, altrimenti 20 item non coprono 7 giorni e si perdono articoli.
RSS_LIMIT_DEFAULT = 20

# Classificazione dell'articolo -> badge nell'email. Le chiavi sono i soli valori
# accettati dal parser: qualunque altro valore viene scartato.
TIPI_ARTICOLO = {
    "cambia-pratica": {"label": "Cambia la pratica", "colore": "#c41e3a"},
    "conferma":       {"label": "Conferma",          "colore": "#4a7c59"},
    "controverso":    {"label": "Controverso",       "colore": "#b8860b"},
    "esplorativo":    {"label": "Esplorativo",       "colore": "#6b7a8f"},
    "revisione":      {"label": "Revisione",         "colore": "#6f5b8e"},
}

# Frase fissa richiesta al modello quando l'abstract non permette di giudicare:
# essendo fissa, in build_html si può decidere di non stampare la riga.
LIMITE_NON_DESUMIBILE = "Limiti non desumibili dall'abstract."

# ═══════════════════════════════════════════════════════════════════════════════
# PRE-FILTRO DETERMINISTICO
# ═══════════════════════════════════════════════════════════════════════════════
# Il feed RSS di PubMed non espone il campo PublicationType, ma questi tipi di
# pubblicazione sono riconoscibili dal titolo. Filtrarli qui è deterministico
# e a costo zero, invece di delegarlo al prompt di filtro.
ESCLUSIONI_TITOLO = [
    r"^correction\b", r"^corrigendum\b", r"^erratum\b", r"^retraction\b",
    r"^withdrawn\b", r"^expression of concern\b", r"^notice of\b",
    r"^comments? on\b", r"^reply\b", r"^in reply\b", r"^response to\b",
    r"^re:\s", r"^letter\b", r"^correspondence\b", r"^authors?'? repl",
    r"^editorial\b", r"^this month in\b", r"^highlights\b", r"^in this issue\b",
    r"^images? in\b", r"^image of\b", r"^clinical picture\b",
    r"^visual diagnosis\b", r"^obituary\b", r"^in memoriam\b",
    r"^podcast\b", r"^book review\b",
]

# Lunghezza minima dell'abstract. A 200 il run del 03/08 scartava anche ricerca
# originale (SEP-1 e sepsi, arresto cardiaco pediatrico, blocco PENG ecoguidato):
# abbassata a 120, ora che il filtro sui titoli intercetta lettere e correzioni.
# La lunghezza effettiva è loggata a ogni scarto, per tararla sui dati.
ABSTRACT_MIN_CHARS = 120

# ═══════════════════════════════════════════════════════════════════════════════
# E-UTILITIES efetch — abstract veri e tipi di pubblicazione
# ═══════════════════════════════════════════════════════════════════════════════
# Il feed RSS di PubMed non contiene l'abstract per una larga quota di record
# (segnaposto di 11 caratteri) né il campo PublicationType. efetch fornisce
# entrambi con una sola richiesta per lotto di PMID.
EFETCH_URL     = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EFETCH_BATCH   = 200   # PMID per richiesta (POST, nessun limite di lunghezza URL)
EFETCH_TIMEOUT = 30
EFETCH_RETRY   = 2     # tentativi aggiuntivi prima di degradare sulla description RSS
NCBI_TOOL      = "newsletter-ps"
NCBI_EMAIL     = ""    # opzionale: NCBI chiede un contatto per usi automatizzati

# PublicationType da escludere. Etichette ufficiali PubMed: esatte, non euristiche.
# NB: "Review" e "Practice Guideline" NON sono qui: revisioni sistematiche e linee
# guida sono fra i contenuti più utili del digest.
PUBTYPE_ESCLUSI = {
    "Letter", "Comment", "Editorial", "Published Erratum", "Retraction of Publication",
    "Retracted Publication", "Expression of Concern", "Case Reports", "News",
    "Newspaper Article", "Biography", "Historical Article", "Portrait", "Interview",
    "Congress", "Video-Audio Media", "Address", "Autobiography", "Bibliography",
    "Personal Narrative", "Introductory Journal Article", "Patient Education Handout",
}

# Tipi da segnalare al filtro come indizio di qualità metodologica.
# PublicationType che identificano una sintesi di letteratura senza dati primari:
# su questi il badge "revisione" viene imposto in codice, senza chiederlo al modello.
# "Meta-Analysis" e' escluso di proposito: una metanalisi produce stime quantitative
# proprie e puo' legittimamente essere "cambia-pratica".
PUBTYPE_REVISIONE = {
    "Review", "Systematic Review", "Scoping Review", "Practice Guideline",
    "Guideline", "Consensus Development Conference",
    "Consensus Development Conference, NIH",
}

PUBTYPE_PRIORITARI = [
    "Randomized Controlled Trial", "Meta-Analysis", "Systematic Review",
    "Multicenter Study", "Clinical Trial, Phase III", "Practice Guideline",
]

# Schedulazione (trigger esterno via cron-job.org -> workflow_dispatch)
# Lunedì 13:00 ora di Roma. Il fuso/DST è gestito da cron-job.org, non da GitHub.
GIORNO_INVIO    = "lunedì"
ORARIO_INVIO    = "13:00"  # ora di Roma; impostare così su cron-job.org

# Branding
NOME_NEWSLETTER = "EM Weekly Digest a cura di Francesco Panero"
NOME_SERVIZIO   = "Area Critica e Pronto Soccorso · San Giovanni Bosco · Torino"
COLOR_ACCENT    = "#c41e3a"  # rosso
COLOR_DARK      = "#1a1a1a"
# URL PUBBLICO del logo (vuoto = nessun logo). Deve puntare a un file immagine
# raggiungibile pubblicamente (le email non supportano immagini locali/base64).
LOGO_URL        = "https://raw.githubusercontent.com/areacriticaprontosoccorso/newsletter-ps/main/logo.jpg"

# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT CLAUDE
# ═══════════════════════════════════════════════════════════════════════════════
# Contesto operativo del PS. Serve al filtro per scartare articoli inapplicabili
# (percorsi organizzativi esteri, farmaci non in commercio in Italia, risorse assenti).
CONTESTO_PS = """CONTESTO DEL LETTORE:
- Pronto Soccorso e Medicina d'Urgenza di un DEA di II livello, ospedale urbano
  di Torino, Servizio Sanitario Nazionale italiano.
- Casistica esclusivamente adulta, indifferenziata, con prevalenza di patologia
  medica acuta e una quota rilevante di anziani fragili e pluripatologici.
- Annesse Osservazione Breve Intensiva e Area Critica/shock room.
- Sono presenti in sede e attivabili h24: neurochirurgia, cardiochirurgia,
  emodinamica interventistica, chirurgia vascolare, endoscopia digestiva
  d'urgenza, rianimazione, TC, ecografia clinica point-of-care.

COME USARE QUESTO CONTESTO: essendo un hub completo, la disponibilità di risorse
NON è un criterio utile di esclusione, perché quasi tutto è tecnicamente
disponibile. Il criterio discriminante è un altro: la decisione descritta nello
studio appartiene al medico d'urgenza nelle prime ore, oppure a un altro
specialista in un altro momento del percorso?

ATTENZIONE ALLA FINESTRA TEMPORALE. Il lettore gestisce il paziente critico nelle
PRIME ORE, in shock room e in OBI, fino all'affidamento alla terapia intensiva o
al reparto. NON è un rianimatore e non segue la degenza intensiva.
Sono suoi: rianimazione cardiopolmonare e cure immediate post-ROSC, gestione
iniziale delle vie aeree e della ventilazione, rianimazione del paziente in shock
nelle prime ore, sedazione e analgesia procedurale, stabilizzazione del trauma e
del danno cerebrale acuto prima del trasferimento, indicazione al ricovero
intensivo.
NON sono suoi: prognosi e neuroprognosi a giorni di distanza, sedazione e weaning
durante la degenza intensiva, monitoraggio invasivo prolungato, svezzamento dal
ventilatore, nutrizione, decisioni di sospensione dei trattamenti, gestione delle
complicanze tardive della degenza. In particolare NON sono suoi: monitoraggio
invasivo della pressione intracranica, neuromonitoraggio multimodale, morfologia
dell'onda pressoria, autoregolazione cerebrale, e in generale la gestione
neurorianimatoria che segue il ricovero in terapia intensiva.
Un articolo di terapia intensiva è pertinente solo se l'intervento studiato
inizia nelle prime ore ed è avviabile in Pronto Soccorso.
NON basta che l'articolo menzioni la fase acuta, la stabilizzazione o il
pre-trasferimento per renderlo pertinente: conta dove e quando l'intervento
studiato viene effettivamente avviato. Se richiede strumentazione o competenze
disponibili solo dopo il ricovero in terapia intensiva, scartalo.

Appartengono inoltre al lettore: triage e stratificazione del rischio, diagnostica
d'urgenza, terapia delle prime ore, indicazione e timing dell'attivazione di un
percorso tempo-dipendente, gestione in OBI, decisione di ricovero o dimissione.

NON appartengono al lettore: gestione perioperatoria di chirurgia programmata,
terapia cronica e prevenzione a lungo termine, follow-up ambulatoriale, decisioni
prese in reparto o in sala operatoria dopo il ricovero. Il fatto che il servizio
esista in ospedale non rende l'argomento pertinente.

Privilegia inoltre gli interventi con farmaci in commercio in Italia."""

PROMPT_FILTRO_RILEVANZA = """COMPITO: dalla lista di articoli candidati, restituisci una GRADUATORIA di {n} articoli,
quelli con il maggior impatto sulla pratica clinica quotidiana in Pronto Soccorso,
Medicina d'Urgenza e Terapia Intensiva.

CRITERI DI SELEZIONE, in ordine di priorità decrescente:
1. IMPATTO DECISIONALE - l'articolo può modificare una decisione presa in PS nelle
   prime ore: triage, scelta diagnostica, terapia, destinazione del paziente.
2. LOCUS DECISIONALE - la decisione descritta appartiene al medico d'urgenza nelle
   prime ore, secondo la distinzione del contesto sopra. Uno studio metodologicamente
   ottimo ma su una decisione che non passa dal PS va scartato, non promosso.
3. QUALITA' METODOLOGICA - trial randomizzati, meta-analisi e revisioni sistematiche
   prima di studi osservazionali; numerosità adeguata; endpoint clinici anziché surrogati.
   Il campo TIPO di ogni candidato riporta i PublicationType ufficiali di PubMed:
   usalo come indizio diretto del disegno dello studio.
4. NOVITA' - a parità di tutto il resto, preferisci ciò che cambia o ribalta una
   pratica consolidata rispetto a ciò che conferma quanto già noto.

VINCOLI DI COMPOSIZIONE:
- Massimo 2 articoli sullo stesso tema clinico (es. non 3 studi sulla sepsi).
- Massimo 2 articoli dalla stessa rivista.
- Almeno 2 articoli devono provenire da riviste con AREA "urgenza" o "generale":
  le riviste specialistiche pubblicano molto di più e tendono a monopolizzare la
  selezione, ma il lettore è un medico d'urgenza.
- Preferisci una selezione che copra aree cliniche diverse.

ESCLUDI:
- Case report e case series.
- Studi puramente organizzativi su sistemi sanitari non europei.
- Ricerca di base o preclinica senza ricaduta clinica immediata.
- Cardiologia interventistica elettiva, chirurgia elettiva, oncologia ambulatoriale.

IMPORTANTE - LUNGHEZZA DELLA GRADUATORIA: devi restituire ESATTAMENTE {n} voci,
non {n_finali}. Ne verranno pubblicate soltanto le prime {n_finali}: le voci dalla
{n_finali} in poi sono la riserva da cui il sistema attinge per rispettare i vincoli
di composizione, e servono anche quando sono meno interessanti delle prime. Restituisci
meno di {n} voci solo se i candidati validi sono davvero meno di {n}.
L'ORDINE CONTA: ordina dalla più rilevante alla meno rilevante con cura, perché la
posizione determina cosa viene pubblicato.

ARTICOLI CANDIDATI:
{articoli}

FORMATO DI RISPOSTA - restituisci SOLO un array JSON valido, senza testo prima o dopo,
senza blocchi markdown. Scegli esclusivamente PMID presenti nella lista qui sopra:
non inventare né modificare PMID.

[
  {{"pmid": "12345678", "tema": "sepsi", "perche": "motivo in max 15 parole"}},
  {{"pmid": "23456789", "tema": "trauma cranico", "perche": "..."}}
]

Ordina dal più rilevante al meno rilevante."""

# Regole di traduzione condivise. Vivono nel system prompt delle chiamate di sintesi.
REGOLE_TRADUZIONE = """REGOLE DI TRADUZIONE (obbligatorie):
- ORTOGRAFIA: usa gli accenti italiani corretti (è, à, ì, ò, ù, é). Non sostituirli
  mai con l'apostrofo: si scrive "qualità", non "qualita\'"; "è", non "e\'";
  "più", non "piu\'"; "perché", non "perche\'".
- ORTOGRAFIA DI TERMINI RICORRENTI, spesso storpiati: si scrive "preospedaliero"
  (non "preistospedaliero" né "prestospedaliero"), "intraospedaliero",
  "extraospedaliero", "endovenoso", "endotracheale", "emogasanalisi".
- Traduci il SIGNIFICATO clinico, mai parola per parola. Vietati i calchi dall'inglese.
- Evita i falsi amici: "severe"=grave (non "severo"); "evidence"=prove/evidenze
  (non "evidenza"); "eventually"=infine (non "eventualmente"); "actual"=effettivo/reale
  (non "attuale"); "consistent"=coerente/costante (non "consistente"); "to require"=
  necessitare; "to administer"=somministrare; "rate"=tasso; "significant"
  (statistico)=statisticamente significativo; "mortality"=mortalità;
  "morbidity"=morbilità; "compliance"=aderenza; "management"=gestione;
  "care"=assistenza/cure; "to realize"=rendersi conto.
- Usa la terminologia clinica italiana corrente: stroke=ictus, seizure=crisi epilettica,
  bleeding=sanguinamento/emorragia, airway=vie aeree, ward=reparto,
  critically ill=pazienti critici, drug=farmaco, physician=medico, wound=ferita.
- Lessico dei trial: "trial"=studio/sperimentazione clinica; "arm"=braccio;
  "blinded"=in cieco; "double-blind"=in doppio cieco; "open-label"=in aperto;
  "primary/secondary endpoint"=endpoint primario/secondario; "number needed to
  treat"=NNT; "confounding"=confondimento; "adherence"=aderenza.
- Lascia in inglese SOLO i termini realmente in uso in clinica italiana: ARDS, shock,
  outcome, endpoint, follow-up, weaning, screening, setting, cut-off, bias,
  propensity score, hazard, washout; usa "basale" per baseline.
- Riporta con precisione le misure statistiche: odds ratio (OR), hazard ratio (HR),
  rischio relativo (RR), intervallo di confidenza (IC) al 95%, valore di p.
- NUMERI: riporta cifre e separatore decimale ESATTAMENTE come nell'originale
  (punto decimale: 0.85; p<0.001). Non convertire il punto in virgola: ogni
  riscrittura di un numero è un'occasione di errore. Non alterare dosi,
  unità di misura, percentuali.
- Mantieni in forma originale le scale validate (GCS, SOFA, qSOFA, NEWS2, CURB-65).
- Espandi ogni acronimo alla prima comparsa, poi usa la sigla.
- Non usare mai "significativo" da solo: specifica "statisticamente significativo"
  oppure "clinicamente rilevante".
- Attieniti SOLO ai dati dell'abstract: non aggiungere, non inferire, non inventare."""

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
# Persona, contesto e regole stabili vivono qui: sono identici a ogni esecuzione,
# mentre il messaggio utente contiene solo il compito e i dati della settimana.
SYSTEM_FILTRO = """Sei un medico strutturato di Pronto Soccorso italiano con esperienza
in medicina d'urgenza e cure critiche. Selezioni la letteratura settimanale per i
colleghi del tuo reparto.

""" + CONTESTO_PS

SYSTEM_SINTESI = """Sei un medico di Pronto Soccorso italiano, esperto di letteratura
scientifica e di traduzione medico-scientifica dall'inglese all'italiano. Scrivi in
italiano, con linguaggio medico-scientifico preciso, del registro usato nelle riviste
italiane di area critica.

""" + REGOLE_TRADUZIONE

# ── PROMPT DI SINTESI ─────────────────────────────────────────────────────────
# Sintesi di TUTTI gli articoli in una sola chiamata API.
PROMPT_SINTESI_MULTI = """Analizza OGNI articolo della lista e produci per ciascuno
quattro campi:

1. "sintesi" - da 90 a 120 parole, che rispondano nell'ordine a: quesito clinico;
   disegno dello studio e popolazione, con numerosità; risultato principale con i
   numeri chiave e la misura di effetto; ricaduta sulla pratica in PS/Area Critica.
2. "rilevanza" - UNA sola frase, massimo 30 parole, sulla ricaduta pratica concreta.
3. "limite" - UNA sola frase, massimo 25 parole, sul principale limite metodologico:
   monocentrico, non in cieco, endpoint surrogato, campione ridotto, popolazione
   selezionata, interruzione precoce, follow-up breve, conflitti di interesse.
   Per le sintesi di letteratura il limite riguarda il metodo della revisione:
   narrativa e non sistematica, selezione degli studi non riproducibile, assenza
   di valutazione formale della qualità, eterogeneità degli studi inclusi.
   Solo se l'abstract non consente davvero di identificare alcun limite, scrivi
   esattamente: "Limiti non desumibili dall'abstract."
4. "tipo" - UNO SOLO fra questi valori, riportato esattamente così:
   "cambia-pratica" = lo studio modifica una condotta oggi diffusa
   "conferma"       = rafforza una pratica già consolidata
   "controverso"    = risultati discordanti con evidenze o linee guida attuali
   "esplorativo"    = ipotesi generatrice, dati preliminari, campione insufficiente
   "revisione"      = sintesi di letteratura senza dati primari originali: review
                      narrativa, scoping review, revisione sistematica, linea guida.
                      Usa SEMPRE questo valore per le sintesi di letteratura, anche
                      quando le conclusioni sono preliminari: "esplorativo" è
                      riservato agli studi con dati primari.

SE L'ABSTRACT E' ASSENTE O PRIVO DI RISULTATI NUMERICI: scrivi nella "sintesi" una
sola frase che lo dichiari esplicitamente, non inferire nulla dal titolo, usa
"esplorativo" come tipo.

ARTICOLI:
{articoli}

FORMATO DI RISPOSTA - restituisci SOLO un array JSON valido, senza testo prima o dopo,
senza blocchi markdown, con un oggetto per articolo, nello stesso ordine della lista.
Riporta il "pmid" esattamente come ti è stato fornito.

[
  {{
    "pmid": "12345678",
    "sintesi": "...",
    "rilevanza": "...",
    "limite": "...",
    "tipo": "cambia-pratica"
  }}
]"""

# Sintesi di un singolo articolo (fallback se dal multi manca qualcosa).
# Restituisce un array di UN elemento, così da riusare lo stesso parser del multi.
PROMPT_SINTESI = """Analizza l'articolo e produci quattro campi:

1. "sintesi" - 90-120 parole: quesito clinico; disegno e popolazione con numerosità;
   risultato principale con i numeri chiave; ricaduta per PS/Area Critica.
2. "rilevanza" - una sola frase, massimo 30 parole.
3. "limite" - una sola frase, massimo 25 parole, sul principale limite metodologico.
   Per le revisioni: limite del metodo della revisione (narrativa, selezione non
   riproducibile, eterogeneità degli studi). Solo se davvero non desumibile,
   scrivi esattamente: "Limiti non desumibili dall'abstract."
4. "tipo" - uno fra: "cambia-pratica", "conferma", "controverso", "esplorativo",
   "revisione" (quest'ultimo per ogni sintesi di letteratura senza dati primari).

Se l'abstract è assente o privo di risultati numerici, dichiaralo nella "sintesi"
in una sola frase, non inferire dal titolo, e usa tipo "esplorativo".

Articolo:
PMID: {pmid}
Titolo: {titolo}
Autori: {autori}
Rivista: {rivista} ({data})
Abstract: {abstract}

FORMATO DI RISPOSTA - restituisci SOLO un array JSON valido con UN solo oggetto,
senza testo prima o dopo, senza blocchi markdown:

[
  {{
    "pmid": "{pmid}",
    "sintesi": "...",
    "rilevanza": "...",
    "limite": "...",
    "tipo": "conferma"
  }}
]"""

# ═══════════════════════════════════════════════════════════════════════════════
# PATH FILE
# ═══════════════════════════════════════════════════════════════════════════════
DIR_BASE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(DIR_BASE, "newsletter.log")


def valida_config():
    mancanti = []
    if not ANTHROPIC_API_KEY: mancanti.append("ANTHROPIC_API_KEY")
    # In dry run non si invia nulla: le credenziali Gmail e i destinatari
    # non servono, così la prova gira anche in locale senza token.
    if not DRY_RUN:
        if not GMAIL_USER:  mancanti.append("GMAIL_USER")
        if not GMAIL_TOKEN: mancanti.append("GMAIL_TOKEN")
        if not DESTINATARI: mancanti.append("DESTINATARI")
    if mancanti:
        raise RuntimeError(
            f"Variabili d'ambiente mancanti: {', '.join(mancanti)}.\n"
            "Configurale prima di eseguire lo script."
        )
    return True
