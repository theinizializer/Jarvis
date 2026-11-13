"""Credential migration and Discord delivery with synthetic secrets/transports only."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app_config import Settings
from discord_runtime import DiscordRuntime
from email_module import JarvisEmailMixin
from jarvis_v10 import Jarvis
from jarvis_secrets import CRYPTO_OK, SecretsManager


class ServiceCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{},clear=True)
        self.env.start()
        self.source=patch('app_config.ROOT',self.root)
        self.source.start()

    def tearDown(self):
        self.source.stop();self.env.stop();self.tmp.cleanup()

    @unittest.skipUnless(CRYPTO_OK,'cryptography optional')
    def test_encrypted_v9_fallback_stays_encrypted_and_out_of_environment(self):
        path=self.root/'.env.enc'
        manager=SecretsManager(path)
        path.write_bytes(manager._fernet.encrypt(manager._serialize({
            'GMAIL_ADDRESS':'old@example.test','GMAIL_APP_PASSWORD':'encrypted-password',
            'UNRELATED_PRIVATE_KEY':'must-not-import'})))
        before=path.read_bytes()
        with patch('jarvis_secrets._default_enc_path',return_value=path):
            settings=Settings(self.root/'config')
            settings._use_legacy_services=True
            settings.secrets['GMAIL_ADDRESS']=''
            self.assertEqual(settings.secret('GMAIL_ADDRESS'),'old@example.test')
            self.assertEqual(settings.secret('GMAIL_APP_PASSWORD'),'encrypted-password')
            self.assertEqual(settings.secret('UNRELATED_PRIVATE_KEY'),'')
            settings.save_secrets({'DISCORD_CHANNEL_ID':'123'})
        self.assertEqual(path.read_bytes(),before)
        self.assertNotIn('encrypted-password',(settings.directory/'.env').read_text())
        self.assertNotIn('GMAIL_APP_PASSWORD',os.environ)
        settings.secrets['GMAIL_ADDRESS']='new@example.test'
        self.assertEqual(settings.secret('GMAIL_ADDRESS'),'new@example.test')

    def test_explicit_configuration_precedes_legacy(self):
        settings=Settings(self.root/'config')
        settings._use_legacy_services=True
        settings._legacy_services={'GMAIL_ADDRESS':'old@example.test'}
        os.environ['GMAIL_ADDRESS']='environment@example.test'
        self.assertEqual(settings.secret('GMAIL_ADDRESS'),'environment@example.test')
        settings.secrets['GMAIL_ADDRESS']='saved@example.test'
        self.assertEqual(settings.secret('GMAIL_ADDRESS'),'saved@example.test')

    def test_gmail_verification_selects_readonly_and_closes(self):
        host=JarvisEmailMixin()
        values={'GMAIL_ADDRESS':' account@example.test ','GMAIL_APP_PASSWORD':'abcd efgh ijkl mnop'}
        host.settings=SimpleNamespace(secret=lambda name:values.get(name,''))
        connection=Mock()
        connection.select.return_value=('OK',[b'1'])
        with patch('email_module.imaplib.IMAP4_SSL',return_value=connection) as imap:
            self.assertIn('Gmail connesso',host.email_status())
        imap.assert_called_once_with('imap.gmail.com',timeout=15)
        connection.login.assert_called_once_with('account@example.test','abcdefghijklmnop')
        connection.select.assert_called_once_with('INBOX',readonly=True)
        connection.logout.assert_called_once()
        connection.fetch.assert_not_called()
        connection.store.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec('discord'),'discord.py optional')
class DiscordTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host=SimpleNamespace(settings=SimpleNamespace(secret=lambda key:{
            'DISCORD_TOKEN':'synthetic-private-token','DISCORD_CHANNEL_ID':''}.get(key,'')),
            events=queue.Queue(),_cfg={},session_user='admin',require_admin=Mock(),
            discord_reply=Mock(return_value='Risposta di prova'))
        self.bot=DiscordRuntime(self.host)
        self.bot._session_user='admin'
        user=SimpleNamespace(id=999,name='JARVIS',mentioned_in=lambda msg:'<@999>' in msg.content)
        self.bot.client=SimpleNamespace(user=user,application_info=AsyncMock(
            return_value=SimpleNamespace(owner=SimpleNamespace(id=123),team=None)))

    def message(self,content='ciao',author=123,guild=None):
        channel=Mock(id=88)
        channel.send=AsyncMock()
        typing=Mock()
        typing.__aenter__=AsyncMock()
        typing.__aexit__=AsyncMock()
        channel.typing.return_value=typing
        return SimpleNamespace(content=content,author=SimpleNamespace(id=author,bot=False),
                               channel=channel,guild=guild)

    async def test_ready_resolves_owner_and_dm_replies(self):
        await self.bot.on_ready()
        self.assertEqual(self.bot.state,'ready')
        self.assertEqual(self.bot.owners,{123})
        message=self.message()
        await self.bot.on_message(message)
        message.channel.send.assert_awaited_once()
        self.assertEqual(message.channel.send.await_args.args,('Risposta di prova',))
        self.assertEqual(self.host.discord_reply.call_args.args,('ciao',[]))
        self.assertIs(self.host.discord_reply.call_args.kwargs['cancel_event'],self.bot._cancel)
        self.assertFalse(self.bot.is_busy)
        self.assertGreater(self.bot.last_work_at,0)

    async def test_owner_allowlist_and_mentions(self):
        self.host._cfg['discord_allowed_users']=['456']
        await self.bot.on_ready()
        self.assertFalse(self.bot.accepts(self.message(author=123)))
        self.assertTrue(self.bot.accepts(self.message(author=456)))
        self.assertFalse(self.bot.accepts(self.message(author=456,guild=object())))
        self.assertTrue(self.bot.accepts(self.message('<@999> ciao',author=456,guild=object())))
        unauthorized=self.message(author=123)
        await self.bot.on_message(unauthorized)
        unauthorized.channel.send.assert_not_called()

    async def test_model_failure_has_reply_and_local_error(self):
        await self.bot.on_ready()
        self.host.discord_reply.side_effect=RuntimeError('synthetic-private-token')
        message=self.message()
        await self.bot.on_message(message)
        self.assertIn('Richiesta non completata',message.channel.send.await_args.args[0])
        self.assertEqual(self.bot.state,'error')
        self.assertNotIn('synthetic-private-token',self.bot.message)

    async def test_empty_model_response_has_explicit_reply(self):
        await self.bot.on_ready()
        self.host.discord_reply.return_value=''
        message=self.message()
        await self.bot.on_message(message)
        self.assertIn('Non ho ricevuto',message.channel.send.await_args.args[0])

    async def test_blank_mention_explains_missing_content(self):
        await self.bot.on_ready()
        message=self.message('<@999>',guild=object())
        await self.bot.on_message(message)
        self.assertIn('Message Content Intent',message.channel.send.await_args.args[0])
        self.host.discord_reply.assert_not_called()

    async def test_send_chunks_and_redacts_token(self):
        message=self.message()
        await self.bot._send(message,'synthetic-private-token'+'x'*2200)
        sent=[call.args[0] for call in message.channel.send.await_args_list]
        self.assertEqual(len(sent),2)
        self.assertTrue(all(len(chunk)<=1900 for chunk in sent))
        self.assertNotIn('synthetic-private-token',''.join(sent))

    async def test_stopping_drops_late_response_and_history(self):
        await self.bot.on_ready()
        def reply(text,history,**kwargs):
            self.bot.stop()
            history.append({'role':'user','content':text})
            return 'Late response'
        self.host.discord_reply.side_effect=reply
        message=self.message()
        await self.bot.on_message(message)
        message.channel.send.assert_not_called()
        self.assertEqual(self.bot.histories,{})

    async def test_worker_stays_busy_until_real_completion_after_waiter_is_cancelled(self):
        started,release=threading.Event(),threading.Event()
        def reply(*args,**kwargs):
            started.set()
            release.wait(2)
            return 'Done'
        self.host.discord_reply.side_effect=reply
        task=asyncio.create_task(self.bot._background_reply(123,88,'ciao'))
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.005)
            self.assertTrue(started.is_set())
            self.assertTrue(self.bot.is_busy)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(self.bot.is_busy,'A cancelled waiter does not mean its worker finished')
        finally:release.set()
        for _ in range(100):
            if not self.bot.is_busy:break
            await asyncio.sleep(.005)
        self.assertFalse(self.bot.is_busy)


class DiscordConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'XDG_DATA_HOME':str(self.root/'data')})
        self.env.start()
        self.host=Jarvis(settings=Settings(self.root/'config'))

    def tearDown(self):
        self.host._shutdown();self.env.stop();self.tmp.cleanup()

    def test_remote_context_is_secondary_and_history_is_separate(self):
        self.host._history=[{'role':'user','content':'private desktop message'}]
        original=list(self.host._history)
        def model(messages,tools):
            self.assertEqual(self.host.execution_role,'secondary')
            self.assertNotIn('private desktop message',str(messages))
            self.assertEqual([tool['function']['name'] for tool in tools],['system_info'])
            return {'content':'Ciao da Discord'}
        self.host._chat_completion=model
        history=[]
        self.assertEqual(self.host.discord_reply('ciao',history),'Ciao da Discord')
        self.assertEqual(self.host._history,original)
        self.assertEqual(len(history),2)
        self.assertEqual(self.host.execution_role,'admin')

    def test_mutating_hallucinated_tool_is_denied(self):
        destination=self.root/'must-not-exist.txt'
        call={'id':'1','type':'function','function':{'name':'write_text_file',
            'arguments':json.dumps({'path':str(destination),'content':'no'})}}
        replies=iter([{'tool_calls':[call]},{'content':'Operazione non disponibile'}])
        seen=[]
        def model(messages,tools):
            seen.extend(dict(m) for m in messages if m['role']=='tool')
            return next(replies)
        self.host._chat_completion=model
        self.host.discord_reply('scrivi',[])
        self.assertFalse(destination.exists())
        self.assertIn('non consentito',seen[0]['content'])

    def test_locked_session_does_not_contact_model(self):
        self.host.role='locked'
        self.host._chat_completion=Mock()
        self.assertIn('sospesa',self.host.discord_reply('ciao',[]))
        self.host._chat_completion.assert_not_called()

    def test_desktop_cancellation_does_not_cancel_discord_and_remote_stop_is_separate(self):
        self.host.cancel_current()
        desktop_cancel=self.host._agent_cancel
        self.assertTrue(desktop_cancel.is_set())
        def model(*args):
            self.assertIsNot(self.host._agent_cancel,desktop_cancel)
            self.assertFalse(self.host._agent_cancel.is_set())
            return {'content':'Remote request works'}
        self.host._chat_completion=model
        self.assertEqual(self.host.discord_reply('ciao',[]),'Remote request works')
        self.assertIs(self.host._agent_cancel,desktop_cancel)
        remote_cancel=__import__('threading').Event()
        remote_cancel.set()
        self.host._chat_completion=Mock()
        self.assertIn('interrotta',self.host.discord_reply('ciao',[],cancel_event=remote_cancel))
        self.host._chat_completion.assert_not_called()


if __name__=='__main__':unittest.main()
