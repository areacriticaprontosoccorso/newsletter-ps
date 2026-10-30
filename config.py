"""
EM Weekly Digest — Configurazione centralizzata
Pronto Soccorso San Giovanni Bosco, Torino
Tutti i parametri configurabili in un solo posto.
Le credenziali vere stanno in variabili d'ambiente (secrets GitHub Actions).

VERSIONE ECONOMICA: la pipeline fa 3 chiamate API a settimana (screening, graduatoria,
sintesi di tutti gli articoli in una volta), come prima delle sintesi per articolo.
La sintesi articolo per articolo con estrazione e revisore e' stata tolta apposta.

COMPATIBILITA': questo file mantiene tutti i nomi usati dalla versione attuale di
newsletter_rss.py, che quindi funziona anche senza modifiche (e senza il bug di
scrivi_post_gruppo). Le sezioni marcate [NUOVO] diventano operative solo quando
newsletter_rss.py le usa; quelle marcate [ATTIVO SUBITO] migliorano già il
comportamento attuale (prompt, glossario, esempi, limit dei feed).
"""
import os
import re
import json

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

# Modalità solo Facebook: esegue tutta la pipeline e pubblica i post programmati,
# ma NON invia l'email. Utile per ripubblicare o per lanciare la sola parte social
# senza far arrivare un secondo digest ai destinatari.
# Ha effetto solo se DRY_RUN non è attivo: la prova a vuoto non pubblica mai nulla.
SOLO_FACEBOOK = os.environ.get("SOLO_FACEBOOK", "").strip().lower() in ("1", "true", "yes", "si")

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

# ═══════════════════════════════════════════════════════════════════════════════
# PUBBLICAZIONE SU PAGINA FACEBOOK
# ═══════════════════════════════════════════════════════════════════════════════
# Le schede PNG vengono pubblicate come UNICO post multi-foto sulla Pagina.
# NB: nei GRUPPI non è più possibile pubblicare via API — Meta ha rimosso le
# Groups API il 22/04/2024. Dalla Pagina il post si condivide nel gruppo a mano.
# Permessi necessari sul token di Pagina: pages_manage_posts, pages_read_engagement.
FB_ABILITATO   = True
FB_API_VERSION = "v21.0"
FB_PAGE_ID     = os.environ.get("FB_PAGE_ID", "")
FB_PAGE_TOKEN  = os.environ.get("FB_PAGE_TOKEN", "")
FB_TIMEOUT     = 60

# Pubblicazione distribuita: un post al giorno nei giorni feriali, uno per articolo.
# La corsa del lunedì crea tutti e cinque i post già programmati; a pubblicarli
# è poi Facebook. Vincolo API: l'orario deve essere almeno 10 minuti nel futuro.
FB_UN_POST_AL_GIORNO = True
FB_FUSO              = "Europe/Rome"
FB_ORA               = 16   # ora di pubblicazione (0-23), fuso di Roma
FB_MINUTO            = 0
FB_GIORNI_FERIALI    = {0, 1, 2, 3, 4}   # lunedì=0 ... venerdì=4

# Didascalia del singolo post giornaliero.
POST_GIORNALIERO = """EM Weekly Digest — Settimana {settimana}/{anno} · articolo {i} di {tot}

{titolo}
{rivista}, {data}{badge}

{sintesi}

{rilevanza}{limite}

Fonte: pubmed.ncbi.nlm.nih.gov/{pmid}

Sintesi generata con supporto di intelligenza artificiale: verificare prima
dell'applicazione clinica.

#MedicinaDUrgenza #ProntoSoccorso #AreaCritica #EBM"""

# Intestazione del post. {settimana}, {anno} e {n} vengono sostituiti.
FB_INTESTAZIONE = """EM Weekly Digest — Settimana {settimana}/{anno}

I {n} articoli più rilevanti per la pratica in Pronto Soccorso e Area Critica,
selezionati dalla letteratura internazionale della settimana."""

FB_CHIUSURA = """Le sintesi sono generate con supporto di intelligenza artificiale
e vanno verificate prima dell'applicazione clinica.

#MedicinaDUrgenza #ProntoSoccorso #AreaCritica #EBM"""

# ── MESSAGGIO PER IL GRUPPO FACEBOOK ──────────────────────────────────────────
# [CORREZIONE BUG] scrivi_post_gruppo() in newsletter_rss.py legge queste tre
# costanti, che mancavano: il run terminava con AttributeError (exit code 1)
# subito dopo la programmazione dei post. Il messaggio va incollato A MANO nel
# gruppo (le Groups API non esistono più). Segnaposto sostituiti dal codice:
# {nome}, {settimana}, {anno}, {primo_titolo}, {link}, {pagina}.
FB_NOME_PAGINA = "Emergency Ultrasound School Turin"
FB_FILE_GRUPPO = "post_gruppo.txt"
POST_GRUPPO = """{nome} — Settimana {settimana}/{anno}

Da oggi, un articolo al giorno sulla pagina {pagina}.
Si parte con: {primo_titolo}

{link}"""

# ═══════════════════════════════════════════════════════════════════════════════
# PODCAST SETTIMANALE (podcast.py)
# ═══════════════════════════════════════════════════════════════════════════════
# Con PODCAST_ABILITATO il post Facebook del lunedì (primo articolo) non viene
# programmato da newsletter_rss.py: ne salva i dati in PODCAST_PRIMO_POST e lo
# pubblica "podcast.py pubblica", con il video del podcast se è pronto oppure,
# se il podcast è fallito, con la sola immagine come prima. Gli altri quattro
# post restano invariati. Con False tutto torna esattamente come prima.
PODCAST_ABILITATO  = os.environ.get("PODCAST", "1").strip().lower() not in ("0", "false", "no")
PODCAST_DIR        = "podcast"
PODCAST_PRIMO_POST = os.path.join(PODCAST_DIR, "primo_post.json")
PODCAST_ESITO      = os.path.join(PODCAST_DIR, "esito.json")
# Il podcast si genera dentro la newsletter, prima della mail, e l'MP3 parte allegato.
# Oltre PODCAST_TIMEOUT_MAIL secondi il processo viene chiuso e la mail esce senza.
PODCAST_TIMEOUT_MAIL = 45 * 60
# Cosa allegare alla mail, in ordine: il video (MP4, ~4 MB, audio + schede) e, se
# l'invio col video fallisce, l'MP3 (~2 MB). Nessuno dei due se gli allegati
# supererebbero MAIL_MAX_MB (Gmail accetta 25 MB; molte caselle aziendali meno).
PODCAST_MAIL_ALLEGATI = ["mp4", "mp3"]
MAIL_MAX_MB           = 15
# Il workflow passa l'esito del passo che installa torch/chatterbox: se è fallito
# non si perde tempo a tentare. In locale (variabile assente) si tenta.
PODCAST_DIPENDENZE_OK = os.environ.get("PODCAST_DIPENDENZE", "success").strip() in ("success", "")
PODCAST_VOCE_RIF   = "podcast_voce.wav"   # campione da imitare: 12,6 s della voce di Francesco Panero

PODCAST_INTRO = ("Benvenuti! Questa è la newsletter settimanale della Medicina "
                 "d'Emergenza-Urgenza dell'ospedale San Giovanni Bosco di Torino. "
                 "{Articoli} articoli, due minuti, sola medicina d'emergenza-urgenza. "
                 "Buon ascolto!")
PODCAST_CHIUSURA = "Alla prossima."

# Carattere della voce Chatterbox: valori più bassi = intonazione più calma e meno
# enfatica. 0,35 / 0,3 scelti all'ascolto il 30/10 (prima: 0,5 / 0,5).
PODCAST_ESAGERAZIONE = 0.35
PODCAST_CFG          = 0.3

# Velocità: il parlato si accelera quanto serve per stare in PODCAST_DURATA_MAX,
# ma sempre fra PODCAST_ATEMPO_MIN e PODCAST_ATEMPO_MAX. Con la voce di Francesco
# (ritmo più disteso) si è scelto 1,20 fisso all'ascolto il 30/10: con 216 parole
# il podcast dura circa 2'10" (oltre il limite: si pubblica comunque, con avviso).
PODCAST_DURATA_MAX = 120
PODCAST_ATEMPO_MIN = 1.20
PODCAST_ATEMPO_MAX = 1.20
# Parole del copione per TUTTI gli articoli insieme (intro e chiusura escluse),
# riferite a un digest di ARTICOLI_FINALI articoli: con meno articoli i limiti
# scalano in proporzione. Misurato sul run del 30/10: 235 parole + intro = 124 s anche
# a velocità 1,10, quindi il tetto è sceso a 220 (circa 120 s).
# In PODCAST_INTRO, {Articoli} diventa il numero in lettere ("Cinque", "Quattro"...).
PODCAST_PAROLE_MIN = 185
PODCAST_PAROLE_MAX = 220

