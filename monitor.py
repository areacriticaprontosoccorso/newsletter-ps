"""
Monitor dei tre repo (EM, POCUS, NIV): gira da qui, schedulato (vedi .github/workflows/monitor.yml).

  python monitor.py controlla     avvisa per email se il run del giorno non e' partito, e' fallito
                                  o e' bloccato (cron-job.org che non spara, run appeso)
  python monitor.py statistiche   email con il riepilogo della settimana: post Instagram e Facebook
                                  e iscritti delle newsletter

Le email vanno a AVVISI_EMAIL (con GMAIL_USER / GMAIL_TOKEN, come la newsletter EM). Mai eccezioni
non gestite: un errore di rete o di permessi diventa una riga nell'email, non un run rosso muto.
Il riepilogo non contiene indirizzi email degli iscritti, solo conteggi.
"""
import base64
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avviso  # noqa: E402

log = logging.getLogger("monitor")
ROMA = ZoneInfo("Europe/Rome")
ORG = "areacriticaprontosoccorso"

# giorno: 0 = lunedi. soglia_min: oltre questi minuti un run ancora in corso e' considerato bloccato
# (i timeout dei workflow sono 90 min per EM, 15 per POCUS e NIV).
NEWSLETTER = [
    {"nome": "EM Weekly Digest", "repo": f"{ORG}/newsletter-ps", "workflow": "newsletter.yml",
     "giorno": 0, "soglia_min": 120},
    {"nome": "POCUS Weekly Digest", "repo": f"{ORG}/newsletter-pocus", "workflow": "newsletter_pocus.yml",
     "giorno": 2, "soglia_min": 40, "iscritti": "subscribers.json"},
    {"nome": "NIV Weekly Digest", "repo": f"{ORG}/Newsletter-NIV", "workflow": "newsletter_NIV.yml",
     "giorno": 4, "soglia_min": 40, "iscritti": "subscribers.json"},
]
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
VERSIONI_GRAPH = ("v25.0", "v23.0", "v21.0")      # la prima che risponde


# ────────────────────────────────── rete ──────────────────────────────────

