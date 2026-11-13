"""
skills/linux_skill.py — JARVIS v10
Skill per amministrazione sistema Linux (Arch/CachyOS, KDE Plasma, Jellyfin).
"""
from __future__ import annotations
import subprocess
from skills.skill_base import JarvisSkill


class LinuxSkill(JarvisSkill):
    name        = "Linux Sysadmin"
    description = "Comandi, troubleshooting e ottimizzazione per Arch Linux / CachyOS con KDE Plasma"
    version     = "1.0"
    author      = "Radostin"
    icon        = "🐧"

    system_prompt_addon = """
Il sistema operativo di Radostin è Arch Linux (CachyOS) con KDE Plasma.
Quando chiede aiuto su Linux:
- Usa pacman/yay per la gestione pacchetti (NON apt o dnf)
- Per AUR usa yay o paru
- I comandi systemd sono la norma (systemctl, journalctl)
- Jellyfin è installato come media server locale
- Preferisce soluzioni da terminale, è esperto
- Se proponi un comando pericoloso (rm -rf, format, ecc), avvisa esplicitamente
"""

    def on_load(self) -> None:
        """Rileva info sul sistema al caricamento."""
        try:
            result = subprocess.run(
                ["uname", "-r"], capture_output=True, text=True, timeout=3
            )
            self._kernel = result.stdout.strip()
        except Exception:
            self._kernel = "sconosciuto"

    def get_config_schema(self) -> dict:
        return {
            "shell": {
                "type":    "select",
                "label":   "Shell preferita",
                "options": ["bash", "zsh", "fish"],
                "default": "bash",
            },
            "aur_helper": {
                "type":    "select",
                "label":   "AUR helper",
                "options": ["yay", "paru", "trizen"],
                "default": "yay",
            },
        }