# Verifica della voce: ogni frase viene ritrascritta con Whisper e confrontata
# col testo; sotto soglia si rigenera (fino a PODCAST_TENTATIVI volte, si tiene
# la migliore).
PODCAST_SOGLIA_WHISPER = 0.82
PODCAST_TENTATIVI      = 3
PODCAST_WHISPER_MODELLO = os.environ.get("PODCAST_WHISPER", "small")

# Pronuncia: sostituzioni applicate SOLO al testo dato alla sintesi vocale.
PODCAST_PRONUNCIA_IT = {
    "Emergenza-Urgenza": "Emergenza  Urgenza",   # col trattino "Urgenza" si fonde
    "emergenza-urgenza": "emergenza  urgenza",
    "EM": "I-em",
    "NIV": "Niv",
    "PS": "Pronto Soccorso",
    "OBI": "o-bi-i",
    "ASL": "a-esse-elle",
}
# Parole inglesi brevi dentro una frase italiana: invece di un segmento in voce
# inglese (che spezzerebbe il ritmo) si usa questa grafia fonetica.
PODCAST_GRAFIA_FONETICA = {
    "trial": "tràial",
    "cluster": "clàster",
    "stepped-wedge": "stèpd-uèdg",
    "outcome": "àutcam",
    "endpoint": "èndpoint",
    "score": "scòr",
    "follow-up": "follou-àp",
    "setting": "sètting",
    "timing": "tàiming",
}

# Testo del post unico del lunedì: va sopra la didascalia del primo articolo.
PODCAST_FB_TESTA = ("Il podcast della settimana: i {n} articoli in due minuti, "
                    "nel video. Qui sotto il primo articolo.\n\n")
PODCAST_FB_TITOLO = "EM Weekly Digest — Podcast settimana {settimana}/{anno}"

PODCAST_PROMPT = """Scrivi il copione parlato del podcast settimanale di un Pronto Soccorso: \
una sola voce, in italiano, tono tecnico per medici d'urgenza. Presenta i {n} articoli \
qui sotto, nell'ordine dato. Introduzione e saluti li aggiunge il programma: tu scrivi \
solo i paragrafi degli articoli.

REGOLE
1. LUNGHEZZA, il vincolo più importante: il podcast dura due minuti, quindi devi \
scegliere, non riassumere tutto. Un paragrafo per articolo, da {per_min} a {per_max} \
parole ciascuno, MAI di più (circa 3 o 4 frasi). Tutti i paragrafi insieme: fra \
{min_parole} e {max_parole} parole. Conta le parole prima di rispondere.
2. Apri ogni paragrafo con un connettivo d'ordine e la rivista, per esempio: \
"Si parte da [[Circulation]], ...", "Secondo studio, su ...", "Terzo, su ...", \
"Quarto, ancora su ...", "Infine, una metanalisi su ...".
3. Per ogni articolo: il disegno dello studio in poche parole, UN risultato principale \
con al massimo due numeri, e il limite più importante o il peso dell'evidenza. \
Ometti intervalli di confidenza, valori p, analisi secondarie e dettagli di metodo.
Ogni percentuale o rischio va sempre col suo confronto ("X% contro Y%"): mai un \
valore isolato. Scrivi i termini medici italiani per esteso e nella forma corretta \
("ritorno della circolazione spontanea", non "circolo spontaneo").
4. NUMERI: usa SOLO numeri presenti nell'abstract o nella sintesi di QUELL'articolo, \
scritti in cifre (decimali con la virgola, migliaia col punto: 60.000; percentuali col \
simbolo %). Puoi arrotondare solo premettendo "circa". NON calcolare numeri nuovi: \
niente differenze, conversioni, rapporti o percentuali derivate.
5. TERMINI INGLESI: racchiudi fra doppie parentesi quadre OGNI nome di rivista, nome o \
acronimo di studio e termine tecnico inglese: [[Critical Care Medicine]], \
[[point-of-care]], [[odds]], [[trial]]. Se la pronuncia inglese non si deduce dalla \
grafia, aggiungi dopo una barra verticale come si legge: [[ICare-FASTER|Eye-Care Faster]], \
[[ARDS|A R D S]]. Usa la parola italiana quando esiste ed è d'uso comune \
(rianimazione, ventilazione non invasiva, esito).
6. Frasi brevi, adatte all'ascolto: al massimo 30 parole per frase. Niente parentesi, \
elenchi, virgolette doppie o abbreviazioni da leggere (scrivi "intervallo di \
confidenza", non "IC"). Le sigle italiane d'uso comune (PS, NIV) vanno bene.
7. Non aggiungere nulla che non sia negli abstract o nelle sintesi.

Rispondi SOLO con un array JSON, un oggetto per articolo nello stesso ordine:
[{{"pmid": "...", "testo": "..."}}]

ARTICOLI
{blocchi}"""

