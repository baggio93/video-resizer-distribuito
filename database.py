import sqlite3
import os
import time

# Percorso predefinito (verrà sovrascritto dal server all'avvio)
DB_PATH = 'resizer.db' 

def set_db_path(path):
    """Imposta il percorso del database letto dal file di configurazione."""
    global DB_PATH
    DB_PATH = path

def clean_db():
    """Elimina il file del database se esiste, utile per reset puliti."""
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print(f"Database precedente ({DB_PATH}) rimosso con successo.")
        except Exception as e:
            print(f"Impossibile rimuovere il database: {e}")

def get_connection():
    """Crea e restituisce una connessione al database SQLite."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Inizializza le tabelle del database se non sono già presenti."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Clients (
            client_id TEXT PRIMARY KEY,
            benchmark_time REAL,
            last_seen REAL,
            ip_address TEXT
        )
    ''')
    
    try:
        cursor.execute('ALTER TABLE Clients ADD COLUMN ip_address TEXT')
    except sqlite3.OperationalError:
        pass 
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE,
            status TEXT,
            original_size REAL,
            final_size REAL,
            priorita INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER,
            chunk_filename TEXT,
            status TEXT,
            client_id TEXT,
            start_time REAL,
            FOREIGN KEY(video_id) REFERENCES Videos(id),
            FOREIGN KEY(client_id) REFERENCES Clients(client_id)
        )
    ''')
    
    conn.commit()
    conn.close()

def save_client_benchmark(client_id, benchmark_time, ip_address):
    """Salva o aggiorna i dati di benchmark e l'IP di un client."""
    conn = get_connection()
    cursor = conn.cursor()
    current_time = time.time()
    cursor.execute('''
        INSERT INTO Clients (client_id, benchmark_time, last_seen, ip_address)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(client_id) DO UPDATE SET benchmark_time=excluded.benchmark_time, last_seen=excluded.last_seen, ip_address=excluded.ip_address
    ''', (client_id, benchmark_time, current_time, ip_address))
    conn.commit()
    conn.close()

def get_client(client_id):
    """Recupera le informazioni di un singolo client tramite il suo ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Clients WHERE client_id = ?', (client_id,))
    client = cursor.fetchone()
    conn.close()
    return client

def update_client_last_seen(client_id, current_time, ip_address):
    """Aggiorna il timestamp dell'ultimo segnale di vita (heartbeat) del client."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Clients SET last_seen = ?, ip_address = ? WHERE client_id = ?', (current_time, ip_address, client_id))
    conn.commit()
    conn.close()

def cleanup_inactive_clients(current_time):
    """Rimuove dal database i client inattivi svincolando i loro chunk."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT client_id FROM Clients 
        WHERE (? - last_seen) > (10 * benchmark_time)
    ''', (current_time,))
    
    da_eliminare = [row['client_id'] for row in cursor.fetchall()]
    
    if da_eliminare:
        placeholders = ','.join(['?'] * len(da_eliminare))
        cursor.execute(f'''
            UPDATE Chunks 
            SET status = 'in_attesa', client_id = NULL, start_time = NULL 
            WHERE status = 'in_esecuzione' AND client_id IN ({placeholders})
        ''', da_eliminare)
        
        cursor.execute(f'''
            DELETE FROM Clients 
            WHERE client_id IN ({placeholders})
        ''', da_eliminare)
        
        conn.commit()
        
    conn.close()
    return len(da_eliminare)

def insert_video(filename):
    """Inserisce un nuovo video nella coda di elaborazione."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        size = os.path.getsize(filename) if os.path.exists(filename) else 0
        
        cursor.execute('''
            INSERT INTO Videos (filename, status, original_size, final_size, priorita) 
            VALUES (?, 'da_splittare', ?, 0, 0)
        ''', (filename, size))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()
    return None

def update_video_final_size(video_id, final_size):
    """Aggiorna la dimensione finale del video completato."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Videos SET final_size = ? WHERE id = ?', (final_size, video_id))
    conn.commit()
    conn.close()

def update_video_filename(video_id, new_filename):
    """Aggiorna il nome e l'estensione del video nel database a conversione finita."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Videos SET filename = ? WHERE id = ?', (new_filename, video_id))
    conn.commit()
    conn.close()

