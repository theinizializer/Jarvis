import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chat_store import ChatStore
from app_config import Settings
from jarvis_v10 import Jarvis


class ChatActionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=ChatStore(self.root/'chats.sqlite')

    def tearDown(self):self.temp.cleanup()

    def save(self,title,body='',owner='user',profile='main'):
        return self.store.save(None,owner,profile,[],[{'speaker':'Tu','text':title},{'speaker':'JARVIS','text':body}])

    def test_pin_survives_updates_and_restart_and_orders_before_recent_chats(self):
        first=self.save('Prima');second=self.save('Seconda')
        self.store.pin(first,'user','main',True)
        self.store.save(first,'user','main',[],[{'speaker':'Tu','text':'Prima aggiornata'}])
        reopened=ChatStore(self.store.path);chats=reopened.list('user','main')
        self.assertEqual(chats[0]['id'],first);self.assertTrue(chats[0]['pinned'])
        reopened.pin(first,'user','main',False);self.assertFalse(reopened.list('user','main')[0]['pinned'])

    def test_search_finds_title_or_body_unicode_case_and_literal_percent(self):
        first=self.save('Spesa','CAFFÈ speciale 50%');self.save('Altro');self.save('Spesa','CAFFÈ',owner='another')
        for query in ('spesa','caffè','50%'):
            self.assertEqual([c['id'] for c in self.store.list('user','main',query)],[first])
        self.assertFalse(self.store.list('user','main',"' OR 1=1 --"))

    def test_delete_and_undo_preserve_content_pin_and_owner_isolation(self):
        first=self.save('Importante','contenuto preciso');self.store.pin(first,'user','main',True)
        with self.assertRaises(KeyError):self.store.delete(first,'another','main')
        with self.assertRaises(KeyError):self.store.pin(first,'user','other',True)
        backup=self.store.delete(first,'user','main')
        self.assertFalse(self.store.list('user','main'))
        with self.assertRaises(KeyError):self.store.load(first,'user','main')
        with self.assertRaises(PermissionError):self.store.restore(backup,'another','main')
        self.store.restore(backup,'user','main')
        self.assertTrue(self.store.list('user','main')[0]['pinned'])
        self.assertEqual(self.store.load(first,'user','main')['transcript'][-1]['text'],'contenuto preciso')

    def test_existing_six_column_archive_is_migrated_without_data_loss(self):
        old=self.root/'old.sqlite'
        with sqlite3.connect(old) as db:
            db.execute('CREATE TABLE chats (id TEXT PRIMARY KEY,owner TEXT,profile TEXT,title TEXT,updated REAL,payload TEXT)')
            db.execute('INSERT INTO chats VALUES (?,?,?,?,?,?)',('old','user','main','Vecchia',1,json.dumps({'history':[],'transcript':[]})))
        db.close()
        migrated=ChatStore(old);self.assertEqual(migrated.list('user','main')[0]['id'],'old')
        self.assertFalse(migrated.list('user','main')[0]['pinned'])
        migrated.pin('old','user','main',True)

    def test_active_chat_deletion_is_blocked_and_deleted_chat_is_not_resurrected(self):
        with patch.dict(os.environ,{'XDG_DATA_HOME':str(self.root/'data')}):
            host=Jarvis(settings=Settings(self.root/'cfg'));host.role='admin'
            host.record_chat_message('Tu','Da eliminare');chat_id=host._chat_id
            host._agent=Mock(state={'task_id':'active'})
            with self.assertRaises(RuntimeError):host.delete_conversation(chat_id)
            host._agent=None;host.delete_conversation(chat_id)
            self.assertIsNone(host._chat_id);self.assertFalse(host._history);self.assertFalse(host._transcript)
            host.record_chat_message('Tu','Una nuova chat');self.assertNotEqual(host._chat_id,chat_id)
            self.assertEqual(host.undo_delete_conversation(),chat_id)
            self.assertEqual(len(host.conversations()),2);host._shutdown()


if __name__=='__main__':unittest.main()
