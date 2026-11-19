import os
import sqlite3
from pathlib import Path
from cryptography.fernet import Fernet

class Store:
    def __init__(self, data_dir: str):
        self.path = Path(data_dir)
        self.path.mkdir(parents=True, exist_ok=True)
        key_path = self.path / ".fernet.key"
        if not key_path.exists():
            key_path.write_bytes(Fernet.generate_key())
            os.chmod(key_path, 0o600)
        self.fernet = Fernet(key_path.read_bytes())
        self.db = self.path / "assistant.sqlite3"
        with self.connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS secrets
                (name TEXT PRIMARY KEY, value BLOB NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS oauth_pending
                (request_token TEXT PRIMARY KEY, request_secret BLOB NOT NULL)""")

    def connect(self):
        return sqlite3.connect(self.db)

    def put_secret(self, name: str, value: str):
        encrypted = self.fernet.encrypt(value.encode())
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO secrets(name,value) VALUES(?,?)", (name, encrypted))

    def get_secret(self, name: str):
        with self.connect() as con:
            row = con.execute("SELECT value FROM secrets WHERE name=?", (name,)).fetchone()
        return self.fernet.decrypt(row[0]).decode() if row else None

    def put_pending(self, token: str, secret: str):
        encrypted = self.fernet.encrypt(secret.encode())
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO oauth_pending(request_token,request_secret) VALUES(?,?)", (token, encrypted))

    def get_pending(self, token: str):
        with self.connect() as con:
            row = con.execute("SELECT request_secret FROM oauth_pending WHERE request_token=?", (token,)).fetchone()
        return self.fernet.decrypt(row[0]).decode() if row else None

    def delete_pending(self, token: str):
        with self.connect() as con:
            con.execute("DELETE FROM oauth_pending WHERE request_token=?", (token,))