def set_video_priority(video_id, priorita):
    """Modifica il livello di priorità di un video specifico."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Videos SET priorita = ? WHERE id = ?', (priorita, video_id))
    conn.commit()
    conn.close()

def get_video_by_status(status):
    """Recupera il primo video disponibile con lo stato richiesto."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Videos WHERE status = ? LIMIT 1', (status,))
    video = cursor.fetchone()
    conn.close()
    return video

def get_videos_by_status(status):
    """Recupera tutti i video che corrispondono a un determinato stato."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Videos WHERE status = ?', (status,))
    videos = cursor.fetchall()
    conn.close()
    return videos

def get_video_by_id(video_id):
    """Cerca e restituisce un video tramite il suo ID primario."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Videos WHERE id = ?', (video_id,))
    video = cursor.fetchone()
    conn.close()
    return video

def update_video_status(video_id, status):
    """Aggiorna lo stato di avanzamento di un video."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Videos SET status = ? WHERE id = ?', (status, video_id))
    conn.commit()
    conn.close()

def insert_chunk(video_id, chunk_filename):
    """Inserisce un singolo pezzo (chunk) associato al suo video originale."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO Chunks (video_id, chunk_filename, status)
        VALUES (?, ?, 'in_attesa')
    ''', (video_id, chunk_filename))
    conn.commit()
    conn.close()

def get_chunks_by_video(video_id):
    """Restituisce tutti i chunk appartenenti a un dato video in ordine sequenziale."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Chunks WHERE video_id = ? ORDER BY id ASC', (video_id,))
    chunks = cursor.fetchall()
    conn.close()
    return chunks

def get_chunk_by_id(chunk_id):
    """Recupera un singolo chunk utilizzando il suo ID univoco."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Chunks WHERE id = ?', (chunk_id,))
    chunk = cursor.fetchone()
    conn.close()
    return chunk

def get_stale_chunks(current_time):
    """Individua i chunk bloccati da troppo tempo per essere riassegnati."""
    conn = get_connection()
    cursor = conn.cursor()
    query = '''
        SELECT Chunks.*, Clients.benchmark_time 
        FROM Chunks
        JOIN Clients ON Chunks.client_id = Clients.client_id
        WHERE Chunks.status = 'in_esecuzione'
    '''
    cursor.execute(query)
    all_executing = cursor.fetchall()
    
    stale_chunks = []
    for row in all_executing:
        benchmark = row['benchmark_time']
        start_time = row['start_time']
        if start_time and benchmark:
            if current_time - start_time > (2 * benchmark):
                stale_chunks.append(row)
                
    conn.close()
    return stale_chunks

def reset_chunk(chunk_id):
    """Riporta lo stato di un chunk a 'in_attesa' rimuovendo il client assegnato."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE Chunks 
        SET status = 'in_attesa', client_id = NULL, start_time = NULL 
        WHERE id = ?
    ''', (chunk_id,))
    conn.commit()
    conn.close()

def assign_pending_chunk(client_id, current_time):
    """Trova un chunk libero e lo assegna al client richiedente gestendo la priorità."""
    max_tentativi = 5 
    for tentativo in range(max_tentativi):
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('BEGIN IMMEDIATE')
            
            cursor.execute('''
                SELECT Chunks.* FROM Chunks
                JOIN Videos ON Chunks.video_id = Videos.id
                WHERE Chunks.status = "in_attesa" 
                ORDER BY Videos.priorita DESC, RANDOM() 
                LIMIT 1
            ''')
            chunk = cursor.fetchone()
            
            if not chunk:
                conn.rollback()
                return None
            
            cursor.execute('''
                UPDATE Chunks 
                SET status = 'in_esecuzione', client_id = ?, start_time = ? 
                WHERE id = ? AND status = 'in_attesa'
            ''', (client_id, current_time, chunk['id']))
            
            if cursor.rowcount > 0:
                conn.commit() 
                cursor.execute('SELECT * FROM Chunks WHERE id = ?', (chunk['id'],))
                chunk_aggiornato = cursor.fetchone()
                return chunk_aggiornato
            else:
                conn.rollback()
                continue
                
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                time.sleep(2) 
            else:
                raise e
        finally:
            conn.close()
            
    return None