def http_json(url, intestazioni=None, timeout=30):
    """GET -> JSON. Solleva RuntimeError con il messaggio dell'API (senza token)."""
    richiesta = urllib.request.Request(url, headers=intestazioni or {})
    try:
        with urllib.request.urlopen(richiesta, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            corpo = json.loads(e.read().decode("utf-8"))
            err = corpo.get("error", corpo)
            testo = f"{err.get('message', err)} (codice {err.get('code', e.code)})" if isinstance(err, dict) else str(err)
        except Exception:
            testo = f"HTTP {e.code}"
        raise RuntimeError(avviso._pulisci(testo)) from None
    except Exception as e:
        raise RuntimeError(avviso._pulisci(str(e))) from None


def gh(percorso, ambiente=None, **params):
    amb = ambiente if ambiente is not None else os.environ
    intestazioni = {"Accept": "application/vnd.github+json", "User-Agent": "newsletter-monitor"}
    if amb.get("GITHUB_TOKEN"):
        intestazioni["Authorization"] = f"Bearer {amb['GITHUB_TOKEN']}"
    url = f"https://api.github.com/{percorso}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return http_json(url, intestazioni)


# ───────────────────────── controllo del mancato avvio ─────────────────────────

def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def valuta_runs(nome, runs, adesso, soglia_min):
    """Problemi di un repo, dato l'elenco dei suoi run (di oggi, ora di Roma) e l'ora attuale (UTC)."""
    oggi = adesso.astimezone(ROMA).date()
    di_oggi = [r for r in runs if _t(r["created_at"]).astimezone(ROMA).date() == oggi]
    if not di_oggi:
        return [f"{nome}: oggi non e' partito nessun run (cron-job.org non ha lanciato il workflow?)"]
    if any(r.get("conclusion") == "success" for r in di_oggi):
        return []
    ultimo = max(di_oggi, key=lambda r: r["created_at"])
    if ultimo.get("status") in ("in_progress", "queued", "waiting", "pending", "requested"):
        minuti = (adesso - _t(ultimo.get("run_started_at") or ultimo["created_at"])).total_seconds() / 60
        if minuti > soglia_min:
            return [f"{nome}: il run e' ancora in corso dopo {minuti:.0f} minuti (limite {soglia_min}): bloccato? {ultimo['html_url']}"]
        return []
    return [f"{nome}: l'ultimo run di oggi e' finito con '{ultimo.get('conclusion')}': {ultimo['html_url']}"]


def controlla(adesso=None, ambiente=None, leggi_runs=None):
    """Elenco dei problemi per le newsletter attese oggi."""
    adesso = adesso or datetime.now(timezone.utc)
    giorno = adesso.astimezone(ROMA).weekday()
    leggi_runs = leggi_runs or (lambda n: gh(f"repos/{n['repo']}/actions/workflows/{n['workflow']}/runs",
                                              ambiente, event="workflow_dispatch", per_page=20)["workflow_runs"])
    problemi = []
    for n in NEWSLETTER:
        if n["giorno"] != giorno:
            continue
        try:
            problemi += valuta_runs(n["nome"], leggi_runs(n), adesso, n["soglia_min"])
        except Exception as e:
            problemi.append(f"{n['nome']}: impossibile leggere i run di GitHub ({e})")
    return problemi


# ─────────────────────────────── statistiche ───────────────────────────────

_GIORNI_BREVI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def _g(dt):
    return f"{_GIORNI_BREVI[dt.weekday()]} {dt:%d/%m}"


def _n(v):
    return "n/d" if v is None else f"{v:,}".replace(",", ".")


def graph(percorso, token, versione, **params):
    params["access_token"] = token
    return http_json(f"https://graph.facebook.com/{versione}/{percorso}?" + urllib.parse.urlencode(params))


def versione_funzionante(percorso_prova, token):
    ultimo = None
    for v in VERSIONI_GRAPH:
        try:
            graph(percorso_prova, token, v, fields="id")
            return v
        except RuntimeError as e:
            ultimo = e
    raise ultimo


def _metriche_media(media_id, token, versione, metriche):
    """Una richiesta per metrica: quelle non supportate dal tipo di post non fanno cadere le altre."""
    out = {}
    for m in metriche:
        try:
            d = graph(f"{media_id}/insights", token, versione, metric=m)["data"]
            out[m] = d[0]["values"][0]["value"] if d else None
        except RuntimeError as e:
            out[m] = None
            if "permission" in str(e).lower() or "(codice 10)" in str(e) or "(codice 200)" in str(e):
                out["_permesso"] = True
    return out


def stat_instagram(ig_id, token, da, versione=None):
    """Account e post Instagram degli ultimi giorni. Mai eccezioni: {'errore': ...} se non va."""
    try:
        v = versione or versione_funzionante(ig_id, token)
        conto = graph(ig_id, token, v, fields="username,followers_count,media_count")
        media = graph(f"{ig_id}/media", token, v, limit=50, fields=(
            "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count"))["data"]
    except Exception as e:
        return {"errore": str(e)}
    post, senza_permesso = [], False
    for m in media:
        if _t(m["timestamp"]) < da:
            continue
        ins = _metriche_media(m["id"], token, v, ("views", "reach", "saved", "shares"))
        senza_permesso = senza_permesso or bool(ins.pop("_permesso", False))
        post.append({"quando": _t(m["timestamp"]).astimezone(ROMA), "tipo": "Reel" if m.get("media_product_type") == "REELS"
                     else ("Carosello" if m.get("media_type") == "CAROUSEL_ALBUM" else m.get("media_type", "Post")),
                     "testo": (m.get("caption") or "").strip().split("\n")[0][:60], "link": m.get("permalink", ""),
                     "like": m.get("like_count"), "commenti": m.get("comments_count"), **ins})
    post.sort(key=lambda p: p["quando"])
    return {"utente": conto.get("username", ""), "follower": conto.get("followers_count"), "post": post,
            "senza_permesso": senza_permesso}


def stat_facebook(page_id, token, da, versione=None):
    try:
        v = versione or versione_funzionante(page_id, token)
        pagina = graph(page_id, token, v, fields="name,followers_count,fan_count")
        posti = graph(f"{page_id}/published_posts", token, v, limit=50, since=int(da.timestamp()), fields=(
            "id,message,created_time,permalink_url,shares,reactions.summary(total_count).limit(0),"
            "comments.summary(total_count).limit(0)"))["data"]
    except Exception as e:
        return {"errore": str(e)}
    post = []
    for p in posti:
        vis = _metriche_media(p["id"], token, v, ("post_media_view",)).get("post_media_view")
        post.append({"quando": _t(p["created_time"]).astimezone(ROMA),
                     "testo": (p.get("message") or "").strip().split("\n")[0][:60], "link": p.get("permalink_url", ""),
                     "reazioni": p.get("reactions", {}).get("summary", {}).get("total_count"),
                     "commenti": p.get("comments", {}).get("summary", {}).get("total_count"),
                     "condivisioni": (p.get("shares") or {}).get("count"), "visualizzazioni": vis})
    post.sort(key=lambda p: p["quando"])
    return {"nome": pagina.get("name", ""), "follower": pagina.get("followers_count", pagina.get("fan_count")),
            "post": post}


def _elenco_iscritti(testo_json):
    return [s for s in json.loads(testo_json) if "@" in str(s.get("email", ""))]


def stat_iscritti(n, da, ambiente=None, leggi_file=None):
    """Iscritti di una newsletter con subscribers.json: totale, nuovi, disiscritti stimati nella settimana.
    Si legge la storia di git del file: totale di una settimana fa = file all'ultimo commit prima di `da`."""
    try:
        leggi = leggi_file or (lambda rif: base64.b64decode(gh(
            f"repos/{n['repo']}/contents/{n['iscritti']}", ambiente, ref=rif)["content"]).decode("utf-8"))
        ora = _elenco_iscritti(leggi("main"))
        nuovi = sum(1 for s in ora if s.get("subscribed_at") and _t(s["subscribed_at"]) >= da)
        prima = None
        try:
            commit = gh(f"repos/{n['repo']}/commits", ambiente, path=n["iscritti"],
                        until=da.strftime("%Y-%m-%dT%H:%M:%SZ"), per_page=1)
            if commit:
                prima = len(_elenco_iscritti(leggi(commit[0]["sha"])))
        except Exception as e:
            log.warning(f"Storico iscritti {n['nome']} non letto: {e}")
        out = {"totale": len(ora), "nuovi": nuovi, "prima": prima}
        if prima is not None:
            out["usciti"] = max(0, nuovi - (len(ora) - prima))
        return out
    except Exception as e:
        return {"errore": str(e)}


def conta_destinatari_em(ambiente=None):
    amb = ambiente if ambiente is not None else os.environ
    n = len([e for e in amb.get("DESTINATARI", "").split(",") if e.strip()])
    return n or None


def _riga_post_ig(p):
    return (f"  {_g(p['quando'])} {p['tipo']:<9} visualizzazioni {_n(p.get('views')):>6} · copertura {_n(p.get('reach')):>6}"
            f" · salvati {_n(p.get('saved')):>4} · condivisi {_n(p.get('shares')):>4} · like {_n(p['like']):>4}"
            f" · commenti {_n(p['commenti']):>3}\n      {p['testo']}")


def _riga_post_fb(p):
    return (f"  {_g(p['quando'])} visualizzazioni {_n(p['visualizzazioni']):>6} · reazioni {_n(p['reazioni']):>4}"
            f" · commenti {_n(p['commenti']):>3} · condivisioni {_n(p['condivisioni']):>3}\n      {p['testo']}")


def componi_statistiche(da, a, ig, fb, iscritti, destinatari_em):
    """(oggetto, testo) del riepilogo settimanale."""
    r = [f"Riepilogo della settimana dal {da.astimezone(ROMA):%d/%m} al {a.astimezone(ROMA):%d/%m/%Y}", ""]
    r.append("ISCRITTI")
    for nome, s in iscritti.items():
        if "errore" in s:
            r.append(f"  {nome}: non letti ({s['errore']})")
        else:
            seg = f"  {nome}: {_n(s['totale'])} iscritti · {_n(s['nuovi'])} nuovi nella settimana"
            if s.get("usciti") is not None:
                seg += f" · circa {_n(s['usciti'])} disiscritti (variazione netta {s['totale'] - s['prima']:+d})"
            r.append(seg)
    r.append(f"  EM Weekly Digest: {_n(destinatari_em)} destinatari (lista del secret DESTINATARI; la variazione "
             "nel tempo non si legge, la lista non ha storia)")
    r += ["", "INSTAGRAM" + (f" @{ig['utente']} · {_n(ig.get('follower'))} follower" if "errore" not in ig else "")]
    if "errore" in ig:
        r.append(f"  Non disponibile: {ig['errore']}")
    else:
        r += [_riga_post_ig(p) for p in ig["post"]] or ["  Nessun post nella settimana."]
        if ig["senza_permesso"]:
            r.append("  NB: visualizzazioni, copertura, salvati e condivisi non letti: al token manca il permesso "
                     "instagram_manage_insights.")
    r += ["", "FACEBOOK" + (f" {fb['nome']} · {_n(fb.get('follower'))} follower" if "errore" not in fb else "")]
    if "errore" in fb:
        r.append(f"  Non disponibile: {fb['errore']}")
    else:
        r += [_riga_post_fb(p) for p in fb["post"]] or ["  Nessun post nella settimana."]
        if fb["post"] and all(p["visualizzazioni"] is None for p in fb["post"]):
            r.append("  NB: visualizzazioni non lette (permesso read_insights o metrica non disponibile).")
    return f"Statistiche della settimana {a.astimezone(ROMA):%d/%m}", "\n".join(r)


def statistiche(adesso=None, ambiente=None):
    amb = ambiente if ambiente is not None else os.environ
    a = adesso or datetime.now(timezone.utc)
    da = a - timedelta(days=7)
    ig_id, page_id = amb.get("IG_USER_ID", ""), amb.get("FB_PAGE_ID", "")
    tok_ig = amb.get("IG_PAGE_TOKEN") or amb.get("FB_PAGE_TOKEN", "")
    tok_fb = amb.get("FB_PAGE_TOKEN") or tok_ig
    ig = stat_instagram(ig_id, tok_ig, da) if ig_id and tok_ig else {"errore": "IG_USER_ID o token mancanti"}
    fb = stat_facebook(page_id, tok_fb, da) if page_id and tok_fb else {"errore": "FB_PAGE_ID o token mancanti"}
    iscritti = {n["nome"]: stat_iscritti(n, da, amb) for n in NEWSLETTER if n.get("iscritti")}
    return componi_statistiche(da, a, ig, fb, iscritti, conta_destinatari_em(amb))


# ─────────────────────────────────── main ───────────────────────────────────

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    comando = argv[0] if argv else ""
    if comando == "controlla":
        problemi = controlla()
        if not problemi:
            log.info("Controllo: tutto regolare")
            return 0
        testo = ("Controllo dell'avvio delle newsletter:\n\n" + "\n".join(f"  - {p}" for p in problemi))
        avviso.invia("[Newsletter] Un invio non e' partito o e' fallito", testo)
        log.warning(testo)
        return 0
    if comando == "statistiche":
        oggetto, testo = statistiche()
        log.info(testo)
        avviso.invia(oggetto, testo)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    raise SystemExit(main())
