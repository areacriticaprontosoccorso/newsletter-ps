# EM Weekly Digest — newsletter-ps

Newsletter settimanale di medicina d'urgenza per il Pronto Soccorso di San Giovanni Bosco, Torino (Area Critica e Pronto Soccorso, ASL Città di Torino).

Ogni lunedì il sistema interroga i feed PubMed di una lista di riviste, filtra e sintetizza gli articoli più rilevanti per il PS con un modello Claude, e distribuisce il risultato per **email** (Gmail, con le schede e il podcast di due minuti in allegato) e su **Facebook** (schede PNG, un post al giorno nei feriali; il lunedì col video del podcast).

---

## Cosa fa, in ordine

1. **Raccolta** — per ogni rivista in `RIVISTE` scarica il feed RSS PubMed degli ultimi 7 giorni (`GIORNI_RICERCA`; fallback a 14 se la settimana è troppo povera). Scarta subito i tipi non pertinenti per titolo (`ESCLUSIONI_TITOLO`) e per PublicationType (`PUBTYPE_ESCLUSI`: lettere, editoriali, case report, ecc.).
2. **Filtro** — invia al modello fino a `MAX_CANDIDATI_PROMPT` (150) candidati e ne fa selezionare `ARTICOLI_RICHIESTI` (8): i primi `ARTICOLI_FINALI` (5) vanno nel digest, gli altri 3 restano come **riserva** per rispettare i vincoli.
3. **Composizione** (`componi_digest`) — applica i vincoli editoriali:
   - almeno `MIN_EM_GEN` (2) articoli da riviste di urgenza/generaliste (`GRUPPI_PRIORITARI = {"em", "gen"}`);
   - massimo `MAX_PER_TEMA` (2) articoli sullo stesso tema clinico;
   - se un vincolo impone uno scambio, entra la rivista di **fascia** più alta ed esce quella più bassa (vedi *Fasce editoriali*).
4. **Sintesi** — genera per ogni articolo una sintesi in italiano, la rilevanza per il PS e i limiti dello studio. Assegna il **badge** metodologico (vedi sotto). Se meno di `MINIMO_ARTICOLI` (3) articoli ottengono una sintesi valida, **l'invio viene annullato** (meglio un run fallito e visibile che un digest svuotato).
5. **Distribuzione** — genera le schede PNG con Playwright e il podcast, costruisce l'HTML dell'email, invia l'email ai `DESTINATARI` (schede e MP3 in allegato) e pubblica su Facebook.
6. **Podcast** — prima dell'email, `podcast.py` produce un podcast di due minuti: l'MP3 parte allegato alla mail e il video esce nel post Facebook del lunedì (vedi *Podcast settimanale*).

---

## Come gira

Il workflow è `.github/workflows/newsletter.yml` (`python newsletter_rss.py`).

**Non esiste uno `schedule`.** Il trigger è esclusivamente `workflow_dispatch`, chiamato dall'esterno da **cron-job.org**, che invia:

```
POST https://api.github.com/repos/areacriticaprontosoccorso/newsletter-ps/actions/workflows/newsletter.yml/dispatches
body: {"ref": "main"}
```

cron-job.org è configurato su **lunedì 13:00 ora di Roma**. Gestisce lui il fuso e l'ora legale, cosa che i cron di GitHub Actions (solo UTC) non fanno. Questa scelta evita anche la disattivazione automatica del workflow dopo 60 giorni di inattività, che colpisce i cron schedulati di GitHub.

Un `concurrency` group (`newsletter-invio`, `cancel-in-progress: false`) impedisce invii doppi se cron-job.org spara due trigger ravvicinati.

Al termine, il workflow carica sempre (anche in caso di errore) un artifact `digest-<run_number>` con `anteprima_digest.html`, `newsletter.log`, la cartella `immagini/` e `post_facebook.txt`, conservato 14 giorni.

---

