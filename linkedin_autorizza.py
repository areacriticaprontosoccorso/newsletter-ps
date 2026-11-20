"""
Ottiene il token di LinkedIn per pubblicare sul tuo profilo (da eseguire sul tuo computer).
Il token dura 60 giorni: quando arriva l'avviso per email, si riesegue questo script.

Prima, una volta sola, su https://www.linkedin.com/developers/apps:
  1. «Create app» (serve una pagina LinkedIn da associare: va bene qualunque pagina di cui sei
     amministratore; l'app pubblica comunque sul tuo profilo personale);
  2. scheda «Products»: aggiungi «Share on LinkedIn» e «Sign In with LinkedIn using OpenID Connect»;
  3. scheda «Auth»: in «Authorized redirect URLs» aggiungi  http://localhost:8765/callback
     e copia «Client ID» e «Primary Client Secret».

Poi, sul computer (nessuna libreria da installare):
    python linkedin_autorizza.py CLIENT_ID CLIENT_SECRET

Si apre il browser: accedi a LinkedIn e consenti. Lo script stampa i tre valori da salvare come
secret: LINKEDIN_TOKEN, LINKEDIN_AUTORE, LINKEDIN_SCADENZA. Conviene salvarli come secret
dell'ORGANIZZAZIONE (areacriticaprontosoccorso -> Settings -> Secrets and variables -> Actions):
cosi' valgono per i tre repository e ogni due mesi si aggiornano in un posto solo.

Copia identica nei tre repo.
"""
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, timedelta

PORTA = 8765
RITORNO = f"http://localhost:{PORTA}/callback"
SCOPE = "openid profile w_member_social"


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    client_id, client_secret = sys.argv[1], sys.argv[2]
    stato = secrets.token_urlsafe(16)
    ricevuto = {}

    class Gestore(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ricevuto.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>Fatto: puoi chiudere questa pagina e tornare al terminale.</p>".encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("localhost", PORTA), Gestore)
    threading.Thread(target=server.handle_request, daemon=True).start()
    url = "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": RITORNO,
        "scope": SCOPE, "state": stato})
    print("Se il browser non si apre, vai a:\n" + url)
    webbrowser.open(url)
    while "code" not in ricevuto and "error" not in ricevuto:
        threading.Event().wait(0.5)
    server.server_close()
    if ricevuto.get("state") != stato or "code" not in ricevuto:
        print("Accesso non riuscito:", ricevuto.get("error_description") or ricevuto.get("error") or "stato non valido")
        return 1

    corpo = urllib.parse.urlencode({"grant_type": "authorization_code", "code": ricevuto["code"],
                                    "redirect_uri": RITORNO, "client_id": client_id,
                                    "client_secret": client_secret}).encode()
    with urllib.request.urlopen(urllib.request.Request(
            "https://www.linkedin.com/oauth/v2/accessToken", data=corpo,
            headers={"Content-Type": "application/x-www-form-urlencoded"})) as r:
        t = json.loads(r.read())
    with urllib.request.urlopen(urllib.request.Request(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {t['access_token']}"})) as r:
        io = json.loads(r.read())
    scadenza = date.today() + timedelta(seconds=int(t.get("expires_in", 5184000))) - timedelta(days=1)
    print(f"\nProfilo: {io.get('name', '')}\n\nSalva questi tre secret:\n")
    print(f"LINKEDIN_TOKEN    = {t['access_token']}")
    print(f"LINKEDIN_AUTORE   = urn:li:person:{io['sub']}")
    print(f"LINKEDIN_SCADENZA = {scadenza.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