# ═══════════════════════════════════════════════════════════════════════════════
# RIVISTE TARGET (15)
# "gruppo": em = medicina d'urgenza | gen = medicina generale | spec = specialistica.
# Serve al vincolo di composizione: il digest deve contenere almeno
# MIN_EM_GEN articoli da riviste em o gen, per non scivolare tutto sulle
# specialistiche, che pubblicano molto di più.
#
# "fascia": peso editoriale della rivista, 5 = massimo, 1 = minimo.
# NON e' l'impact factor grezzo: usare l'IF come numero (NEJM ~96 contro
# Ann Emerg Med ~5) schiaccerebbe le riviste di urgenza, che e' l'opposto
# di cio' che serve a questo digest. Le fasce comprimono la scala e restano
# stabili quando JCR aggiorna i valori.
# La fascia interviene SOLO negli spareggi di composizione (componi_digest e
# riempimento di riserva): non entra nel prompt di filtro, non tocca il taglio
# dei candidati e non riordina mai il digest finale, che resta in ordine di
# rilevanza deciso dal modello.
#
# "limit": finestra del feed RSS (PubMed accetta 15/20/50/100). [ATTIVO SUBITO]
# Nel run del 05/10 NEJM, Lancet, JAMA e BMJ riempivano TUTTI e 20 gli slot con
# articoli degli ultimi 7 giorni: il feed era troncato e gli altri si perdevano.
# ═══════════════════════════════════════════════════════════════════════════════
RIVISTE = [
    {"nome": "New England Journal of Medicine", "nlmta": "N Engl J Med",       "issn": "0028-4793", "gruppo": "gen",   "fascia": 5, "limit": 100},
    {"nome": "The Lancet",                      "nlmta": "Lancet",             "issn": "0140-6736", "gruppo": "gen",   "fascia": 5, "limit": 100},
    {"nome": "JAMA",                            "nlmta": "JAMA",               "issn": "0098-7484", "gruppo": "gen",   "fascia": 5, "limit": 100},
    {"nome": "BMJ",                             "nlmta": "BMJ",                "issn": "0959-8138", "gruppo": "gen",   "fascia": 5, "limit": 100},
    {"nome": "Circulation",                     "nlmta": "Circulation",        "issn": "0009-7322", "gruppo": "spec",  "fascia": 4},
    {"nome": "Chest",                           "nlmta": "Chest",              "issn": "0012-3692", "gruppo": "spec",  "fascia": 3, "limit": 100},
    {"nome": "Annals of Emergency Medicine",    "nlmta": "Ann Emerg Med",      "issn": "0196-0644", "gruppo": "em",    "fascia": 3},
    {"nome": "Critical Care Medicine",          "nlmta": "Crit Care Med",      "issn": "0090-3493", "gruppo": "spec",  "fascia": 3},
    {"nome": "Intensive Care Medicine",         "nlmta": "Intensive Care Med", "issn": "0342-4642", "gruppo": "spec",  "fascia": 4},
    {"nome": "Resuscitation",                   "nlmta": "Resuscitation",      "issn": "0300-9572", "gruppo": "em",    "fascia": 3},
    {"nome": "Academic Emergency Medicine",     "nlmta": "Acad Emerg Med",     "issn": "1069-6563", "gruppo": "em",    "fascia": 1},
    {"nome": "Emergency Medicine Journal",      "nlmta": "Emerg Med J",        "issn": "1472-0205", "gruppo": "em",    "fascia": 1},
    # Aggiunte: colmano i buchi su neurologia vascolare e terapia intensiva open access.
    {"nome": "Stroke",                          "nlmta": "Stroke",             "issn": "0039-2499", "gruppo": "spec",  "fascia": 3},
    # Critical Care è solo online: se il feed torna vuoto, usare l'eISSN 1466-609X.
    {"nome": "Critical Care",                   "nlmta": "Crit Care",          "issn": "1364-8535", "gruppo": "spec",  "fascia": 3},
    # Annals of Intensive Care è solo online e ha un unico ISSN.
    {"nome": "Annals of Intensive Care",        "nlmta": "Ann Intensive Care", "issn": "2110-5820", "gruppo": "spec",  "fascia": 2},
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
FASCIA_DEFAULT = 3    # fascia attribuita a una rivista priva del campo "fascia"
GRUPPI_PRIORITARI = {"em", "gen"}
ETICHETTA_GRUPPO = {"em": "urgenza", "gen": "generale", "spec": "specialistica"}
MINIMO_ARTICOLI = 3   # sotto questa soglia scatta il fallback di riempimento
MAX_PER_TEMA    = 2   # max articoli sullo stesso tema clinico nello stesso digest
MAX_CANDIDATI_PROMPT = 150  # tetto di candidati inviati al filtro (protegge i token)

# Token per tipo di chiamata.
# NB: NON reintrodurre il parametro "temperature": è deprecato per questo modello
# e l'API risponde 400 (verificato sul run del 03/08/2026).
MAX_TOKENS_FILTRO          = 800
MAX_TOKENS_SINTESI_MULTI   = 4500
MAX_TOKENS_SINTESI_SINGOLA = 1000

# Finestra RSS per rivista: PubMed accetta 15/20/50/100. Le riviste ad alto volume
# vanno alzate (campo "limit" in RIVISTE), altrimenti 20 item non coprono 7 giorni.
RSS_LIMIT_DEFAULT = 20

# ── [NUOVO] Filtro in due passaggi ────────────────────────────────────────────
# 1° passaggio (screening): TUTTI i candidati con abstract compattato (inizio +
#   conclusioni) -> ARTICOLI_SCREENING finalisti. Leggero, tollera falsi positivi.
# 2° passaggio (graduatoria): solo i finalisti, con l'abstract quasi intero
#   (risultati compresi) e il ragionamento attivo -> ARTICOLI_RICHIESTI in ordine.
#   Il 2° passaggio usa PROMPT_FILTRO_RILEVANZA, lo stesso del passaggio unico.
FILTRO_DUE_PASSAGGI      = True
ARTICOLI_SCREENING       = 20
MAX_CANDIDATI_SCREENING  = 250   # oltre, il taglio avviene per disegno (PRIORITA_DISEGNO)
ABSTRACT_MAX_SCREENING   = 450   # compatta_abstract(): ~40% inizio + ~60% fine
ABSTRACT_MAX_FILTRO_FINALE = 2500
FILTRO_FINALE_THINKING   = False  # True = 2° passaggio con ragionamento (nel dry run del 12/10 ha esaurito i token e ripiegato: se lo riattivi alza MAX_TOKENS_FILTRO_FINALE a 16000)
MAX_TOKENS_SCREENING     = 2000
MAX_TOKENS_FILTRO_FINALE = 6000  # include lo spazio per il ragionamento

# ── [NUOVO] Titolo tradotto ───────────────────────────────────────────────────
# Il campo "titolo_it" (prodotto dalla stessa chiamata di sintesi, senza costi
# aggiuntivi) compare sopra il titolo originale in email, schede PNG e post.
TITOLO_TRADOTTO = True

# ── [NUOVO] Archivio settimanale per la revisione ─────────────────────────────
# Selezione + estrazione + sintesi + esito di lint e revisore, in un JSON per
# settimana: il workflow lo carica come artifact (aggiungere "archivio/" al path
# dello step upload-artifact). Con qualche settimana accumulata si costruisce un
# set di riferimento per tarare i prompt.
ARCHIVIO_ABILITATO = True
ARCHIVIO_DIR       = "archivio"
ARCHIVIO_FILE      = "digest-s{settimana}-{anno}.json"

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
# [NUOVO] RICERCA DIRETTA SU PUBMED (ESearch) — alternativa/confronto con l'RSS
# ═══════════════════════════════════════════════════════════════════════════════
# L'RSS restituisce una finestra fissa per rivista e ha già dato feed vuoti o
# troncati. ESearch interroga PubMed per data di inserimento (edat) sulle sole
# riviste target ed esclude i pubtype indesiderati nella query stessa.
#   "off"        = solo RSS (comportamento attuale)
#   "confronto"  = si usa l'RSS ma si esegue anche ESearch e si LOGGA, rivista per
#                  rivista, cosa l'RSS ha perso: ideale per le prime settimane
#   "sostituisci"= i PMID vengono da ESearch; metadati e abstract da efetch
ESEARCH_MODO     = "confronto"
ESEARCH_URL      = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESEARCH_DATETYPE = "edat"   # data di inserimento in Entrez: "last 7 days"[edat]
ESEARCH_RETMAX   = 500
ESEARCH_TIMEOUT  = 30

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

# Troncatura degli abstract passati al modello.
# Al FILTRO (passaggio unico) basta l'inizio: conta il quesito e il disegno, e la
# lista è lunga. Col filtro a due passaggi vedi invece ABSTRACT_MAX_SCREENING e
# ABSTRACT_MAX_FILTRO_FINALE. Alla SINTESI serve tutto: con gli abstract completi
# di efetch, 2000 caratteri mutilavano i trial maggiori (ICECAP è stato
# sintetizzato su un testo troncato, e il modello lo ha segnalato come limite).
# 6000 copre ogni abstract PubMed.
ABSTRACT_MAX_FILTRO  = 700
ABSTRACT_MAX_SINTESI = 6000

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
NCBI_TOOL      = "newsletter-ps"   # User-Agent per i feed PubMed e per le E-utilities
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
# "Meta-Analysis" è escluso di proposito: una metanalisi produce stime quantitative
# proprie e può legittimamente essere "cambia-pratica".
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
BASE_URL_LOGHI  = "https://raw.githubusercontent.com/areacriticaprontosoccorso/newsletter-ps/main/"
# Loghi nell'intestazione della mail, affiancati da sinistra a destra.
LOGHI = [
    {"url": BASE_URL_LOGHI + "logo-medicina-urgenza.png",
     "alt": "Medicina d'Emergenza-Urgenza - Pronto Soccorso e Area Critica, Ospedale San Giovanni Bosco"},
]
LOGO_ALTEZZA    = 100   # px
LOGO_URL        = LOGHI[0]["url"]   # compatibilita'

# ═══════════════════════════════════════════════════════════════════════════════
# [NUOVO] DISEGNO DELLO STUDIO — riconoscimento dal testo
# ═══════════════════════════════════════════════════════════════════════════════
# Il PublicationType "Randomized Controlled Trial" viene assegnato spesso settimane
# dopo la pubblicazione: sugli articoli freschi c'è solo "Journal Article". Il
# disegno si deduce quindi anche da titolo + abstract. Precedenza: 1) PublicationType
# se esatto (PUBTYPE_A_DISEGNO); 2) prima regola del testo che combacia, NELL'ORDINE
# QUI SOTTO. L'ordine conta: "randomized" compare anche in "meta-analysis of
# randomized trials", in "Mendelian randomization" e in "target trial emulation",
# che vanno quindi riconosciuti PRIMA del trial randomizzato.
DISEGNO_NON_DETERMINATO = "non-determinato"

DISEGNI = [
    # Solo se il testo STESSO e' una linea guida o un consenso. La sola parola
    # "guidelines" compare in moltissimi studi ("adherence to guidelines"): non basta.
    ("linea-guida", [
        r"\b(?:these|this) (?:\w+ ){0,2}guidelines?\b",
        r"\bguidelines? (?:recommend|provide|present|update|were developed|was developed)",
        r"\bconsensus (?:statement|recommendations?|document)\b",
        r"\bposition statement\b", r"\bexpert (?:consensus|panel)\b", r"\bdelphi\b",
    ]),
    ("protocollo", [
        r"\bstudy protocol\b", r"\btrial protocol\b", r"\bprotocol for (?:a|the)\b",
        r"\bstatistical analysis plan\b", r"\bwill be randomi[sz]ed\b",
    ]),
    ("mendeliano", [r"\bmendelian randomi[sz]ation\b"]),
    ("meta-analisi", [
        r"\bmeta-?analy(?:sis|ses|tic)\b", r"\bpooled analysis\b",
    ]),
    ("revisione-sistematica", [r"\bsystematic review\b", r"\bscoping review\b"]),
    ("revisione-narrativa", [
        r"\bnarrative review\b", r"\bthis (?:narrative )?review\b",
    ]),
    # Solo se l'analisi è DICHIARATA come derivata da un altro studio ("post hoc
    # analysis of the X trial"). Un RCT primario che riporta un sottogruppo post hoc
    # ("in a post hoc analysis of patients with...") NON deve finire qui, altrimenti
    # perderebbe il badge: la classe [\w\- ] non attraversa "/", ",", "(" e simili.
    ("analisi-secondaria", [
        r"\bsecondary analysis of\b",
        r"\bpost[- ]hoc analysis of (?:the |a |an )?[\w\- ]{0,60}?\b(?:trials?|studies|study|RCTs?)\b",
        r"\bancillary (?:study|analysis)\b", r"\bsub-?analysis\b", r"\bsub-?study\b",
    ]),
    ("rct", [
        r"\brandomi[sz]ed\b", r"\brandomly (?:assigned|allocated)\b",
        r"\brandomi[sz]ation\b", r"\bnon-?inferiority trial\b",
    ]),
    ("emulazione-trial", [r"\btarget trial emulation\b", r"\bemulat(?:e|ed|ing) a (?:target )?trial\b"]),
    ("modello-predittivo", [
        r"\b(?:derivation|validation) (?:cohort|set)\b", r"\bprediction (?:model|rule)\b",
        r"\bmachine[- ]learning\b", r"\bdeep learning\b", r"\bclinical decision rule\b",
    ]),
    ("accuratezza-diagnostica", [
        r"\bdiagnostic accuracy\b", r"\bsensitivity and specificity\b",
        r"\bnegative predictive value\b", r"\barea under the (?:receiver|curve)\b",
        r"\bauroc\b", r"\bauc\b",
    ]),
    ("propensity", [
        r"\bpropensity\b", r"\binverse probability (?:of treatment )?weight",
        r"\binstrumental variable\b", r"\bregression discontinuity\b",
        r"\bdifference-in-differences\b",
    ]),
    ("coorte", [
        r"\bcohort\b", r"\bregistry\b", r"\bregister-based\b", r"\bobservational study\b",
    ]),
    ("caso-controllo", [r"\bcase-control\b"]),
    ("trasversale", [r"\bcross-sectional\b", r"\bsurvey\b", r"\bquestionnaire\b"]),
    ("qualita-organizzativo", [
        r"\bquality improvement\b", r"\bbefore-and-after\b",
        r"\binterrupted time series\b", r"\bimplementation (?:study|trial)\b",
    ]),
]

# PublicationType esatti che determinano il disegno. L'ordine è la precedenza:
# se un record ha sia Meta-Analysis sia Systematic Review vince la metanalisi.
PUBTYPE_A_DISEGNO = {
    "Meta-Analysis":                 "meta-analisi",
    "Systematic Review":             "revisione-sistematica",
    "Practice Guideline":            "linea-guida",
    "Guideline":                     "linea-guida",
    "Randomized Controlled Trial":   "rct",
    "Review":                        "revisione-narrativa",
}

ETICHETTA_DISEGNO = {
    "linea-guida": "linea guida / consenso", "protocollo": "protocollo",
    "mendeliano": "randomizzazione mendeliana", "meta-analisi": "meta-analisi",
    "revisione-sistematica": "revisione sistematica",
    "revisione-narrativa": "revisione narrativa",
    "analisi-secondaria": "analisi secondaria/post hoc",
    "rct": "studio randomizzato", "emulazione-trial": "emulazione di trial",
    "modello-predittivo": "modello predittivo", "accuratezza-diagnostica": "accuratezza diagnostica",
    "propensity": "osservazionale con propensity/aggiustamento", "coorte": "coorte/registro",
    "caso-controllo": "caso-controllo", "trasversale": "trasversale/survey",
    "qualita-organizzativo": "qualità/organizzativo", DISEGNO_NON_DETERMINATO: "non determinato",
}

# Protocolli di studio: non hanno risultati, vanno scartati prima del filtro.
DISEGNI_DA_ESCLUDERE = {"protocollo"}

# Disegni che identificano una sintesi di letteratura senza dati primari: il badge
# diventa "revisione" anche quando il PublicationType non e' ancora stato assegnato.
# La meta-analisi e' esclusa di proposito (produce stime proprie).
DISEGNI_REVISIONE = {"revisione-sistematica", "revisione-narrativa", "linea-guida"}

# Il badge "cambia-pratica" è consentito SOLO a questi disegni. Per gli altri,
# riconosciuti ma non abilitati, declassa_badge() porta il badge a
# BADGE_DECLASSATO. Se il disegno è "non-determinato" il badge non si tocca.
BADGE_CAMBIA_PRATICA_DISEGNI = {"rct", "meta-analisi"}
BADGE_DECLASSATO = "esplorativo"

# Ordine di priorità per il taglio dei candidati quando superano
# MAX_CANDIDATI_SCREENING: prima i disegni più solidi, non i più recenti.
PRIORITA_DISEGNO = {
    "rct": 0, "meta-analisi": 1, "linea-guida": 2, "revisione-sistematica": 3,
    "analisi-secondaria": 4, "modello-predittivo": 5, "accuratezza-diagnostica": 5,
    "propensity": 6, "emulazione-trial": 6, "coorte": 7, "caso-controllo": 8,
    "trasversale": 9, "qualita-organizzativo": 9, "revisione-narrativa": 8,
    "mendeliano": 9, DISEGNO_NON_DETERMINATO: 7, "protocollo": 99,
}

# Riga di ciascun candidato nei prompt di screening e di graduatoria.
FORMATO_CANDIDATO = (
    "PMID: {pmid}\n"
    "RIVISTA: {rivista} ({data})\n"
    "AREA: {area}\n"
    "TIPO: {tipo}\n"
    "DISEGNO: {disegno}\n"
    "TITOLO: {titolo}\n"
    "ABSTRACT: {abstract}"
)

# ═══════════════════════════════════════════════════════════════════════════════
# [ATTIVO SUBITO] VOCABOLARIO CHIUSO DEI TEMI CLINICI
# ═══════════════════════════════════════════════════════════════════════════════
# Con temi liberi "sepsi" e "shock settico" valevano due temi e il vincolo
# MAX_PER_TEMA veniva aggirato. Il modello deve scegliere da questa lista; il
# codice può normalizzare qualunque valore fuori lista in TEMA_FALLBACK.
TEMI = [
    "sepsi-e-shock-settico", "shock-ed-emodinamica", "arresto-cardiaco-e-rianimazione",
    "sindrome-coronarica-acuta", "scompenso-cardiaco-acuto", "aritmie-e-fibrillazione-atriale",
    "sincope", "embolia-polmonare-e-tev", "ipertensione-e-aorta",
    "ictus-ischemico", "emorragia-intracranica", "trauma-cranico",
    "trauma-maggiore-ed-emorragia", "insufficienza-respiratoria-e-ventilazione",
    "vie-aeree-e-intubazione", "polmonite-e-infezioni-respiratorie", "bpco-e-asma",
    "infezioni-non-respiratorie", "anticoagulanti-ed-emostasi", "emorragia-digestiva",
    "addome-acuto-e-chirurgia-urgente", "rene-ed-elettroliti", "diabete-e-disturbi-metabolici",
    "intossicazioni-e-overdose", "anafilassi-e-allergia", "crisi-epilettiche",
    "delirium-e-agitazione", "sedazione-e-analgesia", "geriatria-e-fragilita",
    "ecografia-point-of-care", "imaging-e-diagnostica", "biomarcatori-e-score-di-rischio",
    "intelligenza-artificiale-e-modelli-predittivi", "organizzazione-triage-e-sovraffollamento",
    "emergenze-ambientali", "gravidanza-e-ostetricia", "altro",
]
TEMA_FALLBACK = "altro"
# "altro" non è un tema clinico: non deve contare per il tetto MAX_PER_TEMA.
TEMI_NON_VINCOLATI = {"altro"}
_ELENCO_TEMI = ", ".join(TEMI)

# ═══════════════════════════════════════════════════════════════════════════════
# [NUOVO] CONTROLLI DETERMINISTICI SULLA SINTESI (lint) — costo zero
# ═══════════════════════════════════════════════════════════════════════════════
# controlla_lint() NON fa ripetere nessuna chiamata (sarebbe un costo): i problemi
# trovati vengono scritti nel log e nell'archivio settimanale, per rivedere la
# sintesi prima di condividerla e per tarare i prompt. I pattern sono volutamente
# severi: preferibile un avviso di troppo a un errore in una newsletter clinica.
LINT_ABILITATO        = True
LINT_PAROLE_SINTESI   = (90, 120)   # intervallo ammesso, estremi inclusi
LINT_MAX_PAROLE_RILEVANZA = 30
LINT_MAX_PAROLE_LIMITE    = 25

# Verifica dei numeri: ogni numero con almeno NUMERI_MIN_CIFRE cifre (o con
# decimali) presente nella sintesi deve comparire nell'abstract. NUMERI_AMMESSI
# sono i numeri che il testo può usare senza che compaiano (es. "IC al 95%").
NUMERI_VERIFICA     = True
NUMERI_MIN_CIFRE    = 2
NUMERI_AMMESSI      = {"95"}

_ACCENTATE = (
    "qualita|mortalita|morbilita|attivita|possibilita|probabilita|severita|"
    "disponibilita|capacita|complessita|sensibilita|specificita|variabilita|"
    "funzionalita|tossicita|velocita|comorbilita|fragilita|difficolta|opportunita|"
    "validita|utilita|gravita|novita|eterogeneita|generalizzabilita"
)
LINT_REGEX = [
    (r"\b(?!po\b)[A-Za-zÀ-ÿ]+'(?=\s|[.,;:)]|$)",
     "apostrofo al posto dell'accento (e', piu', qualita'...)"),
    (r"\b(?:piu|perche|poiche|affinche|cosi|gia|puo|cioe|nonche|benche|finche)\b",
     "accento mancante"),
    (r"\b(?:" + _ACCENTATE + r")\b", "accento mancante sulla vocale finale"),
    (r"\bsever[oaie]\b", "falso amico: severe = grave"),
    (r"\bevidenz[ae]\b", "falso amico: evidence = prove/evidenze"),
    (r"\bconsistent[ei]\b", "falso amico: consistent = coerente/costante"),
    (r"\beventualmente\b", "falso amico: eventually = infine"),
    (r"\bcompliance\b", "usa 'aderenza'"),
    (r"\bpre(?:isto|sto)spedalier\w*", "refuso: si scrive 'preospedaliero'"),
    (r"(?<!statisticamente )(?<!clinicamente )\bsignificativ(?:o|a|i|e|amente)\b",
     "'significativo' da solo: usa 'statisticamente significativo' o 'clinicamente rilevante'"),
    (r"\b(?:epinephrine|norepinephrine|acetaminophen|emergency department|ICU)\b",
     "termine inglese: usa il glossario (adrenalina, noradrenalina, paracetamolo, PS, terapia intensiva)"),
    (r"\b(?:CT|MRI)\b", "sigla inglese: usa TC / RM"),
    (r"dipartimento di emergenza", "usa 'Pronto Soccorso (PS)'"),
]

# ═══════════════════════════════════════════════════════════════════════════════
# [ATTIVO SUBITO] GLOSSARIO FARMACI, SIGLE E TERMINI
# ═══════════════════════════════════════════════════════════════════════════════
# Forma italiana da usare sempre. Finisce nel system prompt di sintesi/redazione.
GLOSSARIO = {
    # farmaci
    "epinephrine": "adrenalina", "norepinephrine": "noradrenalina",
    "acetaminophen": "paracetamolo", "heparin": "eparina",
    "low-molecular-weight heparin (LMWH)": "eparina a basso peso molecolare (EBPM)",
    "direct oral anticoagulants (DOAC)": "anticoagulanti orali diretti (DOAC)",
    "vasopressors": "vasopressori", "vasopressin": "vasopressina",
    "crystalloids / balanced crystalloids": "cristalloidi / cristalloidi bilanciati",
    "saline": "soluzione fisiologica", "tranexamic acid": "acido tranexamico",
    "succinylcholine": "succinilcolina", "ketamine": "ketamina",
    "dexamethasone": "desametasone", "hydrocortisone": "idrocortisone",
    "oxygen": "ossigeno", "alteplase / tenecteplase": "alteplase / tenecteplase",
    # sigle e luoghi
    "emergency department (ED)": "Pronto Soccorso (PS), mai 'dipartimento di emergenza'",
    "intensive care unit (ICU)": "terapia intensiva",
    "CT": "TC", "CT angiography": "angio-TC", "MRI": "RM",
    "point-of-care ultrasound (POCUS)": "ecografia point-of-care (POCUS)",
    "OHCA": "arresto cardiaco extraospedaliero (OHCA)",
    "IHCA": "arresto cardiaco intraospedaliero (IHCA)",
    "ROSC": "ritorno della circolazione spontanea (ROSC)",
    "cardiopulmonary resuscitation (CPR)": "rianimazione cardiopolmonare (RCP)",
    "noninvasive ventilation (NIV)": "ventilazione non invasiva (NIV)",
    "high-flow nasal oxygen (HFNO/HFNC)": "ossigenoterapia ad alti flussi (HFNO)",
    "rapid sequence intubation (RSI)": "intubazione a sequenza rapida (RSI)",
    "mechanical ventilation": "ventilazione meccanica",
    "ECMO": "ECMO (ossigenazione extracorporea a membrana)",
    # condizioni
    "atrial fibrillation (AF)": "fibrillazione atriale (FA)",
    "acute coronary syndrome (ACS)": "sindrome coronarica acuta (SCA)",
    "pulmonary embolism (PE)": "embolia polmonare (EP)",
    "venous thromboembolism (VTE)": "tromboembolismo venoso (TEV)",
    "acute kidney injury (AKI)": "danno renale acuto (AKI)",
    "diabetic ketoacidosis (DKA)": "chetoacidosi diabetica (DKA)",
    "traumatic brain injury (TBI)": "trauma cranico",
    "GI bleeding": "emorragia digestiva", "chest pain": "dolore toracico",
    "dyspnea": "dispnea", "length of stay": "durata della degenza",
    "in-hospital mortality": "mortalità intraospedaliera",
    "30-day mortality": "mortalità a 30 giorni",
}
_TESTO_GLOSSARIO = "\n".join(f"  {en} -> {it}" for en, it in GLOSSARIO.items())

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

# ── PROMPT DI SELEZIONE ───────────────────────────────────────────────────────
# Passaggio unico (comportamento attuale) e 2° passaggio del filtro a due fasi.
# Segnaposto: {n}, {n_finali}, {articoli}. L'elenco dei temi è inserito qui.
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
   Il campo TIPO riporta i PublicationType ufficiali di PubMed, ma sugli articoli
   appena usciti spesso c'è solo "Journal Article". Il campo DISEGNO, se presente,
   è il disegno riconosciuto dal testo dell'abstract: usali insieme come indizio
   del disegno e, se sono entrambi poco informativi, giudica dall'abstract.
   Guarda i RISULTATI e le CONCLUSIONI: un trial con endpoint primario non
   raggiunto che nelle conclusioni valorizza un sottogruppo non è un trial positivo.
4. NOVITA' - a parità di tutto il resto, preferisci ciò che cambia o ribalta una
   pratica consolidata rispetto a ciò che conferma quanto già noto.

VINCOLI DI COMPOSIZIONE:
- Massimo 2 articoli sullo stesso tema clinico (es. non 3 studi sulla sepsi).
- Massimo 2 articoli dalla stessa rivista.
- Almeno 2 articoli devono provenire da riviste con AREA "urgenza" o "generale":
  le riviste specialistiche pubblicano molto di più e tendono a monopolizzare la
  selezione, ma il lettore è un medico d'urgenza.
- Preferisci una selezione che copra aree cliniche diverse.

VOCABOLARIO DEI TEMI: il campo "tema" DEVE essere copiato esattamente da questa
lista chiusa. Usa "altro" solo se nessun tema è adatto. Non inventare sinonimi né
varianti: due articoli sullo stesso argomento devono avere lo stesso tema.
@@TEMI@@

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
Nei valori testuali NON usare mai virgolette doppie: per citare un nome, un termine o una frase usa gli apici singoli ('...') oppure le caporali «...». Una virgoletta doppia dentro il testo rompe il formato JSON.

[
  {{"pmid": "12345678", "tema": "sepsi-e-shock-settico", "perche": "motivo in max 15 parole"}},
  {{"pmid": "23456789", "tema": "trauma-cranico", "perche": "..."}}
]

Ordina dal più rilevante al meno rilevante.""".replace("@@TEMI@@", _ELENCO_TEMI)

# [NUOVO] 1° passaggio del filtro a due fasi. Segnaposto: {n}, {articoli}.
PROMPT_SCREENING = """COMPITO: SCREENING. Dalla lista di articoli candidati scegli i {n} che meritano una
valutazione approfondita per il digest settimanale. Non devi ordinarli: in un secondo
passaggio verranno riletti con l'abstract completo e ordinati.

In questo passaggio un falso positivo costa poco, mentre un articolo importante
scartato qui non si recupera più: nel dubbio, includi. Non escludere un articolo
pertinente solo perché la rivista è meno prestigiosa.

Tieni un articolo se la decisione descritta appartiene al medico d'urgenza nelle
prime ore (secondo il contesto del lettore) e se può cambiare triage, diagnosi,
terapia o destinazione del paziente. Scarta in particolare: case report, ricerca
preclinica, studi organizzativi su sistemi extraeuropei, decisioni che si prendono
dopo il ricovero in reparto o in terapia intensiva, chirurgia e oncologia elettive.
A parità di pertinenza preferisci i disegni più solidi (DISEGNO e TIPO) e le
conclusioni che poggiano sull'endpoint primario.

Evita che una sola area sia schiacciante: non più di 3 articoli sullo stesso tema.
Il campo "tema" va copiato esattamente da questa lista chiusa, "altro" solo se nulla
è adatto:
@@TEMI@@

ARTICOLI CANDIDATI:
{articoli}

FORMATO DI RISPOSTA - restituisci SOLO un array JSON valido, senza testo prima o dopo,
senza blocchi markdown, con al massimo {n} voci. Scegli esclusivamente PMID presenti
nella lista: non inventare né modificare PMID.
Nei valori testuali NON usare mai virgolette doppie: per citare un nome, un termine o una frase usa gli apici singoli ('...') oppure le caporali «...». Una virgoletta doppia dentro il testo rompe il formato JSON.

[
  {{"pmid": "12345678", "tema": "sepsi-e-shock-settico"}},
  {{"pmid": "23456789", "tema": "trauma-cranico"}}
]""".replace("@@TEMI@@", _ELENCO_TEMI)

# Regole di traduzione condivise. Vivono nel system prompt delle chiamate di sintesi.
REGOLE_TRADUZIONE = """REGOLE DI TRADUZIONE (obbligatorie):
- ORTOGRAFIA: usa gli accenti italiani corretti (è, à, ì, ò, ù, é). Non sostituirli
  mai con l'apostrofo: si scrive "qualità", non "qualita'"; "è", non "e'";
  "più", non "piu'"; "perché", non "perche'".
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
- GLOSSARIO FARMACI, SIGLE E LUOGHI (usa SEMPRE la forma italiana a destra; le sigle
  tra parentesi si usano dopo la prima comparsa estesa):
@@GLOSSARIO@@
- Lascia in inglese SOLO i termini realmente in uso in clinica italiana: ARDS, shock,
  outcome, endpoint, follow-up, weaning, screening, setting, cut-off, bias,
  propensity score, hazard, washout; usa "basale" per baseline.
- Riporta con precisione le misure statistiche: odds ratio (OR), hazard ratio (HR),
  rischio relativo (RR), intervallo di confidenza (IC) al 95%, valore di p.
- NUMERI: riporta cifre e separatore decimale ESATTAMENTE come nell'originale
  (punto decimale: 0.85; p<0.001). Non convertire il punto in virgola: ogni
  riscrittura di un numero è un'occasione di errore. Non alterare dosi,
  unità di misura, percentuali. Non calcolare nuovi numeri (differenze, percentuali,
  NNT) che l'abstract non riporta.
- Mantieni in forma originale le scale validate (GCS, SOFA, qSOFA, NEWS2, CURB-65).
- Espandi ogni acronimo alla prima comparsa, poi usa la sigla.
- Non usare mai "significativo" da solo: specifica "statisticamente significativo"
  oppure "clinicamente rilevante".
- Attieniti SOLO ai dati dell'abstract: non aggiungere, non inferire, non inventare.""".replace("@@GLOSSARIO@@", _TESTO_GLOSSARIO)

# Regola contro lo spin, condivisa da tutti i prompt di scrittura.
REGOLE_ENDPOINT = """REGOLA SULL'ENDPOINT PRIMARIO (obbligatoria, contro lo spin):
- Individua l'endpoint primario dichiarato dagli autori e il suo esito. Il risultato
  dell'endpoint primario viene SEMPRE prima di qualsiasi risultato secondario, di
  sottogruppo o post hoc.
- Se l'endpoint primario NON è stato raggiunto (differenza non statisticamente
  significativa, intervallo di confidenza che include l'assenza di effetto,
  non inferiorità non dimostrata, oppure effetto sfavorevole), la sintesi lo
  dichiara in modo esplicito entro la seconda frase, con il dato (misura di effetto,
  intervallo di confidenza, p), con formule come "l'endpoint primario non è stato
  raggiunto".
- I risultati secondari, di sottogruppo e post hoc compaiono solo DOPO, presentati
  come esplorativi o generatori di ipotesi. Non attribuire mai efficacia
  all'intervento sulla base di essi.
- Per risultati non statisticamente significativi sono vietati: "tendenza", "trend",
  "suggerisce un beneficio", "promettente", "mostra un vantaggio", "tende a ridurre".
- Se le conclusioni degli autori valorizzano un beneficio che l'endpoint primario non
  supporta, NON riprenderle: riporta ciò che i dati mostrano e segnala nel campo
  "limite" lo scarto fra endpoint primario e conclusioni.
- Un risultato positivo sull'endpoint primario si riporta insieme agli eventuali
  danni o eventi avversi rilevanti."""

# Definizione dei badge, condivisa. Il codice li fa rispettare comunque
# (forza_tipo_revisione e declassa_badge), qui si riduce il lavoro da correggere.
DEFINIZIONI_TIPO = """"tipo" - UNO SOLO fra questi valori, riportato esattamente così:
   "cambia-pratica" = lo studio modifica una condotta oggi diffusa. RISERVATO a studi
                      randomizzati e meta-analisi (anche con endpoint primario
                      negativo, se smentiscono una pratica diffusa). Mai per studi
                      osservazionali, modelli predittivi, analisi post hoc o secondarie.
   "conferma"       = rafforza una pratica già consolidata
   "controverso"    = risultati discordanti con evidenze o linee guida attuali
   "esplorativo"    = ipotesi generatrice, dati preliminari, campione insufficiente,
                      studio osservazionale con possibile confondimento che propone
                      un cambiamento
   "revisione"      = sintesi di letteratura senza dati primari originali: review
                      narrativa, scoping review, revisione sistematica, linea guida.
                      Usa SEMPRE questo valore per le sintesi di letteratura, anche
                      quando le conclusioni sono preliminari: "esplorativo" è
                      riservato agli studi con dati primari."""

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
# Persona, contesto e regole stabili vivono qui: sono identici a ogni esecuzione,
# mentre il messaggio utente contiene solo il compito e i dati della settimana.
SYSTEM_FILTRO = """Sei un medico strutturato di Pronto Soccorso italiano con esperienza
in medicina d'urgenza e cure critiche. Selezioni la letteratura settimanale per i
colleghi del tuo reparto.

""" + CONTESTO_PS

SYSTEM_SCREENING = SYSTEM_FILTRO   # [NUOVO] stesso ruolo e stesso contesto

# ── ESEMPI DI SINTESI ─────────────────────────────────────────────────────────
# [ATTIVO SUBITO] Tre sintesi modello per fissare registro, struttura e gestione
# dell'endpoint primario. ATTENZIONE: i dati degli studi sono FITTIZI, inventati per
# questo scopo (PMID 00000001-3): non descrivono trial reali. Servono solo a mostrare
# COME si scrive; le istruzioni dicono al modello di non riutilizzarne i contenuti.
ESEMPI = [
    {
        "abstract_en": (
            "BACKGROUND: High-flow nasal oxygen (HFNO) is increasingly used in the emergency "
            "department for acute hypoxaemic respiratory failure, but its effect on intubation is "
            "uncertain. METHODS: Multicentre, open-label randomised trial in 9 emergency departments. "
            "Adults with acute hypoxaemic respiratory failure (PaO2/FiO2 <= 200) were randomly assigned "
            "to HFNO or standard oxygen therapy. The primary outcome was intubation within 7 days. "
            "RESULTS: 612 patients were randomised (HFNO 306, standard oxygen 306). Intubation within "
            "7 days occurred in 43 patients (14.1%) in the HFNO group and 50 (16.3%) in the standard "
            "oxygen group (relative risk 0.86, 95% CI 0.60 to 1.24; P=0.42). Dyspnoea scores at 2 hours "
            "were lower with HFNO (mean difference -0.8 points, 95% CI -1.3 to -0.3). Mortality at 28 "
            "days did not differ (9.2% vs 10.1%). In a post hoc analysis of patients with PaO2/FiO2 <= 100, "
            "intubation was less frequent with HFNO (22% vs 38%). CONCLUSIONS: HFNO did not significantly "
            "reduce intubation, although it improved dyspnoea and may benefit patients with severe hypoxaemia."
        ),
        "output": {
            "pmid": "00000001",
            "titolo_it": "Ossigenoterapia ad alti flussi o ossigeno standard nell'insufficienza respiratoria ipossiemica acuta in Pronto Soccorso: studio randomizzato",
            "sintesi": (
                "Nell'insufficienza respiratoria ipossiemica acuta, l'ossigenoterapia ad alti flussi (HFNO) "
                "riduce il ricorso all'intubazione? Studio randomizzato multicentrico in aperto in 9 Pronto "
                "Soccorso (PS): 612 adulti con PaO2/FiO2 <= 200 assegnati a HFNO o a ossigenoterapia "
                "standard. L'endpoint primario, l'intubazione entro 7 giorni, non è stato raggiunto: "
                "14.1% con HFNO contro 16.3% con ossigeno standard (rischio relativo 0.86; intervallo di "
                "confidenza al 95% 0.60-1.24; p=0.42). La dispnea a 2 ore era minore con HFNO e la "
                "mortalità a 28 giorni non differiva (9.2% contro 10.1%). Il minor ricorso all'intubazione "
                "con PaO2/FiO2 <= 100 deriva da un'analisi post hoc: va intesa come ipotesi da verificare."
            ),
            "rilevanza": "Nel paziente ipossiemico in PS l'HFNO non riduce l'intubazione rispetto all'ossigeno standard: non giustifica un uso sistematico, ma può alleviare la dispnea.",
            "limite": "Studio in aperto; il beneficio nei casi più gravi proviene solo da un'analisi post hoc.",
            "tipo": "conferma",
        },
    },
    {
        "abstract_en": (
            "BACKGROUND: Diuretic dosing in acute heart failure is usually guided by clinical assessment. "
            "We tested whether lung-ultrasound-guided therapy improves outcomes. METHODS: In this "
            "multicentre, randomised, parallel-group trial with blinded outcome assessment, adults admitted "
            "through the emergency department with acute heart failure were assigned 1:1 to "
            "lung-ultrasound-guided diuretic therapy or usual care. The primary outcome was a composite of "
            "all-cause death or heart failure rehospitalisation at 90 days. RESULTS: Among 840 patients "
            "(mean age 79 years), the primary outcome occurred in 76 of 420 (18.1%) in the ultrasound group "
            "and 108 of 420 (25.7%) in the usual care group (hazard ratio 0.67, 95% CI 0.50 to 0.90; "
            "P=0.007). Death at 90 days occurred in 9.5% and 11.9%, respectively (HR 0.78, 95% CI 0.52 to "
            "1.17). Acute kidney injury was more frequent with ultrasound-guided therapy (14.8% vs 9.8%). "
            "CONCLUSIONS: Lung-ultrasound-guided diuretic therapy reduced death or rehospitalisation at 90 "
            "days, at the cost of more acute kidney injury."
        ),
        "output": {
            "pmid": "00000002",
            "titolo_it": "Terapia diuretica guidata dall'ecografia polmonare nello scompenso cardiaco acuto: studio randomizzato",
            "sintesi": (
                "La terapia diuretica guidata dall'ecografia polmonare migliora gli esiti dei pazienti con "
                "scompenso cardiaco acuto? Studio randomizzato multicentrico, con valutazione degli esiti in "
                "cieco: 840 adulti (età media 79 anni) ricoverati dal Pronto Soccorso, assegnati 1:1 a terapia "
                "guidata dall'ecografia polmonare o a trattamento abituale. L'endpoint primario composito, "
                "morte per qualsiasi causa o riospedalizzazione per scompenso a 90 giorni, si è verificato nel "
                "18.1% contro il 25.7% (hazard ratio 0.67; intervallo di confidenza al 95% 0.50-0.90; "
                "p=0.007). La mortalità a 90 giorni non differiva (9.5% contro 11.9%), mentre il danno renale "
                "acuto è stato più frequente con la strategia ecoguidata (14.8% contro 9.8%)."
            ),
            "rilevanza": "Nello scompenso acuto la titolazione dei diuretici con ecografia polmonare riduce morte o riospedalizzazione a 90 giorni: strategia da considerare, con monitoraggio della funzione renale.",
            "limite": "Medici e pazienti non erano in cieco; la mortalità da sola non differiva e il danno renale acuto è risultato più frequente.",
            "tipo": "cambia-pratica",
        },
    },
    {
        "abstract_en": (
            "BACKGROUND: The optimal timing of antibiotics in patients with suspected sepsis in the "
            "emergency department is debated. METHODS: Retrospective cohort study of 12,450 adults with "
            "suspected sepsis presenting to 14 emergency departments between 2019 and 2023. Patients "
            "receiving antibiotics within 1 hour of arrival were compared with those treated between 1 and "
            "3 hours using propensity score overlap weighting. The primary outcome was in-hospital "
            "mortality. RESULTS: Of 12,450 patients, 5,310 (42.7%) received antibiotics within 1 hour. "
            "In-hospital mortality was 9.8% in the early group and 11.6% in the later group (adjusted odds "
            "ratio 0.88, 95% CI 0.79 to 0.98). The association was stronger in patients with septic shock "
            "(adjusted OR 0.79, 95% CI 0.66 to 0.94). CONCLUSIONS: Earlier antibiotic administration was "
            "associated with lower in-hospital mortality; residual confounding cannot be excluded."
        ),
        "output": {
            "pmid": "00000003",
            "titolo_it": "Tempistica della terapia antibiotica nei pazienti con sospetta sepsi in Pronto Soccorso: studio di coorte retrospettivo",
            "sintesi": (
                "Nei pazienti con sospetta sepsi in Pronto Soccorso (PS), la somministrazione precoce degli "
                "antibiotici è associata a una minore mortalità? Studio di coorte retrospettivo in 14 PS: "
                "12,450 adulti, dei quali 5,310 (42.7%) hanno ricevuto antibiotici entro 1 ora dall'arrivo, "
                "confrontati con chi li ha ricevuti fra 1 e 3 ore mediante propensity score con pesi di "
                "sovrapposizione. La mortalità intraospedaliera, endpoint primario, era 9.8% contro 11.6% "
                "(odds ratio aggiustato 0.88; intervallo di confidenza al 95% 0.79-0.98), con un'associazione "
                "più marcata nello shock settico (odds ratio aggiustato 0.79; 0.66-0.94). Il dato è "
                "coerente con la pratica raccomandata, ma un'associazione osservazionale non dimostra causalità."
            ),
            "rilevanza": "Conferma il valore della precocità degli antibiotici in PS nella sepsi, soprattutto nello shock settico; non modifica la condotta già raccomandata.",
            "limite": "Disegno retrospettivo osservazionale: gli autori non escludono un confondimento residuo.",
            "tipo": "conferma",
        },
    },
]


def _json(o):
    return json.dumps(o, ensure_ascii=False, indent=2)


_NOTA_ESEMPI = (
    "ESEMPI DI SCRITTURA. I dati degli studi negli esempi sono FITTIZI: servono solo a "
    "mostrare registro, struttura e gestione dell'endpoint primario. Non riutilizzarne "
    "mai numeri, popolazioni o contenuti: ogni scheda si basa SOLO sull'articolo che ti "
    "viene fornito."
)

# Esempi nel formato "abstract -> scheda".
ESEMPI_DIRETTI = _NOTA_ESEMPI + "\n\n" + "\n\n".join(
    f"ESEMPIO {i}\nABSTRACT:\n{e['abstract_en']}\n\nRISPOSTA:\n{_json([e['output']])}"
    for i, e in enumerate(ESEMPI, 1)
)

_PERSONA_SINTESI = """Sei un medico di Pronto Soccorso italiano, esperto di letteratura
scientifica e di traduzione medico-scientifica dall'inglese all'italiano. Scrivi in
italiano, con linguaggio medico-scientifico preciso, del registro usato nelle riviste
italiane di area critica."""

SYSTEM_SINTESI = (
    _PERSONA_SINTESI + "\n\n" + REGOLE_TRADUZIONE + "\n\n" + ESEMPI_DIRETTI
)

# ── PROMPT DI SINTESI ─────────────────────────────────────────────────────────
# Sintesi di TUTTI gli articoli in una sola chiamata API.
# Segnaposto: {articoli}. [ATTIVO SUBITO] ora con titolo_it e regola sull'endpoint.
PROMPT_SINTESI_MULTI = """Analizza OGNI articolo della lista e produci per ciascuno
cinque campi:

0. "titolo_it" - traduzione fedele del titolo in italiano: stesso contenuto, nessuna
   aggiunta, terminologia clinica italiana.
1. "sintesi" - da 90 a 120 parole, che rispondano nell'ordine a: quesito clinico;
   disegno dello studio e popolazione, con numerosità; risultato dell'endpoint
   primario con i numeri chiave e la misura di effetto; altri risultati; ricaduta
   sulla pratica in PS/Area Critica.
2. "rilevanza" - UNA sola frase, massimo 30 parole, sulla ricaduta pratica concreta.
3. "limite" - UNA sola frase, massimo 25 parole, sul principale limite metodologico:
   monocentrico, non in cieco, endpoint surrogato, campione ridotto, popolazione
   selezionata, interruzione precoce, follow-up breve, conflitti di interesse.
   Per le sintesi di letteratura il limite riguarda il metodo della revisione:
   narrativa e non sistematica, selezione degli studi non riproducibile, assenza
   di valutazione formale della qualità, eterogeneità degli studi inclusi.
   Solo se l'abstract non consente davvero di identificare alcun limite, scrivi
   esattamente: "Limiti non desumibili dall'abstract."
4. @@TIPI@@

@@ENDPOINT@@

SE L'ABSTRACT E' ASSENTE O PRIVO DI RISULTATI NUMERICI: scrivi nella "sintesi" una
sola frase che lo dichiari esplicitamente, non inferire nulla dal titolo, usa
"esplorativo" come tipo.

ARTICOLI:
{articoli}

FORMATO DI RISPOSTA - restituisci SOLO un array JSON valido, senza testo prima o dopo,
senza blocchi markdown, con un oggetto per articolo, nello stesso ordine della lista.
Riporta il "pmid" esattamente come ti è stato fornito.
Nei valori testuali NON usare mai virgolette doppie: per citare un nome, un termine o una frase usa gli apici singoli ('...') oppure le caporali «...». Una virgoletta doppia dentro il testo rompe il formato JSON.

[
  {{
    "pmid": "12345678",
    "titolo_it": "...",
    "sintesi": "...",
    "rilevanza": "...",
    "limite": "...",
    "tipo": "cambia-pratica"
  }}
]""".replace("@@TIPI@@", DEFINIZIONI_TIPO).replace("@@ENDPOINT@@", REGOLE_ENDPOINT)

# Sintesi di un singolo articolo (fallback se dal multi manca qualcosa).
# Restituisce un array di UN elemento, così da riusare lo stesso parser del multi.
PROMPT_SINTESI = """Analizza l'articolo e produci cinque campi:

0. "titolo_it" - traduzione fedele del titolo in italiano, senza aggiunte.
1. "sintesi" - 90-120 parole: quesito clinico; disegno e popolazione con numerosità;
   risultato dell'endpoint primario con i numeri chiave; altri risultati; ricaduta
   per PS/Area Critica.
2. "rilevanza" - una sola frase, massimo 30 parole.
3. "limite" - una sola frase, massimo 25 parole, sul principale limite metodologico.
   Per le revisioni: limite del metodo della revisione (narrativa, selezione non
   riproducibile, eterogeneità degli studi). Solo se davvero non desumibile,
   scrivi esattamente: "Limiti non desumibili dall'abstract."
4. @@TIPI@@

@@ENDPOINT@@

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
Nei valori testuali NON usare mai virgolette doppie: per citare un nome, un termine o una frase usa gli apici singoli ('...') oppure le caporali «...». Una virgoletta doppia dentro il testo rompe il formato JSON.

[
  {{
    "pmid": "{pmid}",
    "titolo_it": "...",
    "sintesi": "...",
    "rilevanza": "...",
    "limite": "...",
    "tipo": "conferma"
  }}
]""".replace("@@TIPI@@", DEFINIZIONI_TIPO).replace("@@ENDPOINT@@", REGOLE_ENDPOINT)

# ═══════════════════════════════════════════════════════════════════════════════
# PATH FILE
# ═══════════════════════════════════════════════════════════════════════════════
DIR_BASE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(DIR_BASE, "newsletter.log")


# ═══════════════════════════════════════════════════════════════════════════════
# [NUOVO] FUNZIONI DI SUPPORTO — pure, senza rete né file
# ═══════════════════════════════════════════════════════════════════════════════
_DISEGNI_RE = [
    (chiave, [re.compile(p, re.IGNORECASE) for p in pattern])
    for chiave, pattern in DISEGNI
]
_LINT_RE = [(re.compile(p, re.IGNORECASE), msg) for p, msg in LINT_REGEX]
# Il Lancet scrive i decimali con il punto in alto ("1·28"): va trattato come ".".
_RE_NUMERO = re.compile(r"\d+(?:[.,\u00b7\u2219\u22c5]\d+)*")


def rileva_disegno(titolo="", abstract="", pubtypes=None):
    """Restituisce (chiave_disegno, fonte) con fonte in {"pubtype","testo","n/d"}.
    Il PublicationType esatto ha la precedenza; altrimenti vale la prima regola del
    testo che combacia, nell'ordine di DISEGNI."""
    presenti = set(pubtypes or [])
    for tipo, chiave in PUBTYPE_A_DISEGNO.items():
        if tipo in presenti:
            return chiave, "pubtype"
    testo = f"{titolo or ''}. {abstract or ''}"
    for chiave, regole in _DISEGNI_RE:
        if any(r.search(testo) for r in regole):
            return chiave, "testo"
    return DISEGNO_NON_DETERMINATO, "n/d"


def declassa_badge(tipo, disegno):
    """'cambia-pratica' solo per RCT e meta-analisi. Con un disegno riconosciuto ma
    non abilitato il badge diventa BADGE_DECLASSATO; con disegno non determinato non
    si tocca nulla (meglio un badge non verificabile che uno punito a torto)."""
    if (tipo == "cambia-pratica"
            and disegno != DISEGNO_NON_DETERMINATO
            and disegno not in BADGE_CAMBIA_PRATICA_DISEGNI):
        return BADGE_DECLASSATO
    return tipo


def normalizza_tema(tema):
    """Riporta a TEMA_FALLBACK ogni tema fuori dal vocabolario chiuso."""
    t = (tema or "").strip().lower()
    return t if t in TEMI else TEMA_FALLBACK


def compatta_abstract(testo, massimo):
    """Accorcia un abstract tenendo l'inizio (quesito, disegno) e soprattutto la fine
    (risultati e conclusioni), che una semplice troncatura da sinistra perde."""
    testo = (testo or "").strip()
    if len(testo) <= massimo:
        return testo
    testa = int(massimo * 0.4)
    coda = massimo - testa - 5
    return f"{testo[:testa].rstrip()} [...] {testo[-coda:].lstrip()}"


def parametri_esearch(giorni=None):
    """Parametri (da inviare in POST) per cercare in PubMed gli articoli delle riviste
    target inseriti negli ultimi `giorni`, con i pubtype indesiderati esclusi nella
    query stessa. Si interroga per abbreviazione NLM ([ta]), che evita i problemi di
    ISSN/eISSN delle riviste solo online."""
    giorni = giorni or GIORNI_RICERCA
    riviste = " OR ".join(f'"{r["nlmta"]}"[ta]' for r in RIVISTE)
    esclusi = " OR ".join(f'"{t}"[pt]' for t in sorted(PUBTYPE_ESCLUSI))
    parametri = {
        "db": "pubmed",
        "term": f"({riviste}) NOT ({esclusi})",
        "datetype": ESEARCH_DATETYPE,
        "reldate": str(giorni),
        "retmax": str(ESEARCH_RETMAX),
        "retmode": "json",
        "tool": NCBI_TOOL,
    }
    if NCBI_EMAIL:
        parametri["email"] = NCBI_EMAIL
    return parametri


def _canon_numero(tok):
    # "12,450" (migliaia all'inglese) -> "12450"; "0,5" o "0.5" -> "0.5"
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", tok):
        return tok.replace(",", "")
    return re.sub("[,\u00b7\u2219\u22c5]", ".", tok)


def numeri_non_verificati(testo, abstract):
    """Numeri della sintesi (con decimali o almeno NUMERI_MIN_CIFRE cifre) che non
    compaiono nell'abstract. I numeri in NUMERI_AMMESSI sono esenti."""
    nell_abstract = {_canon_numero(t) for t in _RE_NUMERO.findall(abstract or "")}
    mancanti = []
    for tok in _RE_NUMERO.findall(testo or ""):
        c = _canon_numero(tok)
        if c in NUMERI_AMMESSI or c in nell_abstract:
            continue
        if "." not in c and len(c) < NUMERI_MIN_CIFRE:
            continue
        if c not in mancanti:
            mancanti.append(c)
    return mancanti


def controlla_lint(sintesi, rilevanza="", limite="", abstract=""):
    """Elenco dei problemi deterministici di una scheda (vuoto = tutto a posto).
    I messaggi sono scritti per essere passati così come sono al redattore."""
    problemi = []
    n = len((sintesi or "").split())
    lo, hi = LINT_PAROLE_SINTESI
    if n < lo or n > hi:
        problemi.append(f"la sintesi ha {n} parole: deve averne da {lo} a {hi}")
    if len((rilevanza or "").split()) > LINT_MAX_PAROLE_RILEVANZA:
        problemi.append(f"la rilevanza supera {LINT_MAX_PAROLE_RILEVANZA} parole")
    if len((limite or "").split()) > LINT_MAX_PAROLE_LIMITE:
        problemi.append(f"il limite supera {LINT_MAX_PAROLE_LIMITE} parole")
    for campo, testo in (("sintesi", sintesi), ("rilevanza", rilevanza), ("limite", limite)):
        for rx, msg in _LINT_RE:
            for m in rx.finditer(testo or ""):
                p = f'{campo}: "{m.group(0)}" - {msg}'
                if p not in problemi:
                    problemi.append(p)
    if NUMERI_VERIFICA and abstract:
        dubbi = numeri_non_verificati(f"{sintesi} {rilevanza} {limite}", abstract)
        if dubbi:
            problemi.append(
                "numeri non presenti nell'abstract (riportali come nell'originale o "
                "eliminali): " + ", ".join(dubbi)
            )
    return problemi


def valida_config():
    mancanti = []
    if not ANTHROPIC_API_KEY: mancanti.append("ANTHROPIC_API_KEY")
    # In dry run non si invia nulla: le credenziali Gmail e i destinatari
    # non servono, così la prova gira anche in locale senza token.
    if not DRY_RUN and not SOLO_FACEBOOK:
        if not GMAIL_USER:  mancanti.append("GMAIL_USER")
        if not GMAIL_TOKEN: mancanti.append("GMAIL_TOKEN")
        if not DESTINATARI: mancanti.append("DESTINATARI")
    if mancanti:
        raise RuntimeError(
            f"Variabili d'ambiente mancanti: {', '.join(mancanti)}.\n"
            "Configurale prima di eseguire lo script."
        )
    return True
