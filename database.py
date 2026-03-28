import sqlite3
import os
import time

# Percorso in cui verrà salvato il database SQLite
DB_PATH = 'resizer.db' 

def clean_db():
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print("Database precedente rimosso con successo.")
        except Exception as e:
            print(f"Impossibile rimuovere il database: {e}")

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Clients (
            client_id TEXT PRIMARY KEY,
            benchmark_time REAL
        )
    ''')
    
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

def save_client_benchmark(client_id, benchmark_time):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO Clients (client_id, benchmark_time)
        VALUES (?, ?)
        ON CONFLICT(client_id) DO UPDATE SET benchmark_time=excluded.benchmark_time
    ''', (client_id, benchmark_time))
    conn.commit()
    conn.close()

def get_client(client_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Clients WHERE client_id = ?', (client_id,))
    client = cursor.fetchone()
    conn.close()
    return client

def insert_video(filename):
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
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Videos SET final_size = ? WHERE id = ?', (final_size, video_id))
    conn.commit()
    conn.close()

def set_video_priority(video_id, priorita):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Videos SET priorita = ? WHERE id = ?', (priorita, video_id))
    conn.commit()
    conn.close()

def get_video_by_status(status):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Videos WHERE status = ? LIMIT 1', (status,))
    video = cursor.fetchone()
    conn.close()
    return video

def get_videos_by_status(status):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Videos WHERE status = ?', (status,))
    videos = cursor.fetchall()
    conn.close()
    return videos

def get_video_by_id(video_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Videos WHERE id = ?', (video_id,))
    video = cursor.fetchone()
    conn.close()
    return video

def update_video_status(video_id, status):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Videos SET status = ? WHERE id = ?', (status, video_id))
    conn.commit()
    conn.close()

def insert_chunk(video_id, chunk_filename):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO Chunks (video_id, chunk_filename, status)
        VALUES (?, ?, 'in_attesa')
    ''', (video_id, chunk_filename))
    conn.commit()
    conn.close()

def get_chunks_by_video(video_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Chunks WHERE video_id = ? ORDER BY id ASC', (video_id,))
    chunks = cursor.fetchall()
    conn.close()
    return chunks

def get_chunk_by_id(chunk_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Chunks WHERE id = ?', (chunk_id,))
    chunk = cursor.fetchone()
    conn.close()
    return chunk

def get_stale_chunks(current_time):
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
                print(f"Database occupato da un altro client. Attesa di 2 secondi (Tentativo {tentativo+1}/{max_tentativi})...")
                time.sleep(2) 
            else:
                raise e
        finally:
            conn.close()
            
    return None

def update_chunk_status(chunk_id, status):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE Chunks SET status = ? WHERE id = ?', (status, chunk_id))
    conn.commit()
    conn.close()

def are_all_chunks_completed(video_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as count FROM Chunks WHERE video_id = ? AND status != "completato"', (video_id,))
    row = cursor.fetchone()
    conn.close()
    return row['count'] == 0

def get_remaining_chunks_count(video_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as count FROM Chunks WHERE video_id = ? AND status != "completato"', (video_id,))
    row = cursor.fetchone()
    conn.close()
    return row['count']

def get_dashboard_stats():
    conn = get_connection()
    cursor = conn.cursor()
    
    stats = {
        "client_attivi": 0,
        "videos": [],
        "global_eta_seconds": 0,
        "global_totali": 0,       # <--- NUOVO PER BARRA GLOBALE
        "global_completati": 0    # <--- NUOVO PER BARRA GLOBALE
    }
    
    cursor.execute('''
        SELECT DISTINCT c.client_id, c.benchmark_time 
        FROM Clients c
        JOIN Chunks ch ON c.client_id = ch.client_id
        WHERE ch.status = 'in_esecuzione'
    ''')
    active_clients = cursor.fetchall()
    stats['client_attivi'] = len(active_clients)
    
    network_cps = 0.0 
    for client in active_clients:
        b_time = client['benchmark_time']
        if b_time and b_time > 0:
            network_cps += 1.0 / b_time
            
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
                
        # Conta quanti chunk totali rimangono
        if video_stat['status'] != 'completato':
            total_pending_chunks += (video_stat['in_attesa'] + video_stat['in_esecuzione'])

        # Somma totale per la barra globale
        stats['global_totali'] += video_stat['totali']
        stats['global_completati'] += video_stat['completati']
                
        stats["videos"].append(video_stat)
    
    # --- CALCOLO ETA GLOBALE ---
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
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Videos')
    videos = cursor.fetchall()
    conn.close()
    return videos

def delete_video(video_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM Chunks WHERE video_id = ?', (video_id,))
    cursor.execute('DELETE FROM Videos WHERE id = ?', (video_id,))
    conn.commit()
    conn.close()
