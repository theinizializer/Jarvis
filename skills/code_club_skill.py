"""
skills/code_club_skill.py — JARVIS v10
Skill per Code Club Luxembourg — aiuto studenti, pianificazione lezioni, team.
"""
from __future__ import annotations
from skills.skill_base import JarvisSkill


class CodeClubSkill(JarvisSkill):
    name        = "Code Club"
    description = "Assistente per le attività di Code Club Luxembourg: lezioni, studenti, team"
    version     = "1.0"
    author      = "Radostin"
    icon        = "🏫"

    system_prompt_addon = """
Sei anche assistente per Code Club Luxembourg.
Radostin insegna programmazione e gestisce un team di 4 studenti.
Quando parla di 'la classe', 'gli studenti', 'il team' o 'Code Club':
- Aiuta a pianificare lezioni di programmazione adatte a ragazzi
- Suggerisci esercizi pratici su Python, Scratch, HTML/CSS
- Aiuta a strutturare spiegazioni chiare per principianti
- Se chiede di valutare codice degli studenti, sii incoraggiante e costruttivo
- Ricorda che il contratto è stato firmato all'inizio del 2026
"""

    def get_config_schema(self) -> dict:
        return {
            "livello_studenti": {
                "type":    "select",
                "label":   "Livello medio degli studenti",
                "options": ["principiante", "intermedio", "avanzato"],
                "default": "principiante",
            },
            "linguaggi": {
                "type":    "text",
                "label":   "Linguaggi insegnati (separati da virgola)",
                "default": "Python, Scratch, HTML",
            },
        }
