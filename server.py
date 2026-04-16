import os
import json
import time
import asyncio
import subprocess
import uvicorn
import hashlib 
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import List
import shutil
import database

import datetime

def log_to_file(message):
    """Scrive un messaggio nei log aggiungendo il timestamp in automatico."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file_path = config.get("LOG_FILE", "server_log.txt")
    with open(log_file_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

def hash_password(password: str) -> str:
    """Restituisce l'hash SHA-256 della stringa fornita."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "PORTA_HTTP": 50123,
    "SPLIT_SECONDS": 30,
    "NOME_FILE_BENCHMARK": "benchmark.mp4",
    "RESIZE_ARGS": ["-vf", "scale=-1:720", "-c:v", "libx264", "-c:a", "copy"],
    "LOG_FILE": "server_log.txt",
    "SCAN_DIR": ".",
    "DB_PATH": "resizer.db",
    "INPUT_EXT": ".mp4",
    "OUTPUT_EXT": ".mp4",
    "DASHBOARD_PASSWORD": hash_password("admin") 
}

if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(DEFAULT_CONFIG, f, indent=4)

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

aggiorna_config = False
if "DASHBOARD_PASSWORD" not in config:
    config["DASHBOARD_PASSWORD"] = hash_password("admin")
    aggiorna_config = True
elif len(config["DASHBOARD_PASSWORD"]) != 64:
    messaggio = "⚠️ Rilevata password in chiaro nel config.json. Conversione in Hash in corso..."
    print(messaggio)
    log_to_file(messaggio)
    config["DASHBOARD_PASSWORD"] = hash_password(config["DASHBOARD_PASSWORD"])
    aggiorna_config = True

if "DB_PATH" not in config:
    config["DB_PATH"] = "resizer.db"
    aggiorna_config = True

if "INPUT_EXT" not in config:
    config["INPUT_EXT"] = ".mp4"
    config["OUTPUT_EXT"] = ".mp4"
    aggiorna_config = True

if aggiorna_config:
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

database.set_db_path(config["DB_PATH"])

is_paused = False

def is_authenticated(request: Request):
    """Verifica se il cookie di sessione coincide con la password hashata."""
    token = request.cookies.get("auth_token")
    return token == config.get("DASHBOARD_PASSWORD")

