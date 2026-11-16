"""
Ripubblica solo i Reel falliti di una corsa gia' fatta, senza rimandare mail, Telegram, carosello
o post di Facebook gia' usciti.

Workflow EM, opzione `ripubblica_reel` (con `ripubblica_cosa` e, facoltativo, `run_id`):
  1. scarica l'artifact digest-N della corsa (quella indicata, oppure l'ultima corsa vera riuscita
     di questa settimana: si riconosce dall'archivio, che ha "dry_run": false);
  2. rimette nella cartella del repo archivio/, immagini/ e podcast/ di quella corsa;
  3. rigenera le schede e i Reel con il codice attuale (codifica compresa) e pubblica solo le voci
     di RIPUBBLICA_COSA: ig_schede, fb_schede, x, ig_podcast, fb_podcast (carosello: 'carosello').

Gli articoli, il podcast e le didascalie sono quelli della corsa originale: nessuna chiamata a
Claude o a Gemini. Con DRY_RUN=1 prepara i file senza pubblicare.

Uso: python ripubblica.py   (servono GH_TOKEN e GITHUB_REPOSITORY, come nel workflow)
"""
import glob
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import date

import config as cfg
import newsletter_rss as nr

log = logging.getLogger("ripubblica")

VOCI = ("carosello", "ig_schede", "fb_schede", "x", "ig_podcast", "fb_podcast")
PREDEFINITE = "ig_schede,ig_podcast"
CARTELLE = ("archivio", "immagini", "podcast")


def voci_da_ripubblicare(testo):
    """L'insieme delle voci valide in `testo` (separate da virgole o spazi). Solleva se ne manca una."""
    voci = {v.strip().lower() for v in (testo or PREDEFINITE).replace(",", " ").split() if v.strip()}
    sconosciute = voci - set(VOCI)
    if sconosciute or not voci:
        raise ValueError(f"voci non valide: {', '.join(sorted(sconosciute)) or '(nessuna)'} "
                         f"(ammesse: {', '.join(VOCI)})")
    return voci


def _gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def archivio_della_corsa(cartella):
    """(percorso, dati) dell'archivio JSON nella cartella di un artifact, o (None, None)."""
    for p in sorted(glob.glob(os.path.join(cartella, "**", "archivio", "digest-s*.json"), recursive=True)):
        try:
            with open(p, encoding="utf-8") as f:
                return p, json.load(f)
        except (OSError, ValueError):
            continue
    return None, None


def corsa_valida(dati, settimana=None):
    """True se l'archivio e' di una corsa vera (non di prova) e, se indicata, della settimana giusta."""
    if not dati or dati.get("dry_run", True) or not dati.get("pubblicati"):
        return False
    return settimana is None or int(dati.get("settimana", -1)) == int(settimana)


def scarica(run_id=None, repo=None, base=None):
    """Scarica l'artifact della corsa da ripubblicare. Restituisce la radice dell'artifact (la
    cartella che contiene archivio/). Con run_id usa quella corsa; senza, cerca fra le ultime
    corse riuscite quella vera di questa settimana."""
    repo = repo or os.environ["GITHUB_REPOSITORY"]
    base = base or tempfile.mkdtemp(prefix="ripubblica-")
    if run_id:
        candidati, settimana = [str(run_id)], None
    else:
        corse = json.loads(_gh("run", "list", "-R", repo, "--workflow", "newsletter.yml", "--status",
                               "success", "-L", "20", "--json", "databaseId"))
        attuale = os.environ.get("GITHUB_RUN_ID", "")
        candidati = [str(c["databaseId"]) for c in corse if str(c["databaseId"]) != attuale]
        settimana = date.today().isocalendar()[1]
    for rid in candidati:
        dest = os.path.join(base, rid)
        try:
            _gh("run", "download", rid, "-R", repo, "-D", dest)
        except subprocess.CalledProcessError as e:
            log.info(f"  corsa {rid}: artifact non scaricabile ({(e.stderr or '').strip()[:120]})")
            continue
        percorso, dati = archivio_della_corsa(dest)
        if corsa_valida(dati, settimana):
            log.info(f"Corsa da ripubblicare: {rid} (settimana {dati['settimana']}/{dati['anno']}, "
                     f"{len(dati['pubblicati'])} articoli)")
            return os.path.dirname(os.path.dirname(percorso))
        log.info(f"  corsa {rid}: " + ("prova o altra settimana, la salto" if dati else "senza archivio"))
        shutil.rmtree(dest, ignore_errors=True)
    raise RuntimeError("nessuna corsa vera di questa settimana da ripubblicare"
                       + (f" (run {run_id})" if run_id else ""))


def rimetti_file(radice, dest="."):
    """Copia archivio/, immagini/ e podcast/ della corsa nella cartella del repo. I file del podcast
    diventano 'recenti' (la copertina vale solo se ha meno di 6 ore)."""
    for c in CARTELLE:
        src = os.path.join(radice, c)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, c), dirs_exist_ok=True)
    adesso = time.time()
    for p in glob.glob(os.path.join(dest, "podcast", "*")):
        os.utime(p, (adesso, adesso))


def main():
    # il log (schermo e newsletter.log) e' gia' impostato da newsletter_rss
    voci = voci_da_ripubblicare(os.environ.get("RIPUBBLICA_COSA", ""))
    log.info(f"=== Ripubblicazione: {', '.join(sorted(voci))}"
             f"{' (prova a vuoto)' if cfg.DRY_RUN else ''} ===")
    radice = scarica(os.environ.get("RUN_ID", "").strip() or None)
    rimetti_file(radice)
    _, dati = archivio_della_corsa(".")
    articoli = dati["pubblicati"]
    wl = {"settimana": dati["settimana"], "anno": dati["anno"]}
    immagini = sorted(glob.glob(os.path.join(cfg.IMG_DIR,
                                             f"digest-s{str(wl['settimana']).zfill(2)}-*.png")))
    cfg.SOLO_REEL = voci
    nr.pubblica_instagram(articoli, immagini, wl, dry_run=cfg.DRY_RUN)
    log.info("=== Ripubblicazione finita ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
