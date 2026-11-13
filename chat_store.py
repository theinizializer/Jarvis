"""Local conversation archive. SQLite transactions keep messages and tool state together."""
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager


class ChatStore:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_CREAT|os.O_WRONLY,0o600);os.close(fd)
        os.chmod(self.path,0o600)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, owner TEXT NOT NULL, profile TEXT NOT NULL, title TEXT NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL)')
            columns={row[1] for row in db.execute('PRAGMA table_info(chats)')}
            if 'pinned' not in columns:db.execute('ALTER TABLE chats ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE INDEX IF NOT EXISTS chats_owner_profile ON chats(owner,profile,pinned,updated)')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.create_function('casefold',1,lambda value:(value or '').casefold(),deterministic=True)
        try:
            with db:yield db
        finally:db.close()

    def save(self, chat_id, owner, profile, history, transcript, status='ready'):
        chat_id=chat_id or uuid.uuid4().hex
        title=next((m['text'].strip() for m in transcript if m['speaker']=='Tu'), 'Conversazione')
        title=' '.join(title.split())[:70] or 'Conversazione'
        # Private reasoning is never needed to resume a conversation.
        clean=[{k:v for k,v in m.items() if k not in {'reasoning','reasoning_content','thinking'}} for m in history]
        payload=json.dumps({'history':clean,'transcript':transcript,'status':status},ensure_ascii=False)
        with self.connect() as db:
            existing=db.execute('SELECT owner,profile FROM chats WHERE id=?',(chat_id,)).fetchone()
            if existing and existing!=(owner,profile):raise PermissionError('Questa chat appartiene a un altro utente o profilo.')
            db.execute('INSERT INTO chats (id,owner,profile,title,updated,payload) VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,updated=excluded.updated,payload=excluded.payload',
                       (chat_id,owner,profile,title,time.time(),payload))
        return chat_id

    def list(self, owner, profile, query=''):
        with self.connect() as db:
            return [{'id':row[0],'title':row[1],'updated':row[2],'pinned':bool(row[3])} for row in db.execute(
                'SELECT id,title,updated,pinned FROM chats WHERE owner=? AND profile=? AND (?=\'\' OR instr(casefold(title),?)>0 OR instr(casefold(payload),?)>0) ORDER BY pinned DESC,updated DESC',
                (owner,profile,query,query.casefold(),query.casefold()))]

    def pin(self,chat_id,owner,profile,pinned):
        with self.connect() as db:
            result=db.execute('UPDATE chats SET pinned=? WHERE id=? AND owner=? AND profile=?',(int(bool(pinned)),chat_id,owner,profile))
            if not result.rowcount:raise KeyError('Chat non trovata per questo utente e profilo.')

    def delete(self,chat_id,owner,profile):
        with self.connect() as db:
            row=db.execute('SELECT id,owner,profile,title,updated,payload,pinned FROM chats WHERE id=? AND owner=? AND profile=?',(chat_id,owner,profile)).fetchone()
            if not row:raise KeyError('Chat non trovata per questo utente e profilo.')
            db.execute('DELETE FROM chats WHERE id=? AND owner=? AND profile=?',(chat_id,owner,profile))
        return dict(zip(('id','owner','profile','title','updated','payload','pinned'),row))

    def restore(self,backup,owner,profile):
        if (backup.get('owner'),backup.get('profile'))!=(owner,profile):raise PermissionError('Chat di un altro utente o profilo.')
        with self.connect() as db:
            db.execute('INSERT INTO chats (id,owner,profile,title,updated,payload,pinned) VALUES (?,?,?,?,?,?,?)',
                       tuple(backup[k] for k in ('id','owner','profile','title','updated','payload','pinned')))

    def load(self, chat_id, owner, profile):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM chats WHERE id=? AND owner=? AND profile=?',(chat_id,owner,profile)).fetchone()
        if not row:raise KeyError('Chat non trovata per questo utente e profilo.')
        return json.loads(row[0])
