"""Discord transport for v10: visible lifecycle, owner allowlist and isolated read-only chats."""
import asyncio
import threading
import time


class DiscordRuntime:
    def __init__(self,host):
        self.host=host
        self.client=self.loop=self.thread=None
        self.state='stopped'
        self.message='Discord fermo.'
        self.owners=set()
        self.histories={}
        self.cooldowns={}
        self._pending_users=set()
        self._ever_ready=False
        self._closing=False
        self._generation=0
        self._cancel=threading.Event()
        self._reply_lock=threading.Lock()
        self._work_lock=threading.Lock()
        self._reply_workers=0
        self.last_work_at=0

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    @property
    def is_busy(self):
        with self._work_lock:
            return bool(self._pending_users or self._reply_workers)

    def _state(self,state,message):
        token=self.host.settings.secret('DISCORD_TOKEN')
        self.state=state
        self.message=message.replace(token,'[token]') if token else message
        self.host.events.put({'type':'discord_state','state':state,'message':self.message})

    def start(self):
        self.host.require_admin()
        if self.running:return self.message
        if not self.host.settings.secret('DISCORD_TOKEN'):
            self._state('error','Token Discord mancante. Configuralo in Discord.')
            return self.message
        try:import discord
        except ImportError:
            self._state('error','Discord non installato. Installa requirements-discord.txt.')
            return self.message
        self._closing=False
        self._generation+=1
        self._cancel=threading.Event()
        self.owners.clear()
        self.histories.clear()
        self.cooldowns.clear()
        self._pending_users.clear()
        self._ever_ready=False
        self._session_user=self.host.session_user
        self._state('connecting','Connessione a Discord…')
        self.thread=threading.Thread(target=self._run,daemon=True,name='jarvis-discord')
        self.thread.start()
        return self.message

    def _run(self):
        import discord
        try:
            self.loop=asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            intents=discord.Intents.default()
            intents.message_content=bool(self.host._cfg.get('discord_all_channel_messages',False))
            self.client=discord.Client(intents=intents)
            self.client.event(self.on_ready)
            self.client.event(self.on_message)
            self.client.event(self.on_disconnect)
            self.client.event(self.on_resumed)
            self.client.event(self.on_error)
            self.loop.run_until_complete(self._connect())
        except discord.LoginFailure:
            self._state('error','Token Discord non valido. Aggiornalo nelle impostazioni Discord.')
        except discord.PrivilegedIntentsRequired:
            self._state('error','Abilita Message Content Intent nel Developer Portal, oppure disattiva le risposte a tutti i messaggi del canale.')
        except Exception as exc:
            if not self._closing:self._state('error',f'Discord: connessione non riuscita ({type(exc).__name__}).')
        finally:
            if self.loop and not self.loop.is_closed():
                if self.client and not self.client.is_closed():
                    self.loop.run_until_complete(self.client.close())
                pending=asyncio.all_tasks(self.loop)
                for task in pending:task.cancel()
                if pending:self.loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
                self.loop.close()
            if self._closing:self._state('stopped','Discord fermo.')

    async def _connect(self):
        async def watchdog():
            await asyncio.sleep(30)
            if not self._ever_ready and not self._closing:
                self._state('error','Discord non è pronto dopo 30 secondi. Controlla connessione, token e permessi del bot.')
                await self.client.close()
        watcher=asyncio.create_task(watchdog())
        try:await self.client.start(self.host.settings.secret('DISCORD_TOKEN'))
        finally:
            watcher.cancel()
            try:await watcher
            except asyncio.CancelledError:pass

    async def on_ready(self):
        try:
            info=await self.client.application_info()
            owner_id=getattr(getattr(info,'team',None),'owner_id',None) or getattr(info.owner,'id',None)
            self.owners={int(value) for value in self.host._cfg.get('discord_allowed_users',[]) if str(value).isdigit()}
            if not self.owners and owner_id:self.owners.add(int(owner_id))
            if not self.owners:
                self._state('error','Discord connesso, ma manca un utente autorizzato. Imposta il tuo ID Discord.')
                return
            self._ever_ready=True
            self._state('ready','Discord online · usa un messaggio diretto o @'+self.client.user.name+'.')
        except Exception as exc:
            self._state('error',f'Discord connesso, verifica del proprietario non riuscita ({type(exc).__name__}).')

    async def on_disconnect(self):
        if not self._closing:self._state('connecting','Discord disconnesso · riconnessione in corso…')

    async def on_resumed(self):
        if not self._closing:self._state('ready','Discord riconnesso · usa un messaggio diretto o una menzione.')

    async def on_error(self,event,*args,**kwargs):
        self._state('error','Errore Discord durante '+str(event)+'. Controlla la connessione e i permessi del bot.')

    def accepts(self,message):
        if getattr(message.author,'bot',False) or message.author.id not in self.owners:return False
        channel=self.host.settings.secret('DISCORD_CHANNEL_ID').strip()
        if channel and getattr(message,'guild',None) and str(message.channel.id)!=channel:return False
        return (getattr(message,'guild',None) is None or self.client.user.mentioned_in(message)
                or (bool(channel) and self.host._cfg.get('discord_all_channel_messages',False))
                or str(message.content).lower().startswith('!jarvis'))

    async def on_message(self,message):
        if self._closing or not self.accepts(message):return
        text=str(message.content or '').strip()
        for tag in (f'<@{self.client.user.id}>',f'<@!{self.client.user.id}>'):
            text=text.replace(tag,'').strip()
        if text.lower().startswith('!jarvis'):text=text[7:].strip()
        if not text:
            await self._send(message,'Scrivimi una richiesta dopo la menzione. Se il testo non arriva, controlla Message Content Intent.')
            return
        if text.lower() in {'!help','help','aiuto'}:
            await self._send(message,'Scrivimi in privato o menzionami. Su Discord uso permessi ridotti; le azioni che richiedono autorizzazione si eseguono nell’app.')
            return
        now=time.monotonic()
        if now-self.cooldowns.get(message.author.id,0)<2:
            await self._send(message,'Attendi un momento prima della prossima richiesta.')
            return
        self.cooldowns[message.author.id]=now
        if message.author.id in self._pending_users:
            await self._send(message,'Sto ancora completando la richiesta precedente.')
            return
        self._pending_users.add(message.author.id)
        self.last_work_at=time.monotonic()
        generation=self._generation
        try:
            async with message.channel.typing():
                answer=await asyncio.wait_for(self._background_reply(message.author.id,message.channel.id,text),150)
            if generation==self._generation:
                await self._send(message,answer or 'Non ho ricevuto una risposta dal modello. Riprova.')
        except asyncio.TimeoutError:
            self._state('error','Discord: il modello non ha risposto entro 150 secondi.')
            await self._send(message,'Il modello non ha risposto in tempo. Controlla Modelli e servizi nell’app.')
        except Exception as exc:
            self._state('error',f'Discord: richiesta non completata ({type(exc).__name__}).')
            await self._send(message,'Richiesta non completata. Controlla lo stato Discord nell’app JARVIS.')
        finally:
            self.last_work_at=time.monotonic()
            self._pending_users.discard(message.author.id)

    async def _background_reply(self,user_id,channel_id,text):
        loop=asyncio.get_running_loop()
        future=loop.create_future()
        generation=self._generation
        def publish(value,error):
            if not future.done():
                if error:future.set_exception(error)
                else:future.set_result(value)
        def run():
            try:value,error=self._reply(user_id,channel_id,text,generation),None
            except Exception as exc:value,error=None,exc
            finally:
                with self._work_lock:
                    self.last_work_at=time.monotonic()
                    self._reply_workers-=1
            try:loop.call_soon_threadsafe(publish,value,error)
            except RuntimeError:pass  # The user has stopped Discord.
        with self._work_lock:self._reply_workers+=1
        try:threading.Thread(target=run,daemon=True,name='discord-reply').start()
        except Exception:
            with self._work_lock:self._reply_workers-=1
            raise
        return await future

    def _reply(self,user_id,channel_id,text,generation=None):
        with self._reply_lock:
            if self._closing or (generation is not None and generation!=self._generation):return ''
            if self.host.session_user!=self._session_user:return 'La sessione locale è cambiata. Ricollega Discord dall’app.'
            key=(user_id,channel_id)
            history=self.histories.setdefault(key,[])
            answer=self.host.discord_reply(text,history,cancel_event=self._cancel)
            if self._closing or (generation is not None and generation!=self._generation):return ''
            self.histories[key]=history[-16:]
            return answer

    async def _send(self,message,text):
        if self._closing:return
        import discord
        token=self.host.settings.secret('DISCORD_TOKEN')
        text=str(text).replace(token,'[token]') if token else str(text)
        for start in range(0,len(text),1900):
            await message.channel.send(text[start:start+1900],allowed_mentions=discord.AllowedMentions.none())

    def stop(self):
        self._closing=True
        self._cancel.set()
        self._generation+=1
        self.histories.clear()
        if self.loop and self.loop.is_running() and self.client:
            asyncio.run_coroutine_threadsafe(self.client.close(),self.loop)
        self._state('stopped','Discord fermo.')
        return self.message
