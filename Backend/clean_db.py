import sqlite3
import pandas as pd
import shutil
import os
from datetime import datetime
from pathlib import Path

# Configuration - use same path resolution as database.py
CENTRAL_SERVER_DIR = Path(__file__).parent
DB_PATH = CENTRAL_SERVER_DIR / "teams_dialer.db"
BACKUP_PATH = CENTRAL_SERVER_DIR / "teams_dialer_backup.db"


def clean_database():
    db_path_str = str(DB_PATH)
    backup_path_str = str(BACKUP_PATH)
    
    if not os.path.exists(db_path_str):
        print(f"Database not found at {db_path_str}")
        return

    # 1. Create a backup
    print(f"Creating backup at {backup_path_str}...")
    shutil.copy2(db_path_str, backup_path_str)

    # 2. Connect and load data
    conn = sqlite3.connect(db_path_str)
    df = pd.read_sql_query("SELECT * FROM calls", conn)
    original_count = len(df)
    print(f"Loaded {original_count} rows.")

    # --- CLEANING LOGIC ---

    # Step A: Mark 'Pending' and 'In Progress' for deletion
    # These are temporary states that mess up statistics.
    df['to_delete'] = df['disposition'].isin(['Pending', 'In Progress'])

    # Step B: Deduplicate "Manual Entry" spam
    # We keep the first occurrence of any Manual Entry with the exact same Agent + Timestamp
    manual_mask = df['phone'] == 'Manual Entry'
    # Identify duplicates to mark for deletion (keep='first' means duplicate are True)
    manual_dupes = df[manual_mask].duplicated(subset=['agent_id', 'timestamp'], keep='first')
    # Update the main delete mask using the index of the duplicates
    df.loc[manual_dupes[manual_dupes].index, 'to_delete'] = True

    # Step C: Merge "No Disposition" with "Real Disposition"
    # We sort by Agent -> Phone -> Time to align the pairs
    df = df.sort_values(by=['agent_id', 'phone', 'timestamp'])
    
    # We will iterate and link "No Disposition" (which has duration) with the next "Real Dispo"
    last_no_dispo_idx = None
    
    # Create a mapping for easier row access
    # We iterate through the DataFrame index
    for idx in df.index:
        row = df.loc[idx]
        
        # Skip if already marked for deletion (e.g. Pending rows)
        if row['to_delete']:
            # If we hit a Start/Pending row, it breaks the chain of the previous call
            last_no_dispo_idx = None
            continue

        disposition = row['disposition']

        if disposition == 'No Disposition':
            # Found a potential "End of Call" record with valid duration
            # Mark it as the active candidate to be merged
            last_no_dispo_idx = idx
        
        elif disposition != 'Manual Entry':
            # This is a "Real Disposition" (e.g., No Response, Voicemail)
            if last_no_dispo_idx is not None:
                # We have a preceding "No Disposition" row! Merge them.
                
                # 1. Transfer duration if the current one is missing/zero
                prev_duration = df.loc[last_no_dispo_idx, 'duration']
                curr_duration = df.loc[idx, 'duration']
                if curr_duration == 0 and prev_duration > 0:
                    df.loc[idx, 'duration'] = prev_duration
                
                # 2. Mark the "No Disposition" row for deletion since we merged its info
                df.loc[last_no_dispo_idx, 'to_delete'] = True
                
                # 3. Clear the tracker
                last_no_dispo_idx = None
            else:
                # Standalone disposition (clicked during call?), keep as is.
                pass

    # --- APPLY CHANGES ---

    # Filter out the rows marked for deletion
    df_clean = df[~df['to_delete']].copy()
    
    # Drop the temporary column
    df_clean = df_clean.drop(columns=['to_delete'])

    # Sort by timestamp for tidiness
    df_clean = df_clean.sort_values(by='timestamp')

    final_count = len(df_clean)
    print(f"Cleaning complete. Reduced rows from {original_count} to {final_count} (-{original_count - final_count})")

    # Write back to database
    # We replace the table entirely
    try:
        df_clean.to_sql('calls', conn, if_exists='replace', index=False)
        
        # Re-create indexes that might be lost during 'replace'
        cursor = conn.cursor()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_time ON calls(agent_id, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_disposition ON calls(disposition)")
        conn.commit()
        print("Database successfully optimized.")
    except Exception as e:
        print(f"Error writing to database: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    clean_database()

