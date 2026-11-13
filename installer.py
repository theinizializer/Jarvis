#!/usr/bin/env python3
"""Transactional per-user JARVIS installer. Never deletes credentials or user data."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import venv
from datetime import datetime
from pathlib import Path
from app_config import atomic_write

ROOT = Path(__file__).resolve().parent
ICON_SIZES=(16,22,24,32,48,64,128,256)
APP_FILES = (
    'assets/jarvis-icon-v2.png', 'INSTALLATION.md',
    *(f'assets/icons/jarvis-{size}.png' for size in ICON_SIZES),
    'discord_runtime.py','discord_ui.py','requirements-discord.txt',
    'session_idle.py', 'microphone_feed.py', 'satellite_client.py', 'satellite_core.py',
    'voice_commands.py',
    'speech_output.py', 'vector_scene.py', 'desktop_visibility.py', 'floating_ui.py', 'requirements-desktop.txt',
    'task_io.py', 'provider_context.py', 'web_scraper.py', 'api_key_pool.py',
    'jarvis_v10.py', 'jarvis_app.py', 'jarvis_hud.py', 'provider_fallback.py', 'chat_store.py', 'chat_view.py', 'app_config.py', 'commands_module.py', 'mcp_client.py', 'agent_runtime.py',
    'installer.py', 'project_audit.py', 'jarvis_memory_engine.py', 'persona_module.py',
    'language_module.py', 'translator_module.py', 'command_policy.py', 'utils_module.py',
    'audio_checks.py', 'voice_ui.py', 'voice_session.py', 'vision_ui.py', 'vision_runtime.py', 'requirements-vision.txt',
    'voice_module.py', 'ssh_module.py', 'pin_auth.py', 'user_registry.py', 'jarvis_secrets.py',
    'web_module.py', 'site_analyzer.py', 'api_module.py', 'agent_module.py', 'blender_module.py',
    'email_module.py', 'discord_module.py', 'discord_voice_module.py', 'sepformer_server.py',
    'install.sh', 'install_from_code.sh', 'reinstall.sh', 'avvia_jarvis.sh',
    'jarvis.desktop', 'assets/jarvis.svg', 'README.md', 'requirements-voice.txt',
)
REQUIRED = ('discord_runtime.py','discord_ui.py','requirements-discord.txt','session_idle.py', 'microphone_feed.py', 'satellite_client.py', 'satellite_core.py', 'voice_commands.py', 'speech_output.py', 'vector_scene.py', 'desktop_visibility.py', 'floating_ui.py', 'requirements-desktop.txt', 'audio_checks.py', 'voice_ui.py', 'voice_session.py', 'vision_ui.py', 'vision_runtime.py', 'task_io.py', 'provider_context.py', 'web_scraper.py', 'api_key_pool.py', 'jarvis_v10.py', 'jarvis_app.py', 'jarvis_hud.py', 'provider_fallback.py', 'chat_store.py', 'chat_view.py', 'app_config.py', 'commands_module.py', 'agent_runtime.py',
            'mcp_client.py', 'installer.py', 'project_audit.py', 'skills/skill_base.py')


def downloads_source():
    for path in (Path.home() / 'Scaricati/code', Path.home() / 'Downloads/code'):
        if path.is_dir():
            return path
    raise FileNotFoundError('Progetto non trovato in Scaricati/code o Downloads/code. Usa --source.')


def managed_files(source):
    paths = [Path(name) for name in APP_FILES if (source / name).is_file()]
    paths += [p.relative_to(source) for p in (source / 'skills').rglob('*')
              if p.is_file() and p.suffix in ('.py', '.md') and '__pycache__' not in p.parts]
    for relative in paths:
        path = source / relative
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError('Collegamento simbolico non ammesso nel pacchetto: ' + str(relative))
    missing = set(REQUIRED) - {str(p) for p in paths}
    if missing:
        raise ValueError('Pacchetto incompleto: ' + ', '.join(sorted(missing)))
    for relative in paths:
        if relative.suffix == '.py':
            compile((source / relative).read_text(encoding='utf-8'), str(relative), 'exec')
    return sorted(set(paths))


def install_desktop(destination, home=None):
    home = Path(home) if home else Path.home()
    local_bin = home / '.local/bin'
    data = Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share')))
    applications = data / 'applications'
    generated=destination/'assets/jarvis-icon-v2.png'
    icon = data / ('icons/hicolor/512x512/apps/jarvis.png' if generated.is_file() else 'icons/hicolor/scalable/apps/jarvis.svg')
    icons={icon:generated if generated.is_file() else destination/'assets/jarvis.svg'}
    obsolete=[]
    if generated.is_file():
        for size in ICON_SIZES:
            source=destination/f'assets/icons/jarvis-{size}.png'
            icons[data/f'icons/hicolor/{size}x{size}/apps/jarvis.png']=source if source.is_file() else generated
        # A stale SVG can override the new raster icon at sizes requested by KDE.
        obsolete=[data/'icons/hicolor/scalable/apps/jarvis.svg']
    shortcut=applications/'net.local.jarvis.desktop'
    shortcuts=[shortcut] if shortcut.is_file() else []
    desktop_backup = destination / 'backups' / ('desktop-' + uuid.uuid4().hex[:8])
    desktop_backup.mkdir(parents=True)
    targets = (local_bin / 'jarvis', applications / 'jarvis.desktop', *icons, *obsolete,*shortcuts)
    originals = {}
    for index, target in enumerate(targets):
        if target.is_file():
            saved = desktop_backup / str(index)
            shutil.copy2(target, saved)
            originals[target] = saved
        else:
            originals[target] = None
    try:
        for target,source in icons.items():
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
        for target in obsolete:
            if target.is_file():target.unlink()
        import shlex
        launcher = local_bin / 'jarvis'
        atomic_write(launcher, '#!/usr/bin/env bash\nexec ' + shlex.quote(str(destination / 'avvia_jarvis.sh')) + ' "$@"\n', 0o755)
        # Desktop Exec uses double-quote escaping, not shell single-quote escaping.
        def quote(path):
            return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
        desktop = ('[Desktop Entry]\nVersion=1.0\nType=Application\nName=JARVIS\n'
            'Comment=Assistente AI personale\nExec=' + quote(launcher) + '\nIcon=' + str(icon) +
            '\nTerminal=false\nStartupNotify=true\nStartupWMClass=jarvis\nCategories=Utility;Science;\n')
        atomic_write(applications / 'jarvis.desktop', desktop, 0o644)
        for target in shortcuts:
            lines=target.read_text(encoding='utf-8').splitlines()
            lines=[line for line in lines if not line.startswith('Icon=')]
            index=lines.index('[Desktop Entry]')+1
            lines.insert(index,'Icon='+str(icon))
            atomic_write(target,'\n'.join(lines)+'\n',0o644)
    except Exception:
        for target, saved in originals.items():
            if saved:
                shutil.copy2(saved, target)
            elif target.is_file():
                target.unlink()
        raise
    return applications / 'jarvis.desktop'


def install_project(mode='local', source=None, destination=None, desktop=True, voice=False, dry_run=False, *, vision=False, desktop_effects=False):
    if mode not in ('local', 'import', 'reinstall'):
        raise ValueError('Modalità installer non valida')
    destination = Path(destination or ROOT).expanduser().absolute()
    if destination.is_symlink() or destination == Path.home() or destination == Path('/'):
        raise ValueError('Destinazione non sicura')
    snapshot = destination / '.install-source'
    if source:
        source = Path(source).expanduser().resolve()
    elif mode == 'import':
        source = downloads_source().resolve()
    elif mode == 'reinstall' and snapshot.is_dir():
        source = snapshot
    else:
        source = ROOT
    files = managed_files(source)
    plan = {'mode': mode, 'source': str(source), 'destination': str(destination),
            'managed_files': len(files), 'desktop': desktop, 'voice': voice, 'vision': vision,
            'preserved': ['API keys (.env/.env.enc)', 'configurazione', 'utenti', 'memorie', 'skill personali']}
    if dry_run:
        return plan
    destination.mkdir(parents=True, exist_ok=True)
    # mkdir is an exclusive lock that works across processes and threads.
    lock = destination / '.install-lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError('Un’altra installazione è in corso (.install-lock)') from None
    token = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    backup = destination / 'backups' / ('install-' + token)
    runtime = destination / '.runtime' / token
    stage = None
    replaced = []
    old_env = False
    snapshot_moved = snapshot_installed = False
    fresh_snapshot = None
    manifest_path = destination / '.install-manifest.json'
    try:
        backup.mkdir(parents=True)
        if manifest_path.exists():
            shutil.copy2(manifest_path, backup / 'manifest.json')
        stage = Path(tempfile.mkdtemp(prefix='.install-stage-', dir=destination))
        for relative in files:
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
        # Keep a known source for future reinstall; the environment uses its final path.
        venv.EnvBuilder(with_pip=voice or vision or desktop_effects).create(runtime)
        if voice or vision or desktop_effects:
            subprocess.run([str(runtime / 'bin/python'), '-m', 'pip', 'install', '-r', str(stage / 'requirements-desktop.txt')], check=True)
            subprocess.run([str(runtime / 'bin/python'), '-m', 'pip', 'install', '-r', str(stage / 'requirements-discord.txt')], check=True)
        if voice:
            subprocess.run([str(runtime / 'bin/python'), '-m', 'pip', 'install', '-r', str(stage / 'requirements-voice.txt')], check=True)
        if vision:
            subprocess.run([str(runtime / 'bin/python'), '-m', 'pip', 'install', '-r', str(stage / 'requirements-vision.txt')], check=True)
            environment = {**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': str(runtime / 'browsers')}
            subprocess.run([str(runtime / 'bin/python'), '-m', 'playwright', 'install', 'chromium'], env=environment, check=True)
        subprocess.run([str(runtime / 'bin/python'), '-c', 'import tkinter; import sqlite3'], check=True)
        previous_path = destination / '.install-manifest.json'
        previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
        previous_names = previous.get('files', {})
        # Never trust a manifest to delete arbitrary files or secrets.
        allowed = set(APP_FILES) | {str(p) for p in files}
        def is_managed(name):
            relative = Path(name)
            return (name in allowed or (len(relative.parts) >= 2 and relative.parts[0] == 'skills'
                    and '..' not in relative.parts and relative.suffix in ('.py', '.md')))
        stale = [Path(name) for name in previous_names if is_managed(name) and name not in {str(p) for p in files}]
        for relative in [*files, *stale]:
            target = destination / relative
            if target.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Destinazione file non sicura: ' + str(relative))
            saved = backup / relative
            if target.exists():
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            replaced.append((relative, target.exists()))
            if relative in stale:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage / relative, target)
        env_path = destination / 'venv'
        if env_path.exists() or env_path.is_symlink():
            os.replace(env_path, backup / 'venv')
            old_env = True
        os.symlink(runtime, env_path, target_is_directory=True)
        for name in ('install.sh', 'install_from_code.sh', 'reinstall.sh', 'avvia_jarvis.sh'):
            if (destination / name).exists():
                (destination / name).chmod(0o755)
        # Stage snapshot independently; never include .env, profiles, runtime or backup trees.
        fresh_snapshot = destination / ('.source-' + token)
        fresh_snapshot.mkdir()
        for relative in files:
            target = fresh_snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination / relative, target)
        if snapshot.exists():
            os.replace(snapshot, backup / 'source')
            snapshot_moved = True
        os.replace(fresh_snapshot, snapshot)
        snapshot_installed = True
        manifest = {'files': {str(p): hashlib.sha256((destination / p).read_bytes()).hexdigest() for p in files}}
        atomic_write(previous_path, json.dumps(manifest, indent=2) + '\n')
        if desktop:
            install_desktop(destination)
        # Existing secrets remain byte-for-byte in place; downloaded secrets are never imported.
        plan.update(backup=str(backup), result='Installazione completata. Credenziali e dati conservati.')
        return plan
    except Exception:
        if snapshot_installed and snapshot.exists():
            shutil.rmtree(snapshot)
        if snapshot_moved:
            os.replace(backup / 'source', snapshot)
        if (backup / 'manifest.json').exists():
            shutil.copy2(backup / 'manifest.json', manifest_path)
        elif replaced:
            manifest_path.unlink(missing_ok=True)
        for relative, existed in reversed(replaced):
            target = destination / relative
            if existed:
                shutil.copy2(backup / relative, target)
            else:
                target.unlink(missing_ok=True)
        env_path = destination / 'venv'
        if env_path.is_symlink() and env_path.resolve() == runtime.resolve():
            env_path.unlink()
        if old_env:
            os.replace(backup / 'venv', env_path)
        # Keep failed build logs / environment in backup for inspection.
        if runtime.exists():
            os.replace(runtime, backup / 'failed-runtime')
        raise
    finally:
        if stage and stage.exists():
            shutil.rmtree(stage)
        if fresh_snapshot and fresh_snapshot.exists():
            shutil.rmtree(fresh_snapshot)
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['local', 'import', 'reinstall'], default='local')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path, default=ROOT)
    parser.add_argument('--no-desktop', action='store_true')
    parser.add_argument('--vision', action='store_true', help='Installa browser indipendente e strumenti visivi (richiede rete)')
    parser.add_argument('--voice', action='store_true', help='Installa anche le dipendenze vocali (richiede rete)')
    parser.add_argument('--desktop-effects', action='store_true', help='Installa rendering vettoriale e osservatore fullscreen KDE (richiede rete)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        result = install_project(args.mode, args.source, args.destination, not args.no_desktop, args.voice, args.dry_run, vision=args.vision, desktop_effects=args.desktop_effects)
    except Exception as exc:
        parser.exit(1, 'Installazione non completata: ' + str(exc) + '\n')
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