def update_chunk_status(chunk_id, status):
    """Aggiorna lo stato lavorativo di un singolo chunk."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Chunks SET status = ? WHERE id = ?', (status, chunk_id))
    conn.commit()
    conn.close()

def are_all_chunks_completed(video_id):
    """Verifica se tutti i pezzi di un video sono stati contrassegnati come completati."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as count FROM Chunks WHERE video_id = ? AND status != "completato"', (video_id,))
    row = cursor.fetchone()
    conn.close()
    return row['count'] == 0

def get_remaining_chunks_count(video_id):
    """Restituisce il numero di chunk rimanenti per un determinato video."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as count FROM Chunks WHERE video_id = ? AND status != "completato"', (video_id,))
    row = cursor.fetchone()
    conn.close()
    return row['count']

def get_dashboard_stats():
    """Genera e aggrega tutte le statistiche per popolare l'interfaccia della Dashboard."""
    conn = get_connection()
    cursor = conn.cursor()
    
    stats = {
        "client_attivi": 0,
        "client_list": [],
        "videos": [],
        "global_eta_seconds": 0,
        "global_totali": 0,       
        "global_completati": 0    
    }
    
    current_time = time.time()
    cursor.execute('SELECT client_id, benchmark_time, last_seen, ip_address FROM Clients ORDER BY last_seen DESC')
    clients_db = cursor.fetchall()
    
    network_cps = 0.0 
    for row in clients_db:
        client_info = dict(row)
        client_info['seconds_ago'] = round(current_time - client_info['last_seen'])
        stats['client_list'].append(client_info)
        
        if client_info['benchmark_time'] and client_info['benchmark_time'] > 0:
            network_cps += 1.0 / client_info['benchmark_time']
            
    stats['client_attivi'] = len(clients_db)
            
    cursor.execute("SELECT * FROM Videos ORDER BY priorita DESC, id ASC")
    videos = cursor.fetchall()
    
    total_pending_chunks = 0
    
    for video in videos:
        video_id = video['id']
        video_stat = {
            "id": video_id,
            "nome": os.path.basename(video['filename']),
            "status": video['status'],
            "original_size": video['original_size'], 
            "final_size": video['final_size'],       
            "priorita": video['priorita'],
            "totali": 0,
            "completati": 0,
            "in_esecuzione": 0,
            "in_attesa": 0
        }
        
        cursor.execute("SELECT status, COUNT(*) as count FROM Chunks WHERE video_id = ? GROUP BY status", (video_id,))
        rows = cursor.fetchall()
        for row in rows:
            stato = row['status']
            conteggio = row['count']
            video_stat['totali'] += conteggio
            
            if stato == 'completato':
                video_stat['completati'] = conteggio
            elif stato == 'in_esecuzione':
                video_stat['in_esecuzione'] = conteggio
            elif stato == 'in_attesa':
                video_stat['in_attesa'] = conteggio
                
        if video_stat['status'] != 'completato':
            total_pending_chunks += (video_stat['in_attesa'] + video_stat['in_esecuzione'])

        stats['global_totali'] += video_stat['totali']
        stats['global_completati'] += video_stat['completati']
                
        stats["videos"].append(video_stat)
    
    if total_pending_chunks > 0:
        if network_cps > 0:
            stats['global_eta_seconds'] = total_pending_chunks / network_cps
        else:
            stats['global_eta_seconds'] = -1 
    else:
        stats['global_eta_seconds'] = 0 
                
    conn.close()
    return stats

def get_all_videos():
    """Restituisce l'elenco completo di tutti i video a prescindere dallo stato."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Videos')
    videos = cursor.fetchall()
    conn.close()
    return videos

def delete_video(video_id):
    """Elimina definitivamente un video e i suoi chunk dal database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM Chunks WHERE video_id = ?', (video_id,))
    cursor.execute('DELETE FROM Videos WHERE id = ?', (video_id,))
    conn.commit()
    conn.close()
