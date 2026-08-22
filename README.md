# EM Weekly Digest — newsletter-ps

Newsletter settimanale di medicina d'urgenza per il Pronto Soccorso di San Giovanni Bosco, Torino (Area Critica e Pronto Soccorso, ASL Città di Torino).

Ogni lunedì il sistema interroga i feed PubMed di una lista di riviste, filtra e sintetizza gli articoli più rilevanti per il PS con un modello Claude, e distribuisce il risultato per **email** (Gmail) e su **Facebook** (schede PNG, un post al giorno nei feriali).

---

## Cosa fa, in ordine

1. **Raccolta** — per ogni rivista in `RIVISTE` scarica il feed RSS PubMed degli ultimi 7 giorni (`GIORNI_RICERCA`; fallback a 14 se la settimana è troppo povera). Scarta subito i tipi non pertinenti per titolo (`ESCLUSIONI_TITOLO`) e per PublicationType (`PUBTYPE_ESCLUSI`: lettere, editoriali, case report, ecc.).
2. **Filtro** — invia al modello fino a `MAX_CANDIDATI_PROMPT` (150) candidati e ne fa selezionare `ARTICOLI_RICHIESTI` (8): i primi `ARTICOLI_FINALI` (5) vanno nel digest, gli altri 3 restano come **riserva** per rispettare i vincoli.
3. **Composizione** (`componi_digest`) — applica i vincoli editoriali:
   - almeno `MIN_EM_GEN` (2) articoli da riviste di urgenza/generaliste (`GRUPPI_PRIORITARI = {"em", "gen"}`);
   - massimo `MAX_PER_TEMA` (2) articoli sullo stesso tema clinico;
   - se un vincolo impone uno scambio, entra la rivista di **fascia** più alta ed esce quella più bassa (vedi *Fasce editoriali*).
4. **Sintesi** — genera per ogni articolo una sintesi in italiano, la rilevanza per il PS e i limiti dello studio. Assegna il **badge** metodologico (vedi sotto). Se meno di `MINIMO_ARTICOLI` (3) articoli ottengono una sintesi valida, **l'invio viene annullato** (meglio un run fallito e visibile che un digest svuotato).
5. **Distribuzione** — costruisce l'HTML dell'email, genera le schede PNG con Playwright, invia l'email ai `DESTINATARI` e pubblica su Facebook.

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

## Trappole note

- **Token Gmail OAuth scaduto** — se l'app OAuth di Google è in modalità *Testing*, il refresh token scade dopo 7 giorni. Serve pubblicare l'app (o rigenerare il token). È stata la causa di invii falliti in passato.
- **Cron GitHub disattivato** — i cron schedulati di GitHub Actions si autodisabilitano dopo 60 giorni di inattività del repo. Per questo lo scheduling è delegato a cron-job.org via `workflow_dispatch`.
- **ID pagina Facebook via portfolio** — l'ID pagina si ottiene interrogando l'API tramite l'ID del business portfolio, non direttamente.
- **API Gruppi Facebook deprecata** (aprile 2024) — la pubblicazione nei gruppi è solo manuale. `post_facebook.txt` esiste anche per copiare a mano la didascalia in un gruppo.
- **Installazione Playwright fragile** — in CI conviene `timeout-minutes` e `continue-on-error` sullo step di install per non bloccare l'invio del lunedì.
- **`claude-sonnet-5` e `temperature`/prefill** — vedi sopra: HTTP 400 garantito.
- **Modello Sonnet** — le versioni delle librerie Google sono pinnate nel workflow apposta: un loro breaking change non deve poter bloccare l'invio.

---

## File del repo

| File | Ruolo |
| --- | --- |
| `newsletter_rss.py` | Pipeline completa (raccolta → filtro → sintesi → email/Facebook). |
| `config.py` | Tutti i parametri: riviste, vincoli, prompt, secrets, branding. |
| `.github/workflows/newsletter.yml` | Workflow `workflow_dispatch` (chiamato da cron-job.org). |
| `logo.jpg` | Logo referenziato da `LOGO_URL` nelle email. |

## Fasce editoriali

Il campo `fascia` (1–5, 5 = massimo) è un peso editoriale, **non** l'impact factor grezzo: usare l'IF come numero (NEJM ~96 contro Ann Emerg Med ~5) schiaccerebbe le riviste di urgenza, l'opposto di ciò che serve al digest. La fascia interviene **solo negli spareggi di composizione**: non entra nel prompt di filtro, non tocca il taglio dei candidati e non riordina mai il digest finale, che resta in ordine di rilevanza deciso dal modello. Nella maggior parte dei run non ha alcun effetto; quando interviene, il log riporta lo scambio e il profilo delle fasce.