def verify_auth(request: Request):
    """Solleva un'eccezione HTTP se l'utente tenta di accedere a una risorsa senza login."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Non autorizzato. Effettua il login.")

def generate_benchmark():
    """Genera il file video standard per testare le prestazioni dei client."""
    file_path = config["NOME_FILE_BENCHMARK"]
    split_seconds = config.get("SPLIT_SECONDS", 30)
    
    if not os.path.exists(file_path):
        messaggio = f"Generazione file benchmark in corso: {file_path} (Durata: {split_seconds} secondi)"
        print(messaggio)
        log_to_file(messaggio)
        
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={split_seconds}:size=1920x1080:rate=30",
            "-c:v", "libx264", file_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def scan_and_process():
    """Task infinito: gestisce in background scansione, elaborazione e unione file."""
    while True:
        try:
            deleted_clients = database.cleanup_inactive_clients(time.time())
            if deleted_clients > 0:
                log_to_file(f"Pulizia automatica: {deleted_clients} client inattivi rimossi (Timeout).")
                print(f"Pulizia automatica: {deleted_clients} client inattivi rimossi.")

            if is_paused:
                await asyncio.sleep(5)
                continue

            current_scan_dir = config.get("SCAN_DIR", ".")
            in_ext = config.get("INPUT_EXT", ".mp4").lower()
            out_ext = config.get("OUTPUT_EXT", ".mp4").lower()
            
            if os.path.isdir(current_scan_dir):
                all_videos = database.get_all_videos()
                for video in all_videos:
                    filepath = video['filename']
                    video_id = video['id']
                    
                    if not os.path.exists(filepath):
                        messaggio_rimosso = f"⚠️ File scomparso rilevato: {filepath}. Pulizia in corso..."
                        print(messaggio_rimosso)
                        log_to_file(messaggio_rimosso)
                        
                        chunks = database.get_chunks_by_video(video_id)
                        for chunk in chunks:
                            if os.path.exists(chunk['chunk_filename']):
                                os.remove(chunk['chunk_filename'])
                                
                        list_file_path = os.path.join(current_scan_dir, "tmp", f"list_{video_id}.txt")
                        if os.path.exists(list_file_path):
                            os.remove(list_file_path)
                            
                        database.delete_video(video_id)
                        
                for f in os.listdir(current_scan_dir):
                    if f.lower().endswith(in_ext) and not f.startswith("chunk_") and f != config.get("NOME_FILE_BENCHMARK", "benchmark.mp4"):
                        full_path = os.path.join(current_scan_dir, f)
                        database.insert_video(full_path)
            
            video = database.get_video_by_status("da_splittare")
            if video:
                video_id = video["id"]
                filepath = video["filename"]
                tmp_dir = os.path.join(current_scan_dir, "tmp")
                os.makedirs(tmp_dir, exist_ok=True)
                
                chunk_pattern = os.path.join(tmp_dir, f"{video_id}_%04d{in_ext}")
                
                messaggio_split = f"Avvio split del video: {filepath}..."
                print(messaggio_split)
                log_to_file(messaggio_split)
                database.update_video_status(video_id, "in_elaborazione")
                
                split_sec = config.get("SPLIT_SECONDS", 30)
                
                subprocess.run([
                    "ffmpeg", "-y", "-i", filepath, "-c", "copy", "-map", "0",
                    "-segment_time", str(split_sec), "-f", "segment",
                    "-reset_timestamps", "1", chunk_pattern
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                for chunk_file in sorted(os.listdir(tmp_dir)):
                    if chunk_file.startswith(f"{video_id}_") and chunk_file.endswith(in_ext):
                        chunk_full_path = os.path.join(tmp_dir, chunk_file)
                        database.insert_chunk(video_id, chunk_full_path)
                
                messaggio_div = f"Video {filepath} diviso con successo."
                print(messaggio_div)
                log_to_file(messaggio_div)

            videos_elaborazione = database.get_videos_by_status("in_elaborazione")
            for video_elaborazione in videos_elaborazione:
                video_id = video_elaborazione["id"]
                
                if database.are_all_chunks_completed(video_id):
                    filepath = video_elaborazione["filename"]
                    
                    messaggio_merge = f"Unione (Merge) in corso per il video: {filepath}..."
                    print(messaggio_merge)
                    log_to_file(messaggio_merge)
                    
                    chunks = database.get_chunks_by_video(video_id)
                    list_file_path = os.path.join(current_scan_dir, "tmp", f"list_{video_id}.txt")
                    
                    with open(list_file_path, "w") as lf:
                        for chunk in chunks:
                            safe_path = os.path.abspath(chunk['chunk_filename']).replace('\\', '/')
                            lf.write(f"file '{safe_path}'\n")
                    
                    output_file = filepath + ".merged" + out_ext
                    
                    subprocess.run([
                        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file_path,
                        "-c", "copy", output_file
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    
                    if os.path.exists(output_file):
                        final_size = os.path.getsize(output_file)
                        database.update_video_final_size(video_id, final_size)
                        
                        base_name = os.path.splitext(filepath)[0]
                        final_filepath = base_name + out_ext
                        
                        if filepath != final_filepath and os.path.exists(filepath):
                            os.remove(filepath)
                            
                        os.replace(output_file, final_filepath)
                        database.update_video_filename(video_id, final_filepath)
                    
                    for chunk in chunks:
                        if os.path.exists(chunk['chunk_filename']):
                            os.remove(chunk['chunk_filename'])
                    if os.path.exists(list_file_path):
                        os.remove(list_file_path)
                        
                    database.update_video_status(video_id, "completato")
                    messaggio_fine = f"Video {filepath} unito e completato con successo."
                    print(messaggio_fine)
                    log_to_file(messaggio_fine)
                    
        except Exception as e:
            err = f"Errore nel task in background: {e}"
            print(err)
            log_to_file(err)
            
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestisce il ciclo di vita all'avvio e allo spegnimento del server."""
    print("\n--- INIZIALIZZAZIONE AMBIENTE ---")
    database.clean_db()
    database.init_db()               
    
    current_scan_dir = config.get("SCAN_DIR", ".")
    tmp_dir = os.path.join(current_scan_dir, "tmp")
    if os.path.exists(tmp_dir):
        try:
            shutil.rmtree(tmp_dir)
            print("Cartella dei file temporanei (tmp/) pulita con successo.")
        except Exception as e:
            print(f"Impossibile pulire la cartella temporanea: {e}")
            
    print("---------------------------------\n")

    generate_benchmark()             
    task = asyncio.create_task(scan_and_process()) 
    yield
    task.cancel()                    

app = FastAPI(lifespan=lifespan)

class BenchmarkResult(BaseModel):
    client_id: str
    benchmark_time: float

class ConfigUpdate(BaseModel):
    SPLIT_SECONDS: int
    NOME_FILE_BENCHMARK: str
    LOG_FILE: str
    RESIZE_ARGS: List[str]
    SCAN_DIR: str 
    DB_PATH: str 
    INPUT_EXT: str
    OUTPUT_EXT: str
    DASHBOARD_PASSWORD: str = "" 

class PauseState(BaseModel):
    paused: bool

class PriorityUpdate(BaseModel):
    video_id: int
    priorita: int


