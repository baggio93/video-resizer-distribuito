#  Video Resizer Distribuito

Un sistema client-server ad alte prestazioni per l'elaborazione e il ridimensionamento distribuito di file video. 
Il sistema divide automaticamente i video di grandi dimensioni in piccoli segmenti (chunk), li distribuisce a una rete di computer "lavoratori" (client) per l'elaborazione parallela, e infine li ricompone nel file finale, gestendo il tutto tramite una comoda Dashboard Web.

## Funzionalità Principali

- **Elaborazione Distribuita:** Aggiungi potenza di calcolo semplicemente avviando il file `client.py` su altri computer della tua rete.
- **Dashboard Web Interattiva:** Monitora l'avanzamento globale, il tempo stimato (ETA), i client attivi e gestisci le priorità dei video in tempo reale.
- **Sicurezza Integrata:** Accesso alla dashboard protetto da password con salvataggio tramite Hash SHA-256.
- **Graceful Shutdown:** Se fermi un client (Ctrl+C), questo finirà il lavoro in corso e lo consegnerà prima di spegnersi, evitando file corrotti.
- **Gestione Automatica degli Errori:** Se un client si disconnette improvvisamente o va in errore, il suo pezzo viene riassegnato in automatico a un altro client.
- **Configurazione Live:** Modifica i parametri di FFmpeg, la durata dei chunk o la password direttamente dalla dashboard senza riavviare il server.

---

## Prerequisiti

Per far funzionare sia il Server che i Client, è necessario avere installato:

1. **Python 3.8** (o versioni successive)
2. **FFmpeg**: Deve essere installato a livello di sistema operativo e raggiungibile tramite le variabili d'ambiente (il comando `ffmpeg` deve funzionare se digitato nel terminale).

### Installazione di FFmpeg
- **Windows:** Scarica l'eseguibile da [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) e aggiungilo alla variabile di sistema `PATH`.
- **Linux (Debian/Ubuntu):** `sudo apt update && sudo apt install ffmpeg`
- **macOS:** `brew install ffmpeg`

---

##  Installazione delle dipendenze

Clona questo repository sul tuo computer, apri il terminale nella cartella del progetto e installa le librerie Python necessarie usando `pip`:

```bash
pip install fastapi uvicorn requests python-multipart
