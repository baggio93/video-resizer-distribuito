import os
import time
import uuid
import requests
import subprocess
import shutil
import signal
import sys

# --- VARIABILI GLOBALI ---
SERVER_URL = "http://127.0.0.1:50123"
CLIENT_ID = str(uuid.uuid4())
keep_running = True  
server_config = {}   

# --- GESTIONE DELLA CHIUSURA SICURA (CTRL+C) ---
def signal_handler(sig, frame):
    """Gestisce la terminazione del processo (Ctrl+C) permettendo una chiusura senza file corrotti."""
    global keep_running
    if keep_running:
        print("\n\n[ATTENZIONE] RICEVUTO COMANDO DI SPEGNIMENTO (Ctrl+C)")
        print("[ATTESA] Sto terminando il pezzo in corso. Lo inviero' al server e poi mi spegnero' in sicurezza...")
        print("         (Premi di nuovo Ctrl+C se vuoi forzare l'uscita brutale perdendo il lavoro)\n")
        keep_running = False
    else:
        print("\n[ERRORE] Chiusura forzata!")
        sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
# -----------------------------------------------

def clean_leftover_files():
    """Rimuove eventuali file temporanei interrotti rimasti da esecuzioni passate del client."""
    print("--- PULIZIA INIZIALE ---")
    files_to_check = ["local_benchmark.mp4", "local_benchmark_out.mp4"]
    for file in os.listdir('.'):
        if file in files_to_check or file.startswith("chunk_in_") or file.startswith("chunk_out_"):
            try:
                os.remove(file)
                print(f"[{time.strftime('%H:%M:%S')}] Rimosso file residuo: {file}")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] Impossibile rimuovere il file {file}: {e}")
    print("Pulizia completata.\n------------------------\n")

def scarica_configurazioni():
    """Contatta il server per recuperare le impostazioni aggiornate sulle estensioni e i parametri."""
    global server_config
    try:
        print("Scaricamento configurazioni aggiornate dal server...")
        cfg_res = requests.get(f"{SERVER_URL}/config")
        cfg_res.raise_for_status()
        server_config = cfg_res.json()
    except Exception as e:
        print(f"Impossibile contattare il server su {SERVER_URL}: {e}")
        sys.exit(1)

def run_benchmark():
    """Esegue un test iniziale di rendering per calcolare la velocità del client e lo registra al server."""
    print("\n--- AVVIO BENCHMARK/REGISTRAZIONE ---")
    try:
        res = requests.get(f"{SERVER_URL}/benchmark", stream=True)
        res.raise_for_status()
        with open("local_benchmark.mp4", "wb") as f:
            shutil.copyfileobj(res.raw, f)

        print("Esecuzione test di conversione (FFmpeg)...")
        start_time = time.time()
        out_ext = server_config.get("OUTPUT_EXT", ".mp4")
        bench_out = f"local_benchmark_out{out_ext}"
        
        args = ["ffmpeg", "-y", "-i", "local_benchmark.mp4"] + server_config["RESIZE_ARGS"] + [bench_out]
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elapsed = time.time() - start_time

        print("Invio risultato al server...")
        requests.post(f"{SERVER_URL}/benchmark_result", json={"client_id": CLIENT_ID, "benchmark_time": elapsed})

        if os.path.exists("local_benchmark.mp4"): os.remove("local_benchmark.mp4")
        if os.path.exists(bench_out): os.remove(bench_out)
        
        print(f"--- BENCHMARK CONCLUSO: {elapsed:.2f} sec ---\n")
    except Exception as e:
        print(f"Errore critico durante il benchmark: {e}")
        time.sleep(5)

if __name__ == "__main__":
    print("--- Configurazione Client Video Resizer ---")
    server_ip = input("Inserisci l'indirizzo IP o hostname del server (predefinito 127.0.0.1): ").strip() or "127.0.0.1"
    server_port = input("Inserisci la porta del server (predefinita 50123): ").strip() or "50123"
    SERVER_URL = f"http://{server_ip}:{server_port}"
    
    clean_leftover_files()
    
    scarica_configurazioni()
    run_benchmark()

    print("In attesa di video da elaborare. Premi Ctrl+C per fermare il client in modo sicuro.\n")
    
    while keep_running:
        try:
            res = requests.get(f"{SERVER_URL}/get_chunk", params={"client_id": CLIENT_ID}, timeout=5)
            
            if res.status_code == 400:
                print(f"[{time.strftime('%H:%M:%S')}] [ATTENZIONE] Il server ha perso la mia registrazione (Errore 400).")
                print("Eseguo nuovamente la registrazione (Auto-Recovery)...")
                scarica_configurazioni() 
                run_benchmark()
                continue
            
            if res.status_code == 404:
                for _ in range(5):
                    if not keep_running: break
                    time.sleep(1)
                continue
            
            res.raise_for_status()
            chunk_id = res.headers.get("X-Chunk-Id")
            
            if not chunk_id:
                time.sleep(5)
                continue

            in_ext = server_config.get("INPUT_EXT", ".mp4")
            out_ext = server_config.get("OUTPUT_EXT", ".mp4")
            
            input_chunk = f"chunk_in_{chunk_id}{in_ext}"
            output_chunk = f"chunk_out_{chunk_id}{out_ext}"

            with open(input_chunk, "wb") as f:
                for chunk in res.iter_content(chunk_size=8192):
                    f.write(chunk)

            print(f"[{time.strftime('%H:%M:%S')}] Ricevuto pezzo ID {chunk_id}. Avvio elaborazione...")
            
            args = ["ffmpeg", "-y", "-i", input_chunk] + server_config["RESIZE_ARGS"] + [output_chunk]
            subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if not os.path.exists(output_chunk):
                print(f"[{time.strftime('%H:%M:%S')}] Errore di conversione. Riprovero' al prossimo ciclo.")
                time.sleep(5)
                continue

            print(f"[{time.strftime('%H:%M:%S')}] Pezzo ID {chunk_id} convertito. Upload al server in corso...")
            
            with open(output_chunk, "rb") as f:
                upload_res = requests.post(
                    f"{SERVER_URL}/upload_chunk",
                    data={"client_id": CLIENT_ID, "chunk_id": chunk_id},
                    files={"file": f}
                )
            
            if upload_res.status_code != 200:
                print(f"[{time.strftime('%H:%M:%S')}] Errore durante l'upload: {upload_res.text}")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] Pezzo {chunk_id} consegnato con successo!")

            if os.path.exists(input_chunk): os.remove(input_chunk)
            if os.path.exists(output_chunk): os.remove(output_chunk)

        except requests.exceptions.RequestException as e:
            print(f"[{time.strftime('%H:%M:%S')}] Errore di connessione al server: {e}. Ritento...")
            for _ in range(5):
                if not keep_running: break
                time.sleep(1)
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] Errore inaspettato: {e}")
            for _ in range(5):
                if not keep_running: break
                time.sleep(1)
                
    print("\n[CHIUSURA] Client terminato in modo sicuro. Nessun lavoro e' andato perso! A presto.")