@app.get("/login", response_class=HTMLResponse)
def get_login_page(request: Request, error: str = ""):
    """Mostra la pagina per l'inserimento della password di sicurezza."""
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
        
    error_html = f"<p style='color: #ff5252; font-weight: bold;'>{error}</p>" if error else ""
    html_content = f"""
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <title>Login - Video Resizer</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1e1e2f; color: #fff; margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; }}
            .login-box {{ background: #2a2a40; padding: 40px; border-radius: 12px; box-shadow: 0 8px 20px rgba(0,0,0,0.5); width: 100%; max-width: 350px; text-align: center; border: 1px solid #3d3d5c; }}
            h2 {{ color: #4caf50; margin-top: 0; }}
            input[type="password"] {{ width: 100%; padding: 12px; margin: 20px 0; border: 1px solid #3d3d5c; border-radius: 4px; background: #0c0c12; color: #fff; box-sizing: border-box; outline: none; font-size: 1.1em; text-align: center; }}
            input[type="password"]:focus {{ border-color: #4caf50; }}
            button {{ width: 100%; padding: 12px; background: #4caf50; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 1.1em; transition: background 0.3s; }}
            button:hover {{ background: #388e3c; }}
        </style>
    </head>
    <body>
        <div class="login-box">
            <h2>🔒 Accesso Protetto</h2>
            <p style="color: #bbb; font-size: 0.9em;">Inserisci la password per gestire il server.</p>
            {error_html}
            <form action="/login" method="post">
                <input type="password" name="password" placeholder="Password..." required autofocus>
                <button type="submit">Entra nella Dashboard</button>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/login")
def do_login(password: str = Form(...)):
    """Verifica le credenziali ed emette il cookie di autorizzazione."""
    hashed_input = hash_password(password)
    if hashed_input == config.get("DASHBOARD_PASSWORD"):
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="auth_token", value=hashed_input, httponly=True, max_age=2592000)
        return response
    else:
        return RedirectResponse(url="/login?error=Password errata!", status_code=303)

@app.get("/logout")
def do_logout():
    """Rimuove il cookie di sessione portando l'utente al logout."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("auth_token")
    return response

@app.get("/download_client")
def download_client():
    """Fornisce il download diretto dello script Python del client."""
    client_file = "client.py"
    if os.path.exists(client_file):
        return FileResponse(path=client_file, filename="client.py", media_type="text/x-python")
    raise HTTPException(status_code=404, detail="File client.py non trovato sul server.")

@app.post("/set_priority", dependencies=[Depends(verify_auth)])
def set_priority(data: PriorityUpdate):
    """Scambia la priorità tra normale ed elevata per un dato video."""
    database.set_video_priority(data.video_id, data.priorita)
    stato = "ALTA" if data.priorita > 0 else "NORMALE"
    msg = f"Priorità modificata in {stato} per il video ID {data.video_id}."
    print(msg)
    log_to_file(msg)
    return {"status": "ok"}

@app.post("/set_pause", dependencies=[Depends(verify_auth)])
def set_pause(state: PauseState):
    """Sospende momentaneamente l'erogazione dei chunk e lo split ai client."""
    global is_paused
    is_paused = state.paused
    stato_str = "PAUSA (Niente più scansioni o invio file)" if is_paused else "ATTIVO"
    messaggio = f"Stato del server modificato: {stato_str}"
    print(messaggio)
    log_to_file(messaggio)
    return {"status": "ok", "is_paused": is_paused}

@app.get("/config")
def get_config():
    """Fornisce ai client e alla dashboard le variabili di configurazione escludendo le password."""
    safe_config = config.copy()
    if "DASHBOARD_PASSWORD" in safe_config:
        del safe_config["DASHBOARD_PASSWORD"]
    return safe_config

@app.post("/update_config", dependencies=[Depends(verify_auth)])
def update_config(new_config: ConfigUpdate):
    """Riceve un json di configurazione aggiornato e lo applica in tempo reale."""
    global config
    config["SPLIT_SECONDS"] = new_config.SPLIT_SECONDS
    config["NOME_FILE_BENCHMARK"] = new_config.NOME_FILE_BENCHMARK
    config["LOG_FILE"] = new_config.LOG_FILE
    config["RESIZE_ARGS"] = new_config.RESIZE_ARGS
    config["SCAN_DIR"] = new_config.SCAN_DIR
    config["INPUT_EXT"] = new_config.INPUT_EXT
    config["OUTPUT_EXT"] = new_config.OUTPUT_EXT
    
    config["DB_PATH"] = new_config.DB_PATH.strip() or "resizer.db"
    database.set_db_path(config["DB_PATH"])
    database.init_db() 
    
    if new_config.DASHBOARD_PASSWORD.strip() != "":
        config["DASHBOARD_PASSWORD"] = hash_password(new_config.DASHBOARD_PASSWORD.strip())
    
    if not os.path.exists(config["SCAN_DIR"]):
        os.makedirs(config["SCAN_DIR"], exist_ok=True)
    
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)
        
    messaggio = "Configurazioni di sistema aggiornate tramite Dashboard."
    print(messaggio)
    log_to_file(messaggio)
    return {"status": "ok"}

