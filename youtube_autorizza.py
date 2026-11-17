"""
Ottiene il refresh token di YouTube (da eseguire UNA VOLTA, sul tuo computer, non in GitHub).

Prima, nella Google Cloud Console (lo stesso progetto di Gmail va bene):
  1. «API e servizi» -> «Libreria» -> abilita «YouTube Data API v3»;
  2. «Schermata consenso OAuth»: stato «In produzione» (in «Test» il token scade dopo 7 giorni);
  3. «Credenziali» -> «Crea credenziali» -> «ID client OAuth» -> tipo «App desktop»; scarica il JSON.

Poi, sul computer:
    pip install google-auth-oauthlib
    python youtube_autorizza.py client_secret.json

Si apre il browser: accedi con l'account proprietario del CANALE YouTube (se il canale e' un
«account del brand», scegli quel canale nella schermata) e consenti. Lo script stampa i tre valori
da salvare come secret del repository (Settings -> Secrets and variables -> Actions):
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN. Non salvare ne' condividere altro.

Copia identica nei tre repo.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = ["https://www.googleapis.com/auth/youtube.upload",
         "https://www.googleapis.com/auth/youtube.readonly"]


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    flusso = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPE)
    cred = flusso.run_local_server(port=0, access_type="offline", prompt="consent")
    if not cred.refresh_token:
        print("Google non ha restituito il refresh token: revoca l'accesso dell'app da "
              "https://myaccount.google.com/permissions e rifai la procedura.")
        return 1
    print("\nSalva questi tre secret nel repository:\n")
    print(f"YT_CLIENT_ID     = {cred.client_id}")
    print(f"YT_CLIENT_SECRET = {cred.client_secret}")
    print(f"YT_REFRESH_TOKEN = {cred.refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
