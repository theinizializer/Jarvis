"""Persistent desktop settings. Secrets stay separate from general configuration."""
from __future__ import annotations
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def config_dir():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'jarvis'

def data_dir():
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'jarvis-data'

def desktop_dir():
    """Resolve the user's configured Desktop/Scrivania without invoking a shell."""
    home = Path.home()
    configured = os.environ.get('XDG_DESKTOP_DIR', '').strip().strip('"')
    user_dirs = config_dir().parent / 'user-dirs.dirs'
    if not configured and user_dirs.is_file():
        try:
            for line in user_dirs.read_text(encoding='utf-8').splitlines():
                if line.startswith('XDG_DESKTOP_DIR='):
                    configured = line.split('=', 1)[1].strip().strip('"')
                    break
        except OSError:
            pass
    if configured:
        configured = configured.replace('$HOME', str(home))
        return Path(os.path.expanduser(os.path.expandvars(configured)))
    for name in ('Desktop', 'Scrivania'):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    return home / 'Desktop'

def atomic_write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

RESPONSE_TONES = {
    'Bilanciato': '',
    'Conciso': 'Rispondi direttamente. Includi i dettagli necessari per completare il compito.',
    'Tecnico': 'Usa termini tecnici precisi. Esplicita ipotesi, verifiche e limiti rilevanti.',
    'Amichevole': 'Usa un tono colloquiale e naturale. Spiega i passaggi con parole semplici.',
}

DEFAULTS = {
    'provider': 'ollama', 'model': 'llama3.2',
    'ollama_host': 'http://127.0.0.1:11434', 'lang': 'it',
    'tts': False, 'stt': False, 'microphone': None,
    'session_lock_enabled':True,'session_lock_minutes':2,
    'voice_standby_enabled':True,'voice_standby_minutes':2,
    'discord_allowed_users': [], 'discord_all_channel_messages': False,'discord_enabled':False,
    'vision_provider': 'nvidia', 'vision_models': {},
    'active_profile': 'default',
    'profiles': {'default': {'name': 'Radostin', 'personality': 'Composto, preciso, ironia asciutta.'}},
    'skills': {}, 'mcp_servers': {},
    'provider_models': {}, 'nvidia_thinking': False, 'motion': 'full', 'hud_theme': 'Blu ionico', 'hud_color': '#68DBFF', 'hud_glow': 55, 'ui_background': '#050911',
    'provider_keys': {}, 'provider_primary_key_enabled': {},
    'auto_fallback': True, 'ollama_autostart': True,
    'ollama_timeout': 300,
    'groq_retry_wait': 120, 'groq_small_fallback': False,
    'groq_input_budget': 5000, 'groq_reasoning_effort': 'medium',
}
SERVICE_FIELDS = (
    'GROQ_API_KEY', 'CEREBRAS_API_KEY', 'NVIDIA_API_KEY', 'OPENAI_API_KEY',
    'DISCORD_TOKEN', 'DISCORD_CHANNEL_ID', 'EMAIL_ADDRESS', 'EMAIL_PASSWORD',
    'GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD',
    'IMAP_HOST', 'SMTP_HOST', 'SMTP_PORT', 'JARVIS_ADDRESS',
)

def validate_idle_minutes(value):
    text=str(value).strip()
    if not re.fullmatch(r'[0-9]{1,4}',text) or not 1<=int(text)<=1440:
        raise ValueError('Scegli un numero intero di minuti da 1 a 1440.')
    return int(text)

def inactivity_timeout(values, *, voice=False):
    prefix='voice_standby' if voice else 'session_lock'
    if values.get(prefix+'_enabled',True) is False:
        return None
    try:minutes=validate_idle_minutes(values.get(prefix+'_minutes',2))
    except ValueError:minutes=2
    return minutes*60

def read_env(path):
    result = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:]
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key.strip()):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            result[key.strip()] = value
    return result

class Settings:
    def __init__(self, directory=None):
        self._use_legacy_services = directory is None
        self._legacy_services = None
        self.legacy_service_error = ''
        self.directory = Path(directory) if directory else config_dir()
        self.path = self.directory / 'config.json'
        self.values = deepcopy(DEFAULTS)
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(loaded, dict):
                raise ValueError('config.json deve contenere un oggetto JSON')
            self.values.update(loaded)
        # Legacy config is read once; all future skill writes use config.json.
        old = ROOT / 'config/skills_config.json'
        if not self.values['skills'] and old.is_file():
            self.values['skills'] = json.loads(old.read_text(encoding='utf-8'))
        self.secrets = read_env(ROOT / '.env')
        self.secrets.update(read_env(self.directory / '.env'))
        # Process environment takes priority at launch (not after an explicit UI save).
        for key in set(SERVICE_FIELDS) | set(self.secrets):
            if key in os.environ:
                self.secrets[key] = os.environ[key]
        self.apply_environment()

    def secret(self, name, default=''):
        """Resolve current configuration, then the v9 encrypted service store in memory only."""
        current=self.secrets.get(name) or os.environ.get(name)
        if current:
            return current
        legacy_names={'GMAIL_ADDRESS','GMAIL_APP_PASSWORD','EMAIL_ADDRESS','EMAIL_PASSWORD',
                      'DISCORD_TOKEN','DISCORD_CHANNEL_ID'}
        if not self._use_legacy_services or name not in legacy_names:
            return default
        if self._legacy_services is None:
            self._legacy_services={}
            try:
                from jarvis_secrets import SecretsManager,CRYPTO_OK,_default_enc_path
                path=_default_enc_path()
                if path.is_file():
                    if not CRYPTO_OK:
                        self.legacy_service_error='Installa cryptography per leggere le credenziali cifrate della v9.'
                    else:
                        data=SecretsManager(path).load()
                        self._legacy_services={key:value for key,value in data.items() if key in legacy_names}
            except Exception:
                self.legacy_service_error='Archivio cifrato della v9 non leggibile.'
        return self._legacy_services.get(name,default)

    def apply_environment(self):
        for key, value in self.secrets.items():
            os.environ[key] = value

    def save(self, *, updates=None):
        values = self.values if updates is None else {**self.values, **updates}
        atomic_write(self.path, json.dumps(values, ensure_ascii=False, indent=2) + '\n')
        if updates is not None:
            self.values.update(updates)

    def save_secrets(self, updates):
        merged = {**self.secrets, **updates}
        for key, value in merged.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                raise ValueError('Nome variabile non valido: ' + key)
            if '\n' in value or '\r' in value:
                raise ValueError('I valori dei servizi devono occupare una sola riga')
        # Never interpolate or execute .env as a shell program.
        atomic_write(self.directory / '.env', ''.join(f'{k}={v}\n' for k, v in sorted(merged.items())))
        self.secrets = merged
        self.apply_environment()

    def profile(self):
        profiles = self.values['profiles']
        return profiles.get(self.values['active_profile'], next(iter(profiles.values())))