@app.get("/benchmark")
def get_benchmark():
    """Eroga il file del benchmark iniziale ai client."""
    file_path = config["NOME_FILE_BENCHMARK"]
    if os.path.exists(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Benchmark not found")

@app.post("/benchmark_result")
def post_benchmark_result(data: BenchmarkResult, request: Request):
    """Registra nel DB il tempo impiegato da un client ed estrae il suo IP."""
    client_ip = request.client.host
    database.save_client_benchmark(data.client_id, data.benchmark_time, client_ip)
    return {"status": "ok"}

@app.get("/get_chunk")
def get_chunk(client_id: str, request: Request):
    """Restituisce un chunk fisico da convertire al client autorizzato."""
    if is_paused:
        raise HTTPException(status_code=404, detail="Server in pausa. Nessun chunk erogato.")

    client = database.get_client(client_id)
    if not client:
        raise HTTPException(status_code=400, detail="Client non registrato. Fai prima il benchmark.")
        
    current_time = time.time()
    client_ip = request.client.host
    database.update_client_last_seen(client_id, current_time, client_ip)
    
    stale_chunks = database.get_stale_chunks(current_time)
    for stale in stale_chunks:
        msg_reap = f"Recupero chunk bloccato/scaduto con ID {stale['id']}"
        print(msg_reap)
        log_to_file(msg_reap)
        database.reset_chunk(stale['id'])
        
    chunk = database.assign_pending_chunk(client_id, current_time)
    if not chunk:
        raise HTTPException(status_code=404, detail="No chunks available")
        
    msg_assign = f"Assegnato chunk ID {chunk['id']} (file: {chunk['chunk_filename']}) al client {client_id} (IP: {client_ip})"
    print(msg_assign)
    log_to_file(msg_assign)
        
    return FileResponse(
        path=chunk['chunk_filename'],
        headers={"X-Chunk-Id": str(chunk['id'])}
    )

@app.post("/upload_chunk")
async def upload_chunk(request: Request, client_id: str = Form(...), chunk_id: int = Form(...), file: UploadFile = File(...)):
    """Riceve l'upload di un pezzo convertito e lo marca completato."""
    client_ip = request.client.host
    database.update_client_last_seen(client_id, time.time(), client_ip)
    
    chunk = database.get_chunk_by_id(chunk_id)
    if not chunk:
        raise HTTPException(status_code=400, detail="Chunk not found")
        
    if chunk['status'] == 'completato' : # or chunk['client_id'] != client_id
        raise HTTPException(status_code=400, detail="Chunk assegnato a un altro client o già completato. File in ritardo scartato.")
        
    chunk_path = chunk['chunk_filename']
    with open(chunk_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    database.update_chunk_status(chunk_id, "completato")
    
    video_id = chunk['video_id']
    mancanti = database.get_remaining_chunks_count(video_id)
    
    if mancanti > 0:
        messaggio = f"Ricevuto chunk ID {chunk_id} dal client {client_ip}. Mancano {mancanti} pezzi al termine."
    else:
        messaggio = f"Ricevuto chunk ID {chunk_id} dal client {client_ip}. TUTTI I PEZZI RICEVUTI!"
        
    print(messaggio)
    log_to_file(messaggio)
    
    return {"status": "ok"}

@app.get("/status_data", dependencies=[Depends(verify_auth)])
def get_status_data():
    """Fornisce tutti i JSON statistici necessari all'aggiornamento della pagina."""
    stats = database.get_dashboard_stats()
    stats["cartella_scansione"] = config.get("SCAN_DIR", ".")
    stats["is_paused"] = is_paused 
    return stats

@app.get("/logs_data", dependencies=[Depends(verify_auth)])
def get_logs_data():
    """Legge e formatta il file di testo contenente i log del server."""
    log_file_path = config.get("LOG_FILE", "server_log.txt")
    if not os.path.exists(log_file_path):
        return {"logs": "In attesa dei primi log...\n"}
    
    try:
        with open(log_file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            last_lines = lines[-200:]
            return {"logs": "".join(last_lines)}
    except Exception as e:
        return {"logs": f"Errore nella lettura dei log: {e}"}


@app.get("/", response_class=HTMLResponse)
def get_dashboard(request: Request):
    """Eroga l'interfaccia HTML completa del server Web."""
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
        
    html_content = """
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <title>Dashboard Video Resizer</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1e1e2f; color: #fff; margin: 0; padding: 40px; display: flex; justify-content: center; }
            .dashboard { background: #2a2a40; padding: 30px; border-radius: 12px; box-shadow: 0 8px 20px rgba(0,0,0,0.5); width: 100%; max-width: 800px; }
            h1 { margin-top: 0; color: #4caf50; padding-bottom: 10px;}
            .metric { display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #3d3d5c; font-size: 1.2em; }
            
            .config-panel { background: #151521; padding: 20px; border-radius: 8px; border: 1px solid #ff9800; margin-bottom: 30px; }
            .config-panel h3 { color: #ff9800; margin-top: 0; margin-bottom: 15px; }
            .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 15px; }
            .form-group { display: flex; flex-direction: column; }
            .form-group label { font-size: 0.9em; color: #bbb; margin-bottom: 5px; }
            .form-group input { background: #0c0c12; color: #fff; border: 1px solid #3d3d5c; padding: 8px; border-radius: 4px; outline: none; }
            .form-group input:focus { border-color: #4caf50; }
            
            .btn { color: #fff; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: bold; transition: background 0.3s;}
            .btn-save { background: #ff9800; }
            .btn-save:hover { background: #e68a00; }
            .btn-toggle { background: #3d3d5c; font-size: 0.9em;}
            .btn-toggle:hover { background: #5c5c8a; }

            .video-card { background: #151521; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #3d3d5c; transition: all 0.3s; }
            .video-title { margin-top: 0; margin-bottom: 15px; color: #fff; font-size: 1.1em; display: flex; align-items: center; }
            .video-stats { display: flex; justify-content: space-between; font-size: 0.9em; margin-bottom: 10px; }
            .video-sizes { display: flex; justify-content: space-between; font-size: 0.9em; padding-top: 10px; margin-top: 10px; border-top: 1px dashed #3d3d5c;}
            
            .bar-bg { background: #1e1e2f; border-radius: 8px; height: 25px; width: 100%; overflow: hidden; position: relative;}
            .bar-fill { background: linear-gradient(90deg, #4caf50, #81c784); height: 100%; width: 0%; transition: width 0.5s ease; display: flex; align-items: center; justify-content: center; color: #1e1e2f; font-weight: bold; font-size: 0.9em;}
            
            .logs-container { background: #151521; padding: 15px; border-radius: 8px; border: 1px solid #3d3d5c; margin-top: 30px;}
            h3 { margin: 0 0 10px 0; color: #bbb; font-size: 1.1em; text-transform: uppercase; letter-spacing: 1px;}
            textarea { width: 100%; height: 300px; background: #0c0c12; color: #00ff00; font-family: 'Courier New', Courier, monospace; font-size: 0.9em; border: none; padding: 10px; border-radius: 4px; resize: vertical; box-sizing: border-box; outline: none; }
        </style>
    </head>
    <body>
        <div class="dashboard">
            
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px; border-bottom: 2px solid #3d3d5c; padding-bottom: 15px;">
                <h1 style="margin: 0; color: #4caf50;">🚀 Dashboard Video Resizer</h1>
                <div>
                    <button id="btn-pause" class="btn" style="background: #f44336; font-size: 1.1em;" onclick="togglePause()">⏸️ Metti in Pausa</button>
                    <a href="/download_client" class="btn" style="background: #2196f3; text-decoration: none; display: inline-block; font-size: 1.1em; margin-left: 10px;" download="client.py">🐍 Scarica Client</a>
                    <a href="/logout" class="btn" style="background: #3d3d5c; text-decoration: none; display: inline-block; font-size: 1.1em; margin-left: 10px;">🚪 Esci</a>
                </div>
            </div>
            
            <div id="pause-banner" style="display: none; background: #ff9800; color: #1e1e2f; text-align: center; font-weight: bold; padding: 10px; border-radius: 8px; margin-bottom: 20px; font-size: 1.1em; box-shadow: 0 0 10px #ff9800;">
                ⚠️ IL SERVER È IN PAUSA. Scansione e invio file sospesi. ⚠️
            </div>

            <div class="config-panel">
                <h3>⚙️ Configurazioni Server (Live)</h3>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Split Seconds (Durata Pezzi):</label>
                        <input type="number" id="cfg-split">
                    </div>
                    <div class="form-group">
                        <label>Cartella in Scansione (SCAN_DIR):</label>
                        <input type="text" id="cfg-scan" placeholder=". (Cartella corrente)">
                    </div>
                    <div class="form-group">
                        <label>Estensione Input (es. .mkv):</label>
                        <input type="text" id="cfg-in-ext" placeholder=".mp4">
                    </div>
                    <div class="form-group">
                        <label>Estensione Output (es. .mp4):</label>
                        <input type="text" id="cfg-out-ext" placeholder=".mp4">
                    </div>
                    <div class="form-group">
                        <label>Percorso Database (DB_PATH):</label>
                        <input type="text" id="cfg-db" placeholder="resizer.db">
                    </div>
                    <div class="form-group">
                        <label>Nome File Log:</label>
                        <input type="text" id="cfg-log">
                    </div>
                    <div class="form-group">
                        <label>Password Dashboard:</label>
                        <input type="password" id="cfg-pass" placeholder="Lascia vuoto per non cambiare">
                    </div>
                    <div class="form-group" style="grid-column: 1 / -1;">
                        <label>Argomenti FFmpeg (Array in JSON):</label>
                        <input type="text" id="cfg-args">
                    </div>
                </div>
                <button class="btn btn-save" onclick="saveConfig()">Salva Configurazioni</button>
                <span id="cfg-status" style="margin-left: 15px; font-weight: bold;"></span>
            </div>

            <div class="metric" style="font-size: 1em; background: #151521; padding: 15px; border-radius: 8px; border: 1px solid #3d3d5c;">
                <span>📂 Video in Coda: <strong id="vid-pending" style="color: #ff9800;">0</strong></span>
                <span>✅ Video Completati: <strong id="vid-done" style="color: #4caf50;">0</strong></span>
                <span>📦 Video Totali: <strong id="vid-total">0</strong></span>
            </div>

            <div class="metric" style="margin-bottom: 30px; border-bottom: none; display: flex; flex-direction: column; gap: 10px;">
                <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px dashed #3d3d5c; padding-top: 10px;">
                    <span>📁 Cartella in Scansione:</span>
                    <strong id="scan-dir" style="color: #ffeb3b; font-size: 0.85em; word-break: break-all; text-align: right; max-width: 60%;">-</strong>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px dashed #3d3d5c; padding-top: 10px;">
                    <span>⏱️ Tempo stimato di completamento totale:</span>
                    <strong id="global-eta" style="color: #00e676; font-size: 1.1em;">Calcolo in corso...</strong>
                </div>
                
                <div style="margin-top: 10px;">
                    <div style="display: flex; justify-content: space-between; font-size: 0.9em; margin-bottom: 5px; color: #bbb;">
                        <span>Progresso Complessivo:</span>
                        <strong id="global-perc-text">0%</strong>
                    </div>
                    <div class="bar-bg" style="height: 20px;">
                        <div id="global-bar-fill" class="bar-fill" style="width: 0%; background: linear-gradient(90deg, #2196f3, #64b5f6);">0%</div>
                    </div>
                </div>
            </div>

            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 2px solid #3d3d5c; padding-bottom: 10px;">
                <h3 style="margin: 0; color: #bbb;">💻 Client Operativi (<span id="clients-count">0</span>)</h3>
            </div>
            
            <div id="clients-container" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 15px; margin-bottom: 35px;">
                <p style="color: #777;">Caricamento stato client...</p>
            </div>

            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 2px solid #3d3d5c; padding-bottom: 10px;">
                <h3 style="margin: 0; color: #bbb;">Lista Video</h3>
                <button id="toggle-btn" class="btn btn-toggle" onclick="toggleCompleted()">Mostra Tutti i Completati</button>
            </div>
            
            <div id="videos-container">
                <p style="text-align:center; color:#bbb;">Caricamento dati in corso...</p>
            </div>
            
            <div class="logs-container">
                <h3>📜 Server Logs In Diretta</h3>
                <textarea id="server-logs" readonly>Caricamento log...</textarea>
            </div>
        </div>

        <script>
            let showCompleted = false;
            let currentPausedState = false; 

            function checkAuthError(response) {
                if (response.status === 401) {
                    window.location.href = '/login'; 
                    throw new Error("Sessione scaduta");
                }
                return response;
            }

            function formatETA(seconds) {
                if (seconds === undefined || isNaN(seconds)) return "Calcolo in corso...";
                if (seconds < 0) return "In attesa di client...";
                if (seconds === 0) return "Tutto completato!";
                
                let h = Math.floor(seconds / 3600);
                let m = Math.floor((seconds % 3600) / 60);
                let s = Math.floor(seconds % 60);
                
                let res = [];
                if (h > 0) res.push(h + "h");
                if (m > 0) res.push(m + "m");
                res.push(s + "s");
                return res.join(" ");
            }

            async function togglePriority(videoId, newPriority) {
                try {
                    const res = await fetch('/set_priority', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ video_id: videoId, priorita: newPriority })
                    });
                    checkAuthError(res);
                    updateDashboard(); 
                } catch (e) { console.error("Errore priorità", e); }
            }

            async function togglePause() {
                try {
                    const newState = !currentPausedState;
                    const res = await fetch('/set_pause', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ paused: newState })
                    });
                    checkAuthError(res);
                    if (res.ok) { updateDashboard(); }
                } catch (e) { console.error("Errore durante la pausa", e); }
            }

            function toggleCompleted() {
                showCompleted = !showCompleted;
                const btn = document.getElementById('toggle-btn');
                if (showCompleted) {
                    btn.innerText = "Nascondi i Completati";
                    btn.style.background = "#ff9800"; 
                } else {
                    btn.innerText = "Mostra Tutti i Completati";
                    btn.style.background = "#3d3d5c"; 
                }
                updateDashboard(); 
            }

            function formatBytes(bytes) {
                if (!bytes || bytes === 0) return '0.00 MB';
                return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
            }

            async function loadConfig() {
                try {
                    const res = await fetch('/config');
                    const cfg = await res.json();
                    document.getElementById('cfg-split').value = cfg.SPLIT_SECONDS;
                    document.getElementById('cfg-log').value = cfg.LOG_FILE;
                    document.getElementById('cfg-scan').value = cfg.SCAN_DIR || ".";
                    document.getElementById('cfg-db').value = cfg.DB_PATH || "resizer.db";
                    document.getElementById('cfg-in-ext').value = cfg.INPUT_EXT || ".mp4";
                    document.getElementById('cfg-out-ext').value = cfg.OUTPUT_EXT || ".mp4";
                    document.getElementById('cfg-args').value = JSON.stringify(cfg.RESIZE_ARGS);
                } catch (e) { console.error("Errore config:", e); }
            }

            async function saveConfig() {
                const statusLabel = document.getElementById('cfg-status');
                try {
                    const argsValue = document.getElementById('cfg-args').value;
                    const parsedArgs = JSON.parse(argsValue);
                    if (!Array.isArray(parsedArgs)) throw new Error("Deve essere un array.");

                    const newCfg = {
                        SPLIT_SECONDS: parseInt(document.getElementById('cfg-split').value),
                        NOME_FILE_BENCHMARK: "benchmark.mp4", 
                        LOG_FILE: document.getElementById('cfg-log').value,
                        SCAN_DIR: document.getElementById('cfg-scan').value || ".",
                        DB_PATH: document.getElementById('cfg-db').value || "resizer.db",
                        INPUT_EXT: document.getElementById('cfg-in-ext').value || ".mp4",
                        OUTPUT_EXT: document.getElementById('cfg-out-ext').value || ".mp4",
                        RESIZE_ARGS: parsedArgs,
                        DASHBOARD_PASSWORD: document.getElementById('cfg-pass').value 
                    };

                    const response = await fetch('/update_config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(newCfg)
                    });
                    
                    checkAuthError(response);

                    if (response.ok) {
                        statusLabel.style.color = "#4caf50";
                        statusLabel.innerText = "Salvato con successo!";
                        document.getElementById('cfg-pass').value = ""; 
                        
                        if (newCfg.DASHBOARD_PASSWORD !== "") {
                            alert("Password cambiata! Per favore effettua nuovamente il login.");
                            window.location.href = '/logout';
                        }
                        setTimeout(() => statusLabel.innerText = "", 3000);
                    } else { throw new Error("Errore dal server."); }
                } catch(e) {
                    if(e.message !== "Sessione scaduta") {
                        statusLabel.style.color = "#ff5252";
                        statusLabel.innerText = "Errore: " + e.message;
                    }
                }
            }

            async function updateDashboard() {
                try {
                    const statsResponse = await fetch('/status_data');
                    checkAuthError(statsResponse);
                    
                    const stats = await statsResponse.json();
                    
                    currentPausedState = stats.is_paused;
                    const pauseBtn = document.getElementById('btn-pause');
                    const pauseBanner = document.getElementById('pause-banner');
                    
                    if (currentPausedState) {
                        pauseBtn.innerText = "▶️ Riprendi Elaborazione";
                        pauseBtn.style.background = "#4caf50"; 
                        pauseBanner.style.display = "block";   
                    } else {
                        pauseBtn.innerText = "⏸️ Metti in Pausa";
                        pauseBtn.style.background = "#f44336"; 
                        pauseBanner.style.display = "none";    
                    }

                    document.getElementById('scan-dir').innerText = stats.cartella_scansione || ".";
                    
                    if (stats.global_eta_seconds !== undefined) {
                        document.getElementById('global-eta').innerText = formatETA(stats.global_eta_seconds);
                    }
                    
                    let globalPerc = 0;
                    if (stats.global_totali > 0) {
                        globalPerc = Math.round((stats.global_completati / stats.global_totali) * 100);
                    }
                    document.getElementById('global-perc-text').innerText = globalPerc + '%';
                    document.getElementById('global-bar-fill').style.width = globalPerc + '%';
                    document.getElementById('global-bar-fill').innerText = globalPerc + '%';
                    
                    document.getElementById('clients-count').innerText = stats.client_attivi;
                    const clientsContainer = document.getElementById('clients-container');
                    
                    if (stats.client_list && stats.client_list.length > 0) {
                        let clientsHtml = "";
                        stats.client_list.forEach(c => {
                            let idShort = c.client_id.split('-')[0];
                            let bTime = c.benchmark_time.toFixed(1);
                            
                            let limitWarning = 5 * c.benchmark_time;
                            let statusColor = c.seconds_ago > limitWarning ? "#ff9800" : "#4caf50";
                            let icon = c.seconds_ago > limitWarning ? "⏳" : "🟢";
                            
                            let ipInfo = c.ip_address ? c.ip_address : "Sconosciuto";
                            
                            clientsHtml += `
                            <div style="background: #151521; padding: 15px; border-radius: 8px; border: 1px solid #3d3d5c; border-left: 4px solid ${statusColor};">
                                <div style="font-weight: bold; margin-bottom: 5px;">${icon} ID: ${idShort}</div>
                                <div style="font-size: 0.85em; color: #2196f3; margin-bottom: 5px;">🌐 IP: ${ipInfo}</div>
                                <div style="font-size: 0.9em; color: #bbb;">⚡ Vel: ${bTime}s / pezzo</div>
                                <div style="font-size: 0.8em; margin-top: 5px; color: ${statusColor};">⏱️ Ping: ${c.seconds_ago} sec fa</div>
                            </div>
                            `;
                        });
                        clientsContainer.innerHTML = clientsHtml;
                    } else {
                        clientsContainer.innerHTML = "<p style='color: #777; grid-column: 1 / -1;'>Nessun client attualmente connesso.</p>";
                    }
                    
                    const totalVideos = stats.videos.length;
                    const completedVideos = stats.videos.filter(v => v.status === 'completato').length;
                    const pendingVideos = totalVideos - completedVideos;

                    document.getElementById('vid-total').innerText = totalVideos;
                    document.getElementById('vid-done').innerText = completedVideos;
                    document.getElementById('vid-pending').innerText = pendingVideos;

                    const container = document.getElementById('videos-container');
                    let htmlOutput = "";
                    let visibleCount = 0;
                    
                    if (totalVideos === 0) {
                        htmlOutput = "<p style='text-align:center; color:#bbb;'>Nessun video presente nella cartella.</p>";
                    } else {
                        stats.videos.forEach(v => {
                            if (v.status === 'completato' && !showCompleted) return; 
                            
                            visibleCount++;
                            let perc = 0;
                            if (v.totali > 0) { perc = Math.round((v.completati / v.totali) * 100); }
                            
                            let statusIcon = "⏳"; let statusColor = "#2196f3"; 
                            if (v.status === 'in_elaborazione') { statusIcon = "⚙️"; statusColor = "#ff9800"; }
                            if (v.status === 'completato') { statusIcon = "✅"; statusColor = "#4caf50"; } 
                            
                            let priorityBtn = "";
                            if (v.status !== 'completato') {
                                if (v.priorita > 0) {
                                    priorityBtn = `<button onclick="togglePriority(${v.id}, 0)" style="background:none; border:none; cursor:pointer; font-size:1.1em; color:#ffeb3b; outline:none;" title="Rimuovi Priorità">⭐</button>`;
                                } else {
                                    priorityBtn = `<button onclick="togglePriority(${v.id}, 1)" style="background:none; border:none; cursor:pointer; font-size:1.1em; color:#5c5c8a; outline:none;" title="Imposta Priorità Alta">☆</button>`;
                                }
                            }
                            
                            let cardStyle = v.priorita > 0 ? "border-color: #ffeb3b; box-shadow: 0 0 10px rgba(255, 235, 59, 0.1);" : "";
                            
                            let sizeInfoHtml = `<span>💾 Orig: <strong style="color:#bbb;">${formatBytes(v.original_size)}</strong></span>`;
                            
                            if (v.status === 'completato' && v.original_size > 0) {
                                let diff = v.original_size - v.final_size;
                                let percChange = (Math.abs(diff) / v.original_size) * 100;
                                
                                let color = "#bbb"; let icon = "⚖️ "; let sign = "";
                                if (diff > 0) { color = "#4caf50"; icon = "📉"; sign = "-"; }      
                                else if (diff < 0) { color = "#ff5252"; icon = "📈"; sign = "+"; } 
                                
                                sizeInfoHtml += `
                                <span style="color:${color};">
                                    🏁 Finale: <strong>${formatBytes(v.final_size)}</strong> 
                                    (${icon} ${sign}${percChange.toFixed(1)}%)
                                </span>`;
                            } else {
                                sizeInfoHtml += `<span style="color:#777;">🏁 Finale: In attesa...</span>`;
                            }
                            
                            htmlOutput += `
                            <div class="video-card" style="${cardStyle}">
                                <h3 class="video-title">
                                    ${priorityBtn} ${statusIcon} ${v.nome} 
                                    <span style="font-size: 0.8em; color: ${statusColor}; float: right;">${v.status.toUpperCase()}</span>
                                </h3>
                                <div class="video-stats">
                                    <span>📦 Pezzi Tot: <strong>${v.totali}</strong></span>
                                    <span style="color:#4caf50;">✅ Fatti: <strong>${v.completati}</strong></span>
                                    <span style="color:#ff9800;">⚙️ In esec: <strong>${v.in_esecuzione}</strong></span>
                                    <span style="color:#2196f3;">⏳ Attesa: <strong>${v.in_attesa}</strong></span>
                                </div>
                                <div class="video-sizes">
                                    ${sizeInfoHtml}
                                </div>
                                <div class="bar-bg" style="margin-top: 15px;">
                                    <div class="bar-fill" style="width: ${perc}%;">${perc}%</div>
                                </div>
                            </div>
                            `;
                        });

                        if (visibleCount === 0 && totalVideos > 0) {
                            htmlOutput = "<p style='text-align:center; color:#bbb;'>Tutti i video sono completati. Premi 'Mostra Tutti' per vederli.</p>";
                        }
                    }
                    container.innerHTML = htmlOutput;
                    
                    const logsResponse = await fetch('/logs_data');
                    checkAuthError(logsResponse);
                    
                    const logsData = await logsResponse.json();
                    const logArea = document.getElementById('server-logs');
                    
                    const isScrolledToBottom = logArea.scrollHeight - logArea.clientHeight <= logArea.scrollTop + 5;
                    
                    if(logArea.value !== logsData.logs) {
                        logArea.value = logsData.logs;
                        if (isScrolledToBottom) { logArea.scrollTop = logArea.scrollHeight; }
                    }
                } catch (e) {
                }
            }
            
            loadConfig(); 
            updateDashboard();
            setInterval(updateDashboard, 2000); 
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    current_scan_dir = config.get("SCAN_DIR", ".")
    if not os.path.exists(current_scan_dir):
        print(f"La directory {current_scan_dir} non esiste. Verrà creata ora.")
        log_to_file(f"La directory {current_scan_dir} non esiste. Verrà creata ora.")
        os.makedirs(current_scan_dir, exist_ok=True)
        
    uvicorn.run(app, host="0.0.0.0", port=config["PORTA_HTTP"])
