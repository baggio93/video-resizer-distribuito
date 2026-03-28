import os
import time
import uuid
import requests
import subprocess
import shutil
import signal  # <--- NUOVA LIBRERIA PER INTERCETTARE CTRL+C
import sys     # <--- NUOVA LIBRERIA PER L'USCITA

# --- VARIABILI GLOBALI ---
SERVER_URL = "http://127.0.0.1:50123"
CLIENT_ID = str(uuid.uuid4())
keep_running = True  # Variabile che tiene in vita il ciclo del client

# --- GESTIONE DELLA CHIUSURA SICURA (CTRL+C) ---
def signal_handler(sig, frame):
    global keep_running
    if keep_running:
        print("\n\n⚠️ RICEVUTO COMANDO DI SPEGNIMENTO (Ctrl+C) ⚠️")
        print("⏳ Sto terminando il pezzo in corso. Lo invierò al server e poi mi spegnerò in sicurezza...")
        print("   (Premi di nuovo Ctrl+C se vuoi forzare l'uscita brutale perdendo il lavoro)\n")
        keep_running = False
    else:
        print("\n💀 Chiusura forzata!")
        sys.exit(0)

# Diciamo a Python di usare la nostra funzione quando l'utente preme Ctrl+C
signal.signal(signal.SIGINT, signal_handler)
# -----------------------------------------------

def clean_leftover_files():
    """Pulisce i file temporanei rimasti da esecuzioni precedenti."""
    print("--- PULIZIA INIZIALE ---")
    files_to_check = ["local_benchmark.mp4", "local_benchmark_out.mp4"]
    for file in os.listdir('.'):
        if file in files_to_check or file.startswith("chunk_in_") or file.startswith("chunk_out_"):
            try:
                os.remove(file)
                print(f"[{time.strftime('%H:%M:%S')}] Rimosso vecchio file residuo: {file}")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] Impossibile rimuovere il file {file}: {e}")
    print("Pulizia completata.\n------------------------\n")

if __name__ == "__main__":
    print("--- Configurazione Client Video Resizer ---")
    server_ip = input("Inserisci l'indirizzo IP o hostname del server (predefinito 127.0.0.1): ").strip() or "127.0.0.1"
    server_port = input("Inserisci la porta del server (predefinita 50123): ").strip() or "50123"
    SERVER_URL = f"http://{server_ip}:{server_port}"
    
    clean_leftover_files()
    
    # 1. Recupera la configurazione dal server
    try:
        print("Connessione al server per scaricare le configurazioni...")
        cfg_res = requests.get(f"{SERVER_URL}/config")
        cfg_res.raise_for_status()
        server_config = cfg_res.json()
    except Exception as e:
        print(f"Impossibile contattare il server su {SERVER_URL}: {e}")
        sys.exit(1)

    # 2. Esecuzione del Benchmark iniziale
    print("\n--- AVVIO BENCHMARK ---")
    try:
        print("Scaricamento file di test dal server...")
        res = requests.get(f"{SERVER_URL}/benchmark", stream=True)
        res.raise_for_status()
        with open("local_benchmark.mp4", "wb") as f:
            shutil.copyfileobj(res.raw, f)

        print("Esecuzione test di conversione (FFmpeg)...")
        start_time = time.time()
        args = ["ffmpeg", "-y", "-i", "local_benchmark.mp4"] + server_config["RESIZE_ARGS"] + ["local_benchmark_out.mp4"]
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elapsed = time.time() - start_time

        print(f"Benchmark completato in {elapsed:.2f} secondi!")

        print("Invio risultato al server...")
        requests.post(f"{SERVER_URL}/benchmark_result", json={"client_id": CLIENT_ID, "benchmark_time": elapsed})

        if os.path.exists("local_benchmark.mp4"): os.remove("local_benchmark.mp4")
        if os.path.exists("local_benchmark_out.mp4"): os.remove("local_benchmark_out.mp4")
        
        print("--- BENCHMARK CONCLUSO ---\n")
    except Exception as e:
        print(f"Errore critico durante il benchmark: {e}")
        sys.exit(1)

    # 3. Ciclo infinito di elaborazione pezzi
    print("In attesa di video da elaborare. Premi Ctrl+C per fermare il client in modo sicuro.\n")
    
    # Invece di "while True", usiamo la variabile globale!
    while keep_running:
        try:
            # Chiede un pezzo al server (Timeout 5 secondi per non bloccarsi)
            res = requests.get(f"{SERVER_URL}/get_chunk", params={"client_id": CLIENT_ID}, timeout=5)
            
            if res.status_code == 404:
                # Se il server è in pausa o non ha pezzi, fa un respiro breve e riprova
                # Questo for permette di interrompere l'attesa se si preme Ctrl+C
                for _ in range(5):
                    if not keep_running: break
                    time.sleep(1)
                continue
            
            res.raise_for_status()
            chunk_id = res.headers.get("X-Chunk-Id")
            
            if not chunk_id:
                time.sleep(5)
                continue

            input_chunk = f"chunk_in_{chunk_id}.mp4"
            output_chunk = f"chunk_out_{chunk_id}.mp4"

            # Scarica il pezzo da elaborare
            with open(input_chunk, "wb") as f:
                for chunk in res.iter_content(chunk_size=8192):
                    f.write(chunk)

            print(f"[{time.strftime('%H:%M:%S')}] Ricevuto pezzo ID {chunk_id}. Avvio elaborazione...")
            
            # Elaborazione FFmpeg
            args = ["ffmpeg", "-y", "-i", input_chunk] + server_config["RESIZE_ARGS"] + [output_chunk]
            subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if not os.path.exists(output_chunk):
                print(f"[{time.strftime('%H:%M:%S')}] Errore di conversione. Riproverò al prossimo ciclo.")
                time.sleep(5)
                continue

            print(f"[{time.strftime('%H:%M:%S')}] Pezzo ID {chunk_id} convertito. Upload al server in corso...")
            
            # Invio file al server
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

            # Pulizia file locali
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
                
    # Se il ciclo "while" termina (perché keep_running è diventato False), il programma arriva qui
    print("\n👋 Client terminato in modo sicuro. Nessun lavoro è andato perso! A presto.")
