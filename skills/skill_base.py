"""
skills/skill_base.py — JARVIS v10
Classe base per tutte le skill e sistema di caricamento dinamico.

Come creare una nuova skill:
  1. Crea un file in skills/  es: skills/meteo_skill.py
  2. Definisci una classe che eredita da JarvisSkill
  3. Implementa i metodi obbligatori (vedi JarvisSkill)
  4. La skill appare automaticamente nel pannello impostazioni

Esempio minimale:
    from skills.skill_base import JarvisSkill

    class MeteoSkill(JarvisSkill):
        name        = "Meteo"
        description = "Fornisce previsioni meteo in tempo reale"
        version     = "1.0"
        author      = "Radostin"
        icon        = "🌤"

        system_prompt_addon = (
            "Quando l'utente chiede del meteo, usa WebModule.search() "
            "con la query 'meteo <città>' e presenta i dati in modo chiaro."
        )

        def on_load(self) -> None:
            pass  # setup opzionale

        def on_unload(self) -> None:
            pass  # cleanup opzionale
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Classe base
# ---------------------------------------------------------------------------

class JarvisSkill:
    """
    Classe base per tutte le skill di JARVIS v10.
    Ereditala e definisci i campi di classe + i metodi opzionali.
    """

    # --- Campi obbligatori (da sovrascrivere nella sottoclasse) ---
    name: str        = "Skill senza nome"
    description: str = "Nessuna descrizione"
    version: str     = "1.0"
    author: str      = "Sconosciuto"
    icon: str        = "🔧"

    # --- Istruzioni aggiuntive per il system prompt di Ollama ---
    # Vengono accodate al system prompt principale quando la skill è attiva.
    system_prompt_addon: str = ""

    # --- Dipendenze Python (verranno installate automaticamente se mancanti) ---
    dependencies: list[str] = []

    # --- Stato runtime (gestito da SkillLoader) ---
    enabled: bool = False
    _instance_config: dict = {}

    def __init__(self, config: dict | None = None):
        self._instance_config = config or {}

    # ------------------------------------------------------------------
    # Metodi opzionali da sovrascrivere
    # ------------------------------------------------------------------

    def on_load(self) -> None:
        """Chiamato quando la skill viene abilitata. Setup, autenticazioni, ecc."""
        pass

    def on_unload(self) -> None:
        """Chiamato quando la skill viene disabilitata. Cleanup risorse."""
        pass

    def on_user_message(self, text: str) -> Optional[str]:
        """
        Chiamato prima che JARVIS processi ogni messaggio utente.
        Può restituire una stringa per intercettare la risposta,
        oppure None per lasciare che JARVIS continui normalmente.
        """
        return None

    def get_config_schema(self) -> dict:
        """
        Ritorna lo schema di configurazione della skill per il pannello impostazioni.
        Formato: {campo: {type, label, default, required}}

        Esempio:
            return {
                "api_key": {
                    "type":     "password",
                    "label":    "API Key OpenWeather",
                    "default":  "",
                    "required": True,
                },
                "unita": {
                    "type":    "select",
                    "label":   "Unità temperatura",
                    "options": ["celsius", "fahrenheit"],
                    "default": "celsius",
                },
            }
        """
        return {}

    # ------------------------------------------------------------------
    # Metodi utili (non sovrascrivere)
    # ------------------------------------------------------------------

    def get_config(self, key: str, default: Any = None) -> Any:
        """Legge un valore dalla configurazione della skill."""
        return self._instance_config.get(key, default)

    def __repr__(self) -> str:
        status = "✓" if self.enabled else "○"
        return f"[{status}] {self.icon} {self.name} v{self.version}"


# ---------------------------------------------------------------------------
# SkillLoader — carica, abilita e gestisce le skill
# ---------------------------------------------------------------------------

class SkillLoader:
    """Discover Python metadata without importing disabled skills; one config owner."""
    SKILLS_DIR = Path(__file__).parent

    def __init__(self, settings=None):
        from app_config import Settings
        self.settings = settings or Settings()
        self._registry = {}
        self._instances = {}
        self.errors = {}
        self._discover()

    def _discover(self):
        import ast
        from app_config import data_dir
        directories = (self.SKILLS_DIR, data_dir() / 'skills')
        for directory in directories:
            for path in sorted(directory.glob('*_skill.py')):
                try:
                    tree = ast.parse(path.read_text(encoding='utf-8'))
                    for node in tree.body:
                        if not isinstance(node, ast.ClassDef):
                            continue
                        if not any(isinstance(base, ast.Name) and base.id == 'JarvisSkill' for base in node.bases):
                            continue
                        info = {'path': path, 'class': node.name, 'kind': 'python'}
                        for item in node.body:
                            if isinstance(item, ast.Assign):
                                for target in item.targets:
                                    if isinstance(target, ast.Name) and target.id in ('name', 'description', 'version', 'icon'):
                                        info[target.id] = ast.literal_eval(item.value)
                        name = info.get('name', path.stem)
                        if name in self._registry:
                            raise ValueError('Nome skill duplicato: ' + name)
                        self._registry[name] = info
                except Exception as exc:
                    self.errors[path.name] = str(exc)
            for path in sorted(directory.glob('*/SKILL.md')):
                name = path.parent.name
                if name not in self._registry:
                    self._registry[name] = {'name': name, 'path': path, 'kind': 'markdown',
                        'description': 'Istruzioni personali', 'version': '1.0', 'icon': '✦'}

    def _instantiate(self, info, config):
        if info['kind'] == 'markdown':
            instance = JarvisSkill(config)
            instance.name = info['name']
            instance.system_prompt_addon = info['path'].read_text(encoding='utf-8')
            return instance
        module_name = 'jarvis_skill_' + info['path'].stem
        spec = importlib.util.spec_from_file_location(module_name, info['path'])
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return getattr(module, info['class'])(config=config)

    def enable(self, name, skill_config=None):
        if name not in self._registry:
            return False
        config = skill_config if skill_config is not None else self.settings.values['skills'].get(name, {}).get('config', {})
        instance = self._instantiate(self._registry[name], config)
        for dep in instance.dependencies:
            if importlib.util.find_spec(dep) is None:
                raise RuntimeError('Dipendenza skill mancante: ' + dep + '. Installala nell’ambiente JARVIS.')
        instance.on_load()
        if name in self._instances:
            self._instances[name].on_unload()
        instance.enabled = True
        self._instances[name] = instance
        self.settings.values['skills'][name] = {'enabled': True, 'config': config}
        self.settings.save()
        return True

    def disable(self, name):
        instance = self._instances.pop(name, None)
        if instance:
            instance.on_unload()
        if name not in self._registry:
            return False
        self.settings.values['skills'].setdefault(name, {})['enabled'] = False
        self.settings.save()
        return True

    def _activate_saved(self):
        for name, info in self._registry.items():
            if name not in self._instances and self.settings.values['skills'].get(name, {}).get('enabled'):
                try:
                    self.enable(name)
                except Exception as exc:
                    self.errors[name] = str(exc)

    def list_all(self):
        result = []
        for name, info in sorted(self._registry.items()):
            saved = self.settings.values['skills'].get(name, {})
            result.append({k: v for k, v in {**info, 'name': name,
                'enabled': saved.get('enabled', False), 'config': saved.get('config', {}),
                'loaded': name in self._instances, 'error': self.errors.get(name, '')}.items()
                if k not in ('path', 'class')})
        return result

    def config_schema(self, name):
        return self._instantiate(self._registry[name], {}).get_config_schema()

    def list_enabled(self):
        self._activate_saved()
        return list(self._instances.values())

    def is_enabled(self, name):
        return self.settings.values['skills'].get(name, {}).get('enabled', False)

    def dispatch_message(self, text):
        for instance in self.list_enabled():
            try:
                response = instance.on_user_message(text)
                if response is not None:
                    return response
            except Exception as exc:
                self.errors[instance.name] = str(exc)
        return None

    def get_system_prompt_addons(self):
        return '\n\n'.join(s.system_prompt_addon for s in self.list_enabled() if s.system_prompt_addon)

    def install_markdown(self, source):
        import shutil
        import re
        from app_config import data_dir
        source = Path(source)
        if source.name != 'SKILL.md':
            raise ValueError('Seleziona un file SKILL.md')
        name = re.sub(r'[^A-Za-z0-9_-]', '_', source.parent.name)
        if name in self._registry:
            raise ValueError('Esiste già una skill con questo nome')
        destination = data_dir() / 'skills' / name
        destination.mkdir(parents=True, exist_ok=False)
        shutil.copy2(source, destination / 'SKILL.md')
        self._registry.clear()
        self._discover()
        return name

    def reload(self):
        for instance in self._instances.values():
            instance.on_unload()
        self._instances.clear()
        self._registry.clear()
        self.errors.clear()
        self._discover()

    def close(self):
        for instance in self._instances.values():
            instance.on_unload()
        self._instances.clear()

    def __len__(self):
        return len(self._registry)