## Secrets richiesti

Da impostare in *Settings → Secrets and variables → Actions*. I valori non sono mai nel codice.

| Secret | Uso |
| --- | --- |
| `ANTHROPIC_API_KEY` | Chiamate al modello (filtro + sintesi). Sempre obbligatorio. |
| `GMAIL_USER` | Indirizzo Gmail mittente. |
| `GMAIL_TOKEN` | Token OAuth2 completo (JSON) per l'invio via Gmail. |
| `DESTINATARI` | Indirizzi email separati da virgola. |
| `FB_PAGE_ID` | ID della pagina Facebook. |
| `FB_PAGE_TOKEN` | Page access token Facebook. |

`GMAIL_USER`, `GMAIL_TOKEN` e `DESTINATARI` sono richiesti solo per l'invio reale: in `DRY_RUN` e `SOLO_FACEBOOK` non vengono controllati (`valida_config`).

---

## Modalità di esecuzione

Entrambe si attivano dagli input del `workflow_dispatch` (avvio manuale da *Actions → Run workflow*) o via variabile d'ambiente (`1`, `true`, `yes`, `si`).

- **`DRY_RUN`** — esegue tutta la pipeline ma **non invia nulla**. Scrive `anteprima_digest.html`, le schede PNG in `immagini/` e le didascalie in `post_facebook.txt`. È il modo per controllare la selezione prima di un invio reale. Nel log compare il *profilo delle fasce* del digest.
- **`SOLO_FACEBOOK`** — pubblica su Facebook ma **non invia l'email**. Ha effetto solo se `DRY_RUN` è spento (la prova a vuoto non pubblica mai).

In locale:

```bash
pip install google-auth-oauthlib==1.4.0 google-auth-httplib2==0.4.0 \
            google-api-python-client==2.198.0 playwright==1.49.0
playwright install --with-deps chromium

DRY_RUN=1 ANTHROPIC_API_KEY=sk-... python newsletter_rss.py
```

---

## Configurazione (`config.py`)

Tutti i parametri stanno in `config.py`. I principali:

**Riviste** — lista `RIVISTE`, ognuna con `nome`, `nlmta`, `issn`, `gruppo` (`em` = urgenza, `gen` = generale, `spec` = specialistica) e `fascia` (1–5). Aggiungere una rivista = aggiungere una riga. Se il feed torna vuoto, quasi sempre l'ISSN è sbagliato (per le riviste solo online provare l'eISSN): il codice logga l'errore invece di far sparire la rivista in silenzio.

**Selezione e vincoli** — `GIORNI_RICERCA` (7), `ARTICOLI_FINALI` (5), `ARTICOLI_RICHIESTI` (8), `MIN_EM_GEN` (2), `MAX_PER_TEMA` (2), `MINIMO_ARTICOLI` (3), `MAX_CANDIDATI_PROMPT` (150).

**Badge metodologici** — assegnati in codice dai PublicationType di PubMed, non chiesti al modello:
- `PUBTYPE_REVISIONE` → badge "revisione" (Review, Systematic Review, Guideline, Consensus…). La *Meta-Analysis* è esclusa apposta: produce stime proprie e può essere "cambia-pratica".
- `PUBTYPE_PRIORITARI` → segnalati al filtro come indizio di qualità (RCT, Meta-Analysis, Multicenter, Fase III, Practice Guideline).

**Modello** — `ANTHROPIC_MODEL = "claude-sonnet-5"`. ⚠️ Questo modello **rifiuta** il parametro `temperature` e il prefill del turno assistant (HTTP 400): non reintrodurli.

**Facebook** — `FB_API_VERSION` (v21.0), `FB_UN_POST_AL_GIORNO` (True: un articolo per post, non un elenco unico), `FB_ORA` (16), `FB_MINUTO` (0), `FB_GIORNI_FERIALI` (lun–ven), `FB_FUSO` (Europe/Rome). I post vengono programmati sugli slot feriali successivi.

**Branding** — `NOME_NEWSLETTER`, `NOME_SERVIZIO`, `LOGO_URL` (deve essere un URL pubblico: le email non supportano immagini locali o base64), colori.

---

## Podcast settimanale (`podcast.py`)

Ogni lunedì il workflow produce un podcast di circa **due minuti** (una voce, italiano, riviste e termini inglesi in pronuncia inglese): l'**MP3 parte allegato alla mail** ai `DESTINATARI` e il **video** esce su Facebook nel post del primo articolo.

**Quando parte la mail** — l'orario del run lo decide cron-job.org (lunedì 13:00), non il codice. Dentro il run, `newsletter_rss.py` genera schede e podcast (`genera_podcast`, un processo separato) e solo dopo invia la mail: con la cache dei modelli calda la mail esce circa 20–30 minuti dopo l'avvio. Se il podcast fallisce, o supera `PODCAST_TIMEOUT_MAIL` (45 minuti), il processo viene chiuso e la mail parte **senza** audio, come prima. Se Gmail rifiuta l'invio con l'allegato audio, si riprova subito senza. Nella mail una riga sotto l'intestazione segnala l'allegato.

1. **Copione** — una chiamata a Claude (`PODCAST_PROMPT`) scrive un paragrafo per articolo a partire dall'archivio del digest. Intro e chiusura sono fisse (`PODCAST_INTRO`, `PODCAST_CHIUSURA`).
2. **Controllo** — ogni numero del copione deve comparire nell'abstract o nella sintesi dello stesso articolo (ammessi arrotondamenti, e scarti piccoli solo dopo "circa", "oltre", "quasi"); lunghezza fra `PODCAST_PAROLE_MIN` e `PODCAST_PAROLE_MAX`, frasi brevi. Se qualcosa non torna Claude riscrive (massimo 3 tentativi), poi il podcast salta.
3. **Voce** — [Chatterbox](https://github.com/resemble-ai/chatterbox) gira sul runner, senza API. I termini fra `[[...]]` nel copione sono generati in voce inglese e uniti alla frase senza pause; le parole inglesi brevi (`trial`, `cluster`…) usano la grafia fonetica di `PODCAST_GRAFIA_FONETICA`. Il timbro è quello di `podcast_voce.wav` (una voce sintetica, non di una persona).
4. **Verifica** — ogni frase viene ritrascritta con Whisper e confrontata col testo; sotto `PODCAST_SOGLIA_WHISPER` si rigenera (fino a `PODCAST_TENTATIVI` volte).
5. **Uscite** — `podcast/podcast-sNN-AAAA.mp3`, il video `.mp4` (copertina + schede degli articoli che scorrono mentre se ne parla), `copione.txt` ed `esito.json` con il punteggio di ogni frase: tutto nell'artifact del run.

**Post del lunedì** — con il podcast attivo, `newsletter_rss.py` programma come sempre i post 2–5 ma **rinvia il primo** (`podcast/primo_post.json`). Lo pubblica `python podcast.py pubblica`, allo stesso orario (`FB_ORA`): video del podcast + didascalia del primo articolo, preceduta da `PODCAST_FB_TESTA`. **Se il podcast non è pronto, il post esce con l'immagine come prima**: il lunedì non resta mai scoperto. Anche il messaggio per il gruppo (`post_gruppo.txt`) viene scritto qui, col link al post.

**Tempi e costi** — circa 20–30 minuti in più per corsa (la prima più lunga: scarica ~3 GB di modelli, poi in cache). Costo API: una chiamata in più a settimana (pochi centesimi; di più solo se il controllo chiede correzioni). I minuti di GitHub Actions su repo pubblico sono gratuiti.

**Disattivarlo** — input `podcast` del *Run workflow* su `false`, oppure `PODCAST_ABILITATO = False` in `config.py`: tutto torna esattamente come prima.

**Prova in locale** con un copione già scritto (salta Claude):

```bash
pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
pip install chatterbox-tts==0.1.7 faster-whisper==1.2.1 num2words==0.5.14 pyloudnorm==0.2.0 soundfile "setuptools<81"
DRY_RUN=1 python podcast.py genera --archivio archivio/digest-s44-2026.json --copione copione.json
DRY_RUN=1 python podcast.py pubblica     # scrive solo post_facebook_podcast.txt
```

---

## Trappole note

- **Token Gmail OAuth scaduto** — se l'app OAuth di Google è in modalità *Testing*, il refresh token scade dopo 7 giorni. Serve pubblicare l'app (o rigenerare il token). È stata la causa di invii falliti in passato.
- **Cron GitHub disattivato** — i cron schedulati di GitHub Actions si autodisabilitano dopo 60 giorni di inattività del repo. Per questo lo scheduling è delegato a cron-job.org via `workflow_dispatch`.
- **ID pagina Facebook via portfolio** — l'ID pagina si ottiene interrogando l'API tramite l'ID del business portfolio, non direttamente.
- **API Gruppi Facebook deprecata** (aprile 2024) — la pubblicazione nei gruppi è solo manuale. `post_facebook.txt` esiste anche per copiare a mano la didascalia in un gruppo.
- **Installazione Playwright fragile** — in CI conviene `timeout-minutes` e `continue-on-error` sullo step di install per non bloccare l'invio del lunedì.
- **`claude-sonnet-5` e `temperature`/prefill** — vedi sopra: HTTP 400 garantito.
- **Podcast: memoria e tempo** — Chatterbox su CPU usa ~6 GB di RAM e circa 4–8 secondi di calcolo per secondo di audio (circa 17 minuti per un podcast sul runner). Oltre `PODCAST_TIMEOUT_MAIL` viene interrotto: la mail parte senza audio e il post del lunedì esce con l'immagine. Le dipendenze si installano **prima** di *Esegui newsletter*; se quel passo fallisce, la newsletter non tenta nemmeno il podcast.
- **Podcast: video su Facebook** — il caricamento usa `graph-video.facebook.com` con lo stesso token della Pagina (permesso `pages_manage_posts`). Se Facebook rifiuta il video, il post esce con l'immagine e il log riporta l'errore.
- **Modello Sonnet** — le versioni delle librerie Google sono pinnate nel workflow apposta: un loro breaking change non deve poter bloccare l'invio.

---

## File del repo

| File | Ruolo |
| --- | --- |
| `newsletter_rss.py` | Pipeline completa (raccolta → filtro → sintesi → email/Facebook). |
| `config.py` | Tutti i parametri: riviste, vincoli, prompt, secrets, branding. |
| `.github/workflows/newsletter.yml` | Workflow `workflow_dispatch` (chiamato da cron-job.org). |
| `podcast.py` | Podcast settimanale (copione, voce, video) e post Facebook del lunedì. |
| `podcast_voce.wav` | Campione della voce del podcast (sintetica). |
| `logo.jpg` | Logo referenziato da `LOGO_URL` nelle email. |
| `logo-medicina-urgenza.png` | Logo usato nella copertina del video del podcast. |

## Fasce editoriali

Il campo `fascia` (1–5, 5 = massimo) è un peso editoriale, **non** l'impact factor grezzo: usare l'IF come numero (NEJM ~96 contro Ann Emerg Med ~5) schiaccerebbe le riviste di urgenza, l'opposto di ciò che serve al digest. La fascia interviene **solo negli spareggi di composizione**: non entra nel prompt di filtro, non tocca il taglio dei candidati e non riordina mai il digest finale, che resta in ordine di rilevanza deciso dal modello. Nella maggior parte dei run non ha alcun effetto; quando interviene, il log riporta lo scambio e il profilo delle fasce.
