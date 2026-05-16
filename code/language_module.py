#!/usr/bin/env python3
"""
JARVIS — Language Module
Localizzazione completa: wake word, sleep word, comandi slash,
messaggi di sistema, istruzioni modello — tutto nella lingua scelta.
"""

import json
import re
from pathlib import Path
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
# LINGUE SUPPORTATE
# ══════════════════════════════════════════════════════════════════════════════

ALL_LANGUAGES = {
    "it": "Italiano",
    "fr": "Français",
    "en": "English",
    "de": "Deutsch",
    "es": "Español",
    "pt": "Português",
    "zh": "中文",
    "ja": "日本語",
    "ko": "한국어",
    "ar": "العربية",
    "ru": "Русский",
    "nl": "Nederlands",
    "pl": "Polski",
    "tr": "Türkçe",
    "sv": "Svenska",
    "lb": "Lëtzebuergesch",
}

# ══════════════════════════════════════════════════════════════════════════════
# LOCALIZZAZIONE COMPLETA PER LINGUA
# ══════════════════════════════════════════════════════════════════════════════
# Ogni lingua ha:
#   tts       — codice gTTS
#   wake      — varianti wake word (Whisper può storpiarle)
#   sleep     — frasi sleep word
#   system    — istruzione al modello per rispondere in questa lingua
#   ui        — messaggi UI localizzati
#   commands  — comandi slash localizzati
#   model_instructions — istruzioni complete per il Modelfile

LANG_DATA = {

    "it": {
        "tts": "it",
        "wake": ["jarvis", "jarvi", "giorvis", "giervi", "gervis"],
        "sleep": ["jarvis dormi", "jarvis riposa", "dormi jarvis"],
        "system": "RISPONDI SEMPRE E SOLO IN ITALIANO. Mai in altre lingue, mai in cinese.",
        "ui": {
            "standby":       "😴 In standby — dì 'Jarvis' per attivare...",
            "awake":         "☀️  Sveglio!",
            "sleeping":      "💤 Vado in standby.",
            "session_open":  "✅ Wake word! Sessione aperta per {min} min",
            "speak":         "🎙️  Parla...",
            "transcribed":   "🎙️  Trascritto: «{text}»",
            "not_owner":     "🔒 Voce non riconosciuta ({score:.2f})",
            "ready":         "✅ JARVIS PRONTO",
            "goodbye":       "👋 Ciao!",
        },
        "commands": {
            "/lingua":        "/lingua",
            "/lingue":        "/lingue",
            "/cambia":        "/cambia lingua",
            "/aggiungi":      "/aggiungi lingua",
            "/rimuovi":       "/rimuovi lingua",
            "/tutte":         "/tutte le lingue",
            "/memorizza":     "/memorizza",
            "/memoria":       "/memoria",
            "/dimentica":     "/dimentica tutto",
            "/stats":         "/stats",
            "/tts":           "/tts",
            "/aiuto":         "/aiuto",
        },
        "responses": {
            "current_lang":   "🌍 Lingua corrente: **{name}**",
            "lang_changed":   "✅ Lingua cambiata in: **{name}**",
            "lang_added":     "✅ {name} aggiunta.",
            "lang_removed":   "✅ {name} rimossa.",
            "lang_exists":    "⚠️  {name} è già nella lista.",
            "lang_not_found": "❌ Lingua non trovata: '{query}'",
            "one_lang_only":  "Hai solo una lingua. Usa /aggiungi lingua per aggiungerne altre.",
            "choose_lang":    "🌍 Scegli lingua (digita il numero):",
            "invalid":        "❌ Scelta non valida.",
            "memorized":      "💾 Memorizzato: '{fact}'",
            "memory_empty":   "📭 Nessuna memoria.",
            "memory_cleared": "🗑️  Memoria cancellata.",
            "help": (
                "📋 **Comandi JARVIS:**\n"
                "  /lingua — lingua corrente\n"
                "  /cambia lingua — cambia lingua\n"
                "  /aggiungi lingua — aggiungi lingua\n"
                "  /memorizza [fatto] — memorizza info\n"
                "  /memoria — mostra memoria\n"
                "  /dimentica tutto — cancella memoria\n"
                "  /stats — statistiche\n"
                "  /tts — attiva/disattiva voce\n"
                "  /aiuto — questo messaggio"
            ),
        },
    },

    "fr": {
        "tts": "fr",
        "wake": ["jarvis", "jarvi", "djarvis", "gervis", "jarvi"],
        "sleep": ["jarvis dors", "jarvis repos", "dors jarvis"],
        "system": "RÉPONDS TOUJOURS ET UNIQUEMENT EN FRANÇAIS. Jamais en chinois ou autre langue.",
        "ui": {
            "standby":       "😴 En veille — dis 'Jarvis' pour activer...",
            "awake":         "☀️  Réveillé !",
            "sleeping":      "💤 Je vais en veille.",
            "session_open":  "✅ Mot de réveil ! Session ouverte pour {min} min",
            "speak":         "🎙️  Parlez...",
            "transcribed":   "🎙️  Transcrit : «{text}»",
            "not_owner":     "🔒 Voix non reconnue ({score:.2f})",
            "ready":         "✅ JARVIS PRÊT",
            "goodbye":       "👋 Au revoir !",
        },
        "commands": {
            "/langue":        "/langue",
            "/langues":       "/langues",
            "/changer":       "/changer langue",
            "/ajouter":       "/ajouter langue",
            "/supprimer":     "/supprimer langue",
            "/toutes":        "/toutes les langues",
            "/mémoriser":     "/mémoriser",
            "/mémoire":       "/mémoire",
            "/oublier":       "/oublier tout",
            "/stats":         "/stats",
            "/tts":           "/tts",
            "/aide":          "/aide",
        },
        "responses": {
            "current_lang":   "🌍 Langue actuelle : **{name}**",
            "lang_changed":   "✅ Langue changée : **{name}**",
            "lang_added":     "✅ {name} ajoutée.",
            "lang_removed":   "✅ {name} supprimée.",
            "lang_exists":    "⚠️  {name} est déjà dans la liste.",
            "lang_not_found": "❌ Langue non trouvée : '{query}'",
            "one_lang_only":  "Vous n'avez qu'une langue. Utilisez /ajouter langue pour en ajouter.",
            "choose_lang":    "🌍 Choisissez la langue (tapez le numéro) :",
            "invalid":        "❌ Choix invalide.",
            "memorized":      "💾 Mémorisé : '{fact}'",
            "memory_empty":   "📭 Aucun souvenir.",
            "memory_cleared": "🗑️  Mémoire effacée.",
            "help": (
                "📋 **Commandes JARVIS :**\n"
                "  /langue — langue actuelle\n"
                "  /changer langue — changer de langue\n"
                "  /ajouter langue — ajouter une langue\n"
                "  /mémoriser [fait] — mémoriser une info\n"
                "  /mémoire — afficher la mémoire\n"
                "  /oublier tout — effacer la mémoire\n"
                "  /stats — statistiques\n"
                "  /tts — activer/désactiver la voix\n"
                "  /aide — ce message"
            ),
        },
    },

    "en": {
        "tts": "en",
        "wake": ["jarvis", "jarvi", "gervis", "jarvis"],
        "sleep": ["jarvis sleep", "jarvis rest", "sleep jarvis"],
        "system": "ALWAYS RESPOND IN ENGLISH ONLY. Never in Chinese or any other language.",
        "ui": {
            "standby":       "😴 Standby — say 'Jarvis' to activate...",
            "awake":         "☀️  Awake!",
            "sleeping":      "💤 Going to sleep.",
            "session_open":  "✅ Wake word! Session open for {min} min",
            "speak":         "🎙️  Speak...",
            "transcribed":   "🎙️  Transcribed: «{text}»",
            "not_owner":     "🔒 Voice not recognized ({score:.2f})",
            "ready":         "✅ JARVIS READY",
            "goodbye":       "👋 Goodbye!",
        },
        "commands": {
            "/language":      "/language",
            "/languages":     "/languages",
            "/change":        "/change language",
            "/add":           "/add language",
            "/remove":        "/remove language",
            "/all":           "/all languages",
            "/remember":      "/remember",
            "/memory":        "/memory",
            "/forget":        "/forget all",
            "/stats":         "/stats",
            "/tts":           "/tts",
            "/help":          "/help",
        },
        "responses": {
            "current_lang":   "🌍 Current language: **{name}**",
            "lang_changed":   "✅ Language changed to: **{name}**",
            "lang_added":     "✅ {name} added.",
            "lang_removed":   "✅ {name} removed.",
            "lang_exists":    "⚠️  {name} is already in the list.",
            "lang_not_found": "❌ Language not found: '{query}'",
            "one_lang_only":  "You only have one language. Use /add language to add more.",
            "choose_lang":    "🌍 Choose language (type the number):",
            "invalid":        "❌ Invalid choice.",
            "memorized":      "💾 Memorized: '{fact}'",
            "memory_empty":   "📭 No memories.",
            "memory_cleared": "🗑️  Memory cleared.",
            "help": (
                "📋 **JARVIS Commands:**\n"
                "  /language — current language\n"
                "  /change language — change language\n"
                "  /add language — add a language\n"
                "  /remember [fact] — memorize info\n"
                "  /memory — show memory\n"
                "  /forget all — clear memory\n"
                "  /stats — statistics\n"
                "  /tts — toggle voice\n"
                "  /help — this message"
            ),
        },
    },

    "de": {
        "tts": "de",
        "wake": ["jarvis", "jarvi", "gervis"],
        "sleep": ["jarvis schlaf", "schlaf jarvis", "jarvis ruh"],
        "system": "Antworte IMMER NUR auf Deutsch. Niemals auf Chinesisch oder einer anderen Sprache.",
        "ui": {
            "standby":       "😴 Bereitschaft — sag 'Jarvis' zum Aktivieren...",
            "awake":         "☀️  Wach!",
            "sleeping":      "💤 Ich gehe in den Ruhezustand.",
            "session_open":  "✅ Aktivierungswort! Sitzung für {min} Min geöffnet",
            "speak":         "🎙️  Sprechen Sie...",
            "transcribed":   "🎙️  Transkribiert: «{text}»",
            "not_owner":     "🔒 Stimme nicht erkannt ({score:.2f})",
            "ready":         "✅ JARVIS BEREIT",
            "goodbye":       "👋 Auf Wiedersehen!",
        },
        "commands": {
            "/sprache":       "/sprache",
            "/sprachen":      "/sprachen",
            "/wechseln":      "/sprache wechseln",
            "/hinzufügen":    "/sprache hinzufügen",
            "/entfernen":     "/sprache entfernen",
            "/alle":          "/alle sprachen",
            "/merken":        "/merken",
            "/gedächtnis":    "/gedächtnis",
            "/vergessen":     "/alles vergessen",
            "/stats":         "/stats",
            "/tts":           "/tts",
            "/hilfe":         "/hilfe",
        },
        "responses": {
            "current_lang":   "🌍 Aktuelle Sprache: **{name}**",
            "lang_changed":   "✅ Sprache geändert zu: **{name}**",
            "lang_added":     "✅ {name} hinzugefügt.",
            "lang_removed":   "✅ {name} entfernt.",
            "lang_exists":    "⚠️  {name} ist bereits in der Liste.",
            "lang_not_found": "❌ Sprache nicht gefunden: '{query}'",
            "one_lang_only":  "Sie haben nur eine Sprache. Verwenden Sie /sprache hinzufügen.",
            "choose_lang":    "🌍 Sprache wählen (Nummer eingeben):",
            "invalid":        "❌ Ungültige Auswahl.",
            "memorized":      "💾 Gespeichert: '{fact}'",
            "memory_empty":   "📭 Keine Erinnerungen.",
            "memory_cleared": "🗑️  Gedächtnis gelöscht.",
            "help":           "📋 Hilfe: /hilfe für Befehle",
        },
    },

    "es": {
        "tts": "es",
        "wake": ["jarvis", "jarvi", "hervis", "gervis"],
        "sleep": ["jarvis duerme", "duerme jarvis", "jarvis descansa"],
        "system": "RESPONDE SIEMPRE Y SOLO EN ESPAÑOL. Nunca en chino ni en otros idiomas.",
        "ui": {
            "standby":       "😴 En espera — di 'Jarvis' para activar...",
            "awake":         "☀️  ¡Despierto!",
            "sleeping":      "💤 Entrando en modo espera.",
            "session_open":  "✅ ¡Palabra de activación! Sesión abierta por {min} min",
            "speak":         "🎙️  Habla...",
            "transcribed":   "🎙️  Transcrito: «{text}»",
            "not_owner":     "🔒 Voz no reconocida ({score:.2f})",
            "ready":         "✅ JARVIS LISTO",
            "goodbye":       "👋 ¡Adiós!",
        },
        "commands": {
            "/idioma":        "/idioma",
            "/idiomas":       "/idiomas",
            "/cambiar":       "/cambiar idioma",
            "/añadir":        "/añadir idioma",
            "/eliminar":      "/eliminar idioma",
            "/todos":         "/todos los idiomas",
            "/recordar":      "/recordar",
            "/memoria":       "/memoria",
            "/olvidar":       "/olvidar todo",
            "/stats":         "/stats",
            "/tts":           "/tts",
            "/ayuda":         "/ayuda",
        },
        "responses": {
            "current_lang":   "🌍 Idioma actual: **{name}**",
            "lang_changed":   "✅ Idioma cambiado a: **{name}**",
            "lang_added":     "✅ {name} añadido.",
            "lang_removed":   "✅ {name} eliminado.",
            "lang_exists":    "⚠️  {name} ya está en la lista.",
            "lang_not_found": "❌ Idioma no encontrado: '{query}'",
            "one_lang_only":  "Solo tienes un idioma. Usa /añadir idioma para agregar más.",
            "choose_lang":    "🌍 Elige idioma (escribe el número):",
            "invalid":        "❌ Elección inválida.",
            "memorized":      "💾 Memorizado: '{fact}'",
            "memory_empty":   "📭 Sin recuerdos.",
            "memory_cleared": "🗑️  Memoria borrada.",
            "help":           "📋 Ayuda: /ayuda para comandos",
        },
    },

    "zh": {
        "tts": "zh",
        "wake": ["贾维斯", "jarvis", "贾维"],
        "sleep": ["贾维斯休息", "jarvis休息", "贾维斯睡觉"],
        "system": "始终只用中文回答。绝对不要用其他语言回答。",
        "ui": {
            "standby":       "😴 待机中 — 说'贾维斯'以激活...",
            "awake":         "☀️  已唤醒！",
            "sleeping":      "💤 进入待机模式。",
            "session_open":  "✅ 唤醒词！会话已开启 {min} 分钟",
            "speak":         "🎙️  请说话...",
            "transcribed":   "🎙️  识别：«{text}»",
            "not_owner":     "🔒 声音未识别 ({score:.2f})",
            "ready":         "✅ 贾维斯就绪",
            "goodbye":       "👋 再见！",
        },
        "commands": {
            "/语言":          "/语言",
            "/语言列表":       "/语言列表",
            "/切换语言":       "/切换语言",
            "/添加语言":       "/添加语言",
            "/删除语言":       "/删除语言",
            "/记住":          "/记住",
            "/记忆":          "/记忆",
            "/忘记":          "/忘记一切",
            "/stats":         "/stats",
            "/tts":           "/tts",
            "/帮助":          "/帮助",
        },
        "responses": {
            "current_lang":   "🌍 当前语言：**{name}**",
            "lang_changed":   "✅ 语言已切换至：**{name}**",
            "lang_added":     "✅ {name} 已添加。",
            "lang_removed":   "✅ {name} 已删除。",
            "lang_exists":    "⚠️  {name} 已在列表中。",
            "lang_not_found": "❌ 未找到语言：'{query}'",
            "one_lang_only":  "您只有一种语言。使用 /添加语言 来添加更多。",
            "choose_lang":    "🌍 选择语言（输入数字）：",
            "invalid":        "❌ 无效选择。",
            "memorized":      "💾 已记住：'{fact}'",
            "memory_empty":   "📭 没有记忆。",
            "memory_cleared": "🗑️  记忆已清除。",
            "help":           "📋 帮助：/帮助 查看命令",
        },
    },

    "ja": {
        "tts": "ja",
        "wake": ["ジャービス", "jarvis", "ジャーヴィス"],
        "sleep": ["ジャービス休んで", "jarvis休んで", "ジャービス寝て"],
        "system": "常に日本語だけで答えてください。他の言語は絶対に使わないでください。",
        "ui": {
            "standby":       "😴 待機中 — 「ジャービス」と言って起動...",
            "awake":         "☀️  起動しました！",
            "sleeping":      "💤 スタンバイモードに移行します。",
            "session_open":  "✅ ウェイクワード！{min}分間セッション開始",
            "speak":         "🎙️  お話しください...",
            "transcribed":   "🎙️  認識：«{text}»",
            "not_owner":     "🔒 声が認識されませんでした ({score:.2f})",
            "ready":         "✅ ジャービス準備完了",
            "goodbye":       "👋 さようなら！",
        },
        "commands": {
            "/言語":          "/言語",
            "/言語一覧":       "/言語一覧",
            "/言語変更":       "/言語変更",
            "/言語追加":       "/言語追加",
            "/言語削除":       "/言語削除",
            "/記憶":          "/記憶",
            "/忘れる":         "/すべて忘れる",
            "/stats":         "/stats",
            "/tts":           "/tts",
            "/ヘルプ":         "/ヘルプ",
        },
        "responses": {
            "current_lang":   "🌍 現在の言語：**{name}**",
            "lang_changed":   "✅ 言語を変更しました：**{name}**",
            "lang_added":     "✅ {name} を追加しました。",
            "lang_removed":   "✅ {name} を削除しました。",
            "lang_exists":    "⚠️  {name} はすでにリストにあります。",
            "lang_not_found": "❌ 言語が見つかりません：'{query}'",
            "one_lang_only":  "言語が1つしかありません。/言語追加 で追加してください。",
            "choose_lang":    "🌍 言語を選択してください（番号を入力）：",
            "invalid":        "❌ 無効な選択です。",
            "memorized":      "💾 記憶しました：'{fact}'",
            "memory_empty":   "📭 記憶がありません。",
            "memory_cleared": "🗑️  記憶を消去しました。",
            "help":           "📋 ヘルプ：/ヘルプ でコマンド表示",
        },
    },

    "ru": {
        "tts": "ru",
        "wake": ["джарвис", "jarvis", "жарвис"],
        "sleep": ["джарвис спи", "jarvis спи", "джарвис отдыхай"],
        "system": "Всегда отвечай ТОЛЬКО на русском языке. Никогда не используй другие языки.",
        "ui": {
            "standby":       "😴 Ожидание — скажи 'Джарвис' для активации...",
            "awake":         "☀️  Активирован!",
            "sleeping":      "💤 Перехожу в режим ожидания.",
            "session_open":  "✅ Ключевое слово! Сессия открыта на {min} мин",
            "speak":         "🎙️  Говорите...",
            "transcribed":   "🎙️  Распознано: «{text}»",
            "not_owner":     "🔒 Голос не распознан ({score:.2f})",
            "ready":         "✅ ДЖАРВИС ГОТОВ",
            "goodbye":       "👋 До свидания!",
        },
        "commands": {
            "/язык":          "/язык",
            "/языки":         "/языки",
            "/сменить":       "/сменить язык",
            "/добавить":      "/добавить язык",
            "/удалить":       "/удалить язык",
            "/запомни":       "/запомни",
            "/память":        "/память",
            "/забудь":        "/забудь всё",
            "/stats":         "/stats",
            "/tts":           "/tts",
            "/помощь":        "/помощь",
        },
        "responses": {
            "current_lang":   "🌍 Текущий язык: **{name}**",
            "lang_changed":   "✅ Язык изменён на: **{name}**",
            "lang_added":     "✅ {name} добавлен.",
            "lang_removed":   "✅ {name} удалён.",
            "lang_exists":    "⚠️  {name} уже в списке.",
            "lang_not_found": "❌ Язык не найден: '{query}'",
            "one_lang_only":  "У вас только один язык. Используйте /добавить язык.",
            "choose_lang":    "🌍 Выберите язык (введите номер):",
            "invalid":        "❌ Неверный выбор.",
            "memorized":      "💾 Запомнено: '{fact}'",
            "memory_empty":   "📭 Нет воспоминаний.",
            "memory_cleared": "🗑️  Память очищена.",
            "help":           "📋 Помощь: /помощь для команд",
        },
    },

    "lb": {
        "tts": "fr",  # Lëtzebuergesch usa TTS francese come fallback
        "wake": ["jarvis", "jarvi", "djarvis"],
        "sleep": ["jarvis schlof", "schlof jarvis", "jarvis ro"],
        "system": "Äntwert ëmmer NUR op Lëtzebuergesch. Ni op Chinesesch oder aner Sproochen.",
        "ui": {
            "standby":       "😴 Am Standby — soe 'Jarvis' fir z'aktivéieren...",
            "awake":         "☀️  Waakreg!",
            "sleeping":      "💤 Ech ginn an de Standby.",
            "session_open":  "✅ Wake Word! Sëtzung opgemaach fir {min} Min",
            "speak":         "🎙️  Schwätz...",
            "transcribed":   "🎙️  Transkribéiert: «{text}»",
            "not_owner":     "🔒 Stëmm net erkannt ({score:.2f})",
            "ready":         "✅ JARVIS PRETT",
            "goodbye":       "👋 Äddi!",
        },
        "commands": {
            "/sprooch":       "/sprooch",
            "/sproochen":     "/sproochen",
            "/wiesselen":     "/sprooch wiesselen",
            "/derbäisetzen":  "/sprooch derbäisetzen",
            "/ewechhuelen":   "/sprooch ewechhuelen",
            "/onthalen":      "/onthalen",
            "/erënnerung":    "/erënnerung",
            "/vergiessen":    "/alles vergiessen",
            "/stats":         "/stats",
            "/tts":           "/tts",
            "/hëllef":        "/hëllef",
        },
        "responses": {
            "current_lang":   "🌍 Aktuell Sprooch: **{name}**",
            "lang_changed":   "✅ Sprooch gewiesselt op: **{name}**",
            "lang_added":     "✅ {name} derbäigesat.",
            "lang_removed":   "✅ {name} ewechgeholl.",
            "lang_exists":    "⚠️  {name} ass scho an der Lëscht.",
            "lang_not_found": "❌ Sprooch net fonnt: '{query}'",
            "one_lang_only":  "Dir hutt nëmmen eng Sprooch. Benotzt /sprooch derbäisetzen.",
            "choose_lang":    "🌍 Wielt Sprooch (Nummer aginn):",
            "invalid":        "❌ Ongëlteg Wiel.",
            "memorized":      "💾 Onthalen: '{fact}'",
            "memory_empty":   "📭 Keng Erënnerungen.",
            "memory_cleared": "🗑️  Erënnerung geläscht.",
            "help":           "📋 Hëllef: /hëllef fir Kommandoen",
        },
    },

    # Fallback per lingue senza dati completi
    "pt": {
        "tts": "pt",
        "wake": ["jarvis", "jarvi"],
        "sleep": ["jarvis dorme", "dorme jarvis"],
        "system": "Responde SEMPRE E APENAS em português. Nunca em chinês ou outras línguas.",
        "ui": {"standby": "😴 Em espera — diz 'Jarvis'...", "awake": "☀️  Acordado!", "sleeping": "💤 A dormir.", "session_open": "✅ Sessão aberta {min} min", "speak": "🎙️  Fala...", "transcribed": "🎙️  «{text}»", "not_owner": "🔒 Voz não reconhecida ({score:.2f})", "ready": "✅ JARVIS PRONTO", "goodbye": "👋 Adeus!"},
        "commands": {"/língua": "/língua", "/línguas": "/línguas", "/mudar": "/mudar língua", "/adicionar": "/adicionar língua", "/remover": "/remover língua", "/lembrar": "/lembrar", "/memória": "/memória", "/esquecer": "/esquecer tudo", "/stats": "/stats", "/tts": "/tts", "/ajuda": "/ajuda"},
        "responses": {"current_lang": "🌍 Língua atual: **{name}**", "lang_changed": "✅ Língua alterada: **{name}**", "lang_added": "✅ {name} adicionada.", "lang_removed": "✅ {name} removida.", "lang_exists": "⚠️  {name} já está na lista.", "lang_not_found": "❌ Língua não encontrada: '{query}'", "one_lang_only": "Tem apenas uma língua.", "choose_lang": "🌍 Escolha a língua:", "invalid": "❌ Escolha inválida.", "memorized": "💾 Memorizado: '{fact}'", "memory_empty": "📭 Sem memórias.", "memory_cleared": "🗑️  Memória limpa.", "help": "📋 /ajuda para comandos"},
    },

    "ko": {
        "tts": "ko",
        "wake": ["자비스", "jarvis"],
        "sleep": ["자비스 자", "자비스 쉬어"],
        "system": "항상 한국어로만 답하세요. 절대 다른 언어를 사용하지 마세요.",
        "ui": {"standby": "😴 대기 중 — '자비스'라고 말하세요...", "awake": "☀️  깨어났습니다!", "sleeping": "💤 대기 모드로 전환합니다.", "session_open": "✅ 활성화! {min}분 세션 시작", "speak": "🎙️  말씀하세요...", "transcribed": "🎙️  인식: «{text}»", "not_owner": "🔒 음성 미인식 ({score:.2f})", "ready": "✅ 자비스 준비 완료", "goodbye": "👋 안녕히 계세요!"},
        "commands": {"/언어": "/언어", "/언어목록": "/언어목록", "/언어변경": "/언어변경", "/언어추가": "/언어추가", "/stats": "/stats", "/tts": "/tts", "/도움말": "/도움말"},
        "responses": {"current_lang": "🌍 현재 언어: **{name}**", "lang_changed": "✅ 언어 변경: **{name}**", "lang_added": "✅ {name} 추가됨.", "lang_removed": "✅ {name} 삭제됨.", "lang_exists": "⚠️  {name} 이미 목록에 있습니다.", "lang_not_found": "❌ 언어를 찾을 수 없음: '{query}'", "one_lang_only": "언어가 하나입니다.", "choose_lang": "🌍 언어 선택 (번호 입력):", "invalid": "❌ 잘못된 선택.", "memorized": "💾 기억됨: '{fact}'", "memory_empty": "📭 기억 없음.", "memory_cleared": "🗑️  기억 삭제됨.", "help": "📋 /도움말"},
    },

    "ar": {
        "tts": "ar",
        "wake": ["جارفيس", "jarvis"],
        "sleep": ["جارفيس نم", "نم جارفيس"],
        "system": "أجب دائماً وفقط باللغة العربية. لا تستخدم أي لغة أخرى أبداً.",
        "ui": {"standby": "😴 في وضع الانتظار — قل 'جارفيس'...", "awake": "☀️  مستيقظ!", "sleeping": "💤 الدخول في وضع الانتظار.", "session_open": "✅ كلمة التنشيط! جلسة لمدة {min} دقيقة", "speak": "🎙️  تكلم...", "transcribed": "🎙️  «{text}»", "not_owner": "🔒 الصوت غير معروف ({score:.2f})", "ready": "✅ جارفيس جاهز", "goodbye": "👋 وداعاً!"},
        "commands": {"/لغة": "/لغة", "/لغات": "/لغات", "/تغيير": "/تغيير اللغة", "/stats": "/stats", "/tts": "/tts", "/مساعدة": "/مساعدة"},
        "responses": {"current_lang": "🌍 اللغة الحالية: **{name}**", "lang_changed": "✅ تم تغيير اللغة إلى: **{name}**", "lang_added": "✅ تمت إضافة {name}.", "lang_removed": "✅ تمت إزالة {name}.", "lang_exists": "⚠️  {name} موجودة بالفعل.", "lang_not_found": "❌ اللغة غير موجودة: '{query}'", "one_lang_only": "لديك لغة واحدة فقط.", "choose_lang": "🌍 اختر اللغة:", "invalid": "❌ اختيار غير صالح.", "memorized": "💾 تم الحفظ: '{fact}'", "memory_empty": "📭 لا توجد ذكريات.", "memory_cleared": "🗑️  تم مسح الذاكرة.", "help": "📋 /مساعدة"},
    },

    "nl": {
        "tts": "nl",
        "wake": ["jarvis", "jarvi"],
        "sleep": ["jarvis slaap", "slaap jarvis"],
        "system": "Antwoord ALTIJD en ALLEEN in het Nederlands. Nooit in het Chinees of een andere taal.",
        "ui": {"standby": "😴 Standby — zeg 'Jarvis'...", "awake": "☀️  Wakker!", "sleeping": "💤 Naar standby.", "session_open": "✅ Activatiewoord! Sessie {min} min", "speak": "🎙️  Spreek...", "transcribed": "🎙️  «{text}»", "not_owner": "🔒 Stem niet herkend ({score:.2f})", "ready": "✅ JARVIS KLAAR", "goodbye": "👋 Tot ziens!"},
        "commands": {"/taal": "/taal", "/talen": "/talen", "/wijzigen": "/taal wijzigen", "/toevoegen": "/taal toevoegen", "/verwijderen": "/taal verwijderen", "/onthouden": "/onthouden", "/geheugen": "/geheugen", "/vergeten": "/alles vergeten", "/stats": "/stats", "/tts": "/tts", "/hulp": "/hulp"},
        "responses": {"current_lang": "🌍 Huidige taal: **{name}**", "lang_changed": "✅ Taal gewijzigd: **{name}**", "lang_added": "✅ {name} toegevoegd.", "lang_removed": "✅ {name} verwijderd.", "lang_exists": "⚠️  {name} staat al in de lijst.", "lang_not_found": "❌ Taal niet gevonden: '{query}'", "one_lang_only": "Je hebt maar één taal.", "choose_lang": "🌍 Kies taal:", "invalid": "❌ Ongeldige keuze.", "memorized": "💾 Onthouden: '{fact}'", "memory_empty": "📭 Geen herinneringen.", "memory_cleared": "🗑️  Geheugen gewist.", "help": "📋 /hulp"},
    },

    "pl": {
        "tts": "pl",
        "wake": ["jarvis", "jarvi"],
        "sleep": ["jarvis śpij", "śpij jarvis"],
        "system": "Zawsze odpowiadaj TYLKO po polsku. Nigdy w języku chińskim ani innym.",
        "ui": {"standby": "😴 Tryb czuwania — powiedz 'Jarvis'...", "awake": "☀️  Aktywny!", "sleeping": "💤 Przechodzę w tryb czuwania.", "session_open": "✅ Słowo aktywacji! Sesja {min} min", "speak": "🎙️  Mów...", "transcribed": "🎙️  «{text}»", "not_owner": "🔒 Głos nierozpoznany ({score:.2f})", "ready": "✅ JARVIS GOTOWY", "goodbye": "👋 Do widzenia!"},
        "commands": {"/język": "/język", "/języki": "/języki", "/zmień": "/zmień język", "/dodaj": "/dodaj język", "/usuń": "/usuń język", "/zapamiętaj": "/zapamiętaj", "/pamięć": "/pamięć", "/zapomnij": "/zapomnij wszystko", "/stats": "/stats", "/tts": "/tts", "/pomoc": "/pomoc"},
        "responses": {"current_lang": "🌍 Bieżący język: **{name}**", "lang_changed": "✅ Język zmieniony na: **{name}**", "lang_added": "✅ {name} dodany.", "lang_removed": "✅ {name} usunięty.", "lang_exists": "⚠️  {name} już jest na liście.", "lang_not_found": "❌ Język nie znaleziony: '{query}'", "one_lang_only": "Masz tylko jeden język.", "choose_lang": "🌍 Wybierz język:", "invalid": "❌ Nieprawidłowy wybór.", "memorized": "💾 Zapamiętano: '{fact}'", "memory_empty": "📭 Brak wspomnień.", "memory_cleared": "🗑️  Pamięć wyczyszczona.", "help": "📋 /pomoc"},
    },

    "tr": {
        "tts": "tr",
        "wake": ["jarvis", "jarvi", "cervis"],
        "sleep": ["jarvis uyu", "uyu jarvis"],
        "system": "Her zaman YALNIZCA Türkçe cevap ver. Asla başka dil kullanma.",
        "ui": {"standby": "😴 Bekleme — 'Jarvis' de...", "awake": "☀️  Uyandım!", "sleeping": "💤 Bekleme moduna geçiyorum.", "session_open": "✅ Etkinleştirme kelimesi! {min} dk oturum", "speak": "🎙️  Konuş...", "transcribed": "🎙️  «{text}»", "not_owner": "🔒 Ses tanınmadı ({score:.2f})", "ready": "✅ JARVIS HAZIR", "goodbye": "👋 Hoşça kal!"},
        "commands": {"/dil": "/dil", "/diller": "/diller", "/değiştir": "/dil değiştir", "/ekle": "/dil ekle", "/kaldır": "/dil kaldır", "/hatırla": "/hatırla", "/bellek": "/bellek", "/unut": "/hepsini unut", "/stats": "/stats", "/tts": "/tts", "/yardım": "/yardım"},
        "responses": {"current_lang": "🌍 Mevcut dil: **{name}**", "lang_changed": "✅ Dil değiştirildi: **{name}**", "lang_added": "✅ {name} eklendi.", "lang_removed": "✅ {name} kaldırıldı.", "lang_exists": "⚠️  {name} zaten listede.", "lang_not_found": "❌ Dil bulunamadı: '{query}'", "one_lang_only": "Yalnızca bir diliniz var.", "choose_lang": "🌍 Dil seçin:", "invalid": "❌ Geçersiz seçim.", "memorized": "💾 Hatırlandı: '{fact}'", "memory_empty": "📭 Bellek yok.", "memory_cleared": "🗑️  Bellek silindi.", "help": "📋 /yardım"},
    },

    "sv": {
        "tts": "sv",
        "wake": ["jarvis", "jarvi"],
        "sleep": ["jarvis sov", "sov jarvis"],
        "system": "Svara ALLTID och ENDAST på svenska. Aldrig på kinesiska eller andra språk.",
        "ui": {"standby": "😴 Standby — säg 'Jarvis'...", "awake": "☀️  Vaken!", "sleeping": "💤 Går till standby.", "session_open": "✅ Aktiveringsfras! Session {min} min", "speak": "🎙️  Tala...", "transcribed": "🎙️  «{text}»", "not_owner": "🔒 Röst ej igenkänd ({score:.2f})", "ready": "✅ JARVIS REDO", "goodbye": "👋 Hej då!"},
        "commands": {"/språk": "/språk", "/språk lista": "/språklista", "/byt": "/byt språk", "/lägg till": "/lägg till språk", "/ta bort": "/ta bort språk", "/kom ihåg": "/kom ihåg", "/minne": "/minne", "/glöm": "/glöm allt", "/stats": "/stats", "/tts": "/tts", "/hjälp": "/hjälp"},
        "responses": {"current_lang": "🌍 Nuvarande språk: **{name}**", "lang_changed": "✅ Språk ändrat till: **{name}**", "lang_added": "✅ {name} tillagd.", "lang_removed": "✅ {name} borttagen.", "lang_exists": "⚠️  {name} finns redan.", "lang_not_found": "❌ Språk ej hittat: '{query}'", "one_lang_only": "Du har bara ett språk.", "choose_lang": "🌍 Välj språk:", "invalid": "❌ Ogiltigt val.", "memorized": "💾 Ihågkommet: '{fact}'", "memory_empty": "📭 Inga minnen.", "memory_cleared": "🗑️  Minne rensat.", "help": "📋 /hjälp"},
    },
}

# Aggiungi lingue mancanti con dati minimi
for _code in ALL_LANGUAGES:
    if _code not in LANG_DATA:
        LANG_DATA[_code] = {
            "tts": _code,
            "wake": ["jarvis", "jarvi"],
            "sleep": ["jarvis sleep", "sleep jarvis"],
            "system": f"Always respond ONLY in {ALL_LANGUAGES[_code]}. Never use other languages.",
            "ui": {"standby": "😴 Standby...", "awake": "☀️", "sleeping": "💤", "session_open": "✅ {min}min", "speak": "🎙️", "transcribed": "🎙️  «{text}»", "not_owner": "🔒 ({score:.2f})", "ready": "✅ JARVIS", "goodbye": "👋"},
            "commands": {"/language": "/language", "/stats": "/stats", "/tts": "/tts", "/help": "/help"},
            "responses": {"current_lang": "🌍 **{name}**", "lang_changed": "✅ **{name}**", "lang_added": "✅ {name}", "lang_removed": "✅ {name}", "lang_exists": "⚠️  {name}", "lang_not_found": "❌ '{query}'", "one_lang_only": "Only one language.", "choose_lang": "🌍 Choose:", "invalid": "❌ Invalid.", "memorized": "💾 '{fact}'", "memory_empty": "📭 Empty.", "memory_cleared": "🗑️  Cleared.", "help": "📋 /help"},
        }


# ══════════════════════════════════════════════════════════════════════════════
# LANGUAGE MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class LanguageManager:
    """
    Gestisce lingua corrente, lista personale, comandi slash.
    Tutto localizzato nella lingua scelta.
    """

    def __init__(self, memory_dir: Path = Path.home() / "jarvis_memory"):
        self._path = memory_dir / "languages.json"
        self._data = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text("utf-8"))
            except Exception:
                pass
        return {"current": None, "my_languages": []}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8")

    # ── Proprietà ─────────────────────────────────────────────────────────────

    @property
    def current(self) -> str:
        return self._data.get("current") or "it"

    @property
    def data(self) -> dict:
        return LANG_DATA.get(self.current, LANG_DATA["en"])

    @property
    def tts_lang(self) -> str:
        return self.data["tts"]

    @property
    def system_instruction(self) -> str:
        return self.data["system"]

    @property
    def wake_words(self) -> list[str]:
        return self.data["wake"]

    @property
    def sleep_phrases(self) -> list[str]:
        return self.data["sleep"]

    @property
    def ui(self) -> dict:
        return self.data["ui"]

    @property
    def responses(self) -> dict:
        return self.data["responses"]

    @property
    def my_languages(self) -> list[str]:
        langs = self._data.get("my_languages", [])
        if self.current not in langs:
            langs = [self.current] + langs
        return langs

    @property
    def is_first_run(self) -> bool:
        return self._data.get("current") is None

    def t(self, key: str, **kwargs) -> str:
        """Traduzione localizzata con parametri."""
        msg = self.responses.get(key, key)
        try:
            return msg.format(**kwargs)
        except Exception:
            return msg

    def ui_msg(self, key: str, **kwargs) -> str:
        """Messaggio UI localizzato."""
        msg = self.ui.get(key, key)
        try:
            return msg.format(**kwargs)
        except Exception:
            return msg

    # ── Operazioni lingua ─────────────────────────────────────────────────────

    def set_language(self, code: str) -> bool:
        if code not in ALL_LANGUAGES:
            return False
        self._data["current"] = code
        langs = self._data.setdefault("my_languages", [])
        if code not in langs:
            langs.append(code)
        self._save()
        return True

    def add_language(self, code: str) -> tuple[bool, str]:
        if code not in ALL_LANGUAGES:
            return False, self.t("lang_not_found", query=code)
        langs = self._data.setdefault("my_languages", [])
        if code in langs:
            return False, self.t("lang_exists", name=ALL_LANGUAGES[code])
        langs.append(code)
        self._save()
        return True, self.t("lang_added", name=ALL_LANGUAGES[code])

    def remove_language(self, code: str) -> tuple[bool, str]:
        if code == self.current:
            return False, "Cannot remove current language."
        langs = self._data.get("my_languages", [])
        if code not in langs:
            return False, self.t("lang_not_found", query=code)
        langs.remove(code)
        self._save()
        return True, self.t("lang_removed", name=ALL_LANGUAGES.get(code, code))

    # ── Setup prima esecuzione ─────────────────────────────────────────────────

    def setup_first_run(self) -> str:
        print("\n" + "═" * 52)
        print("🌍 Choose your language / Scegli la lingua")
        print("═" * 52)

        priority = ["it", "fr", "en", "de", "es", "pt", "lb", "ru", "zh", "ja"]
        others   = [c for c in ALL_LANGUAGES if c not in priority]
        ordered  = priority + others

        for i, code in enumerate(ordered, 1):
            name   = ALL_LANGUAGES[code]
            marker = " ⭐" if code in ("it", "fr", "en") else ""
            print(f"  {i:2}. [{code}] {name}{marker}")

        print()
        while True:
            try:
                scelta = input("Scelta / Choice [1]: ").strip() or "1"
                idx    = int(scelta) - 1
                code   = ordered[idx]
                self.set_language(code)
                print(f"✅ {ALL_LANGUAGES[code]}")
                return code
            except (ValueError, IndexError):
                print("❌ Invalid / Non valido")

    # ── Riconoscimento wake/sleep word localizzati ────────────────────────────

    def is_wake(self, text: str) -> bool:
        """Controlla se il testo contiene il wake word nella lingua corrente."""
        t = text.lower().strip()
        for w in self.wake_words:
            if w in t:
                return True
        return False

    def is_sleep(self, text: str) -> bool:
        """Controlla se il testo è una sleep phrase nella lingua corrente."""
        t = text.lower().strip()
        for phrase in self.sleep_phrases:
            if phrase in t:
                return True
        return False

    def strip_wake(self, text: str) -> str:
        """Rimuove il wake word dal testo."""
        t = text.strip()
        for w in self.wake_words:
            t = re.sub(rf'\b{re.escape(w)}\b', '', t, flags=re.IGNORECASE).strip()
        return re.sub(r'^[,\s!]+', '', t).strip()

    # ── Comandi slash ─────────────────────────────────────────────────────────

    def is_slash_command(self, text: str) -> bool:
        return text.strip().startswith("/")

    def handle_slash(self, text: str) -> Optional[str]:
        """Processa un comando slash — localizzato."""
        t   = text.strip()
        cmd = t.lower()

        if not cmd.startswith("/"):
            return None

        cmds = self.data.get("commands", {})

        # Lingua corrente
        if cmd in ["/lingua", "/language", "/langue", "/sprache", "/idioma",
                   "/sprooch", "/язык", "/語言", "/言語", "/언어", "/لغة", "/taal",
                   "/język", "/dil", "/språk"] or \
           cmd == list(cmds.get("/lingua", [cmds.get("/language", "/language")]))[0].lower() if isinstance(cmds.get("/lingua",""), str) else False:
            return self._cmd_current()

        # Controlla per prefisso
        for key, slash in cmds.items():
            if cmd == slash.lower() or cmd.startswith(slash.lower()):
                rest = cmd[len(slash):].strip()
                if key in ("/lingua", "/language", "/langue", "/sprooch", "/язык"):
                    return self._cmd_current()
                if key in ("/lingue", "/languages", "/langues", "/sprachen"):
                    return self._cmd_my_langs()
                if key in ("/cambia", "/change", "/changer", "/wechseln", "/wiesselen",
                           "/сменить", "/切换语言", "/言語変更", "/언어변경"):
                    return self._cmd_change()
                if key in ("/aggiungi", "/add", "/ajouter", "/hinzufügen", "/derbäisetzen",
                           "/добавить", "/添加语言", "/言語追加", "/언어추가"):
                    return self._cmd_add(rest)
                if key in ("/rimuovi", "/remove", "/supprimer", "/entfernen", "/ewechhuelen",
                           "/удалить", "/删除语言", "/언어삭제"):
                    return self._cmd_remove(rest)
                if key in ("/memorizza", "/remember", "/mémoriser", "/merken", "/onthalen",
                           "/запомни", "/记住", "/zapamiętaj", "/hatırla", "/kom ihåg"):
                    fact = t[len(slash):].strip()
                    return self.t("memorized", fact=fact) if fact else None
                if key in ("/aiuto", "/help", "/aide", "/hilfe", "/hëllef",
                           "/помощь", "/帮助", "/ヘルプ", "/도움말", "/مساعدة", "/hulp", "/pomoc", "/yardım", "/hjälp"):
                    return self.t("help")
                if key in ("/stats",):
                    return "__STATS__"
                if key in ("/tts",):
                    return "__TTS__"

        # Fallback generico
        if "/cambia" in cmd or "/change" in cmd or "/changer" in cmd or "/wechsel" in cmd:
            return self._cmd_change()
        if "/aggiungi" in cmd or "/add" in cmd or "/ajouter" in cmd:
            rest = re.sub(r'/\w+\s*', '', t, count=1).strip()
            return self._cmd_add(rest)
        if "/rimuovi" in cmd or "/remove" in cmd or "/supprimer" in cmd:
            rest = re.sub(r'/\w+\s*', '', t, count=1).strip()
            return self._cmd_remove(rest)
        if "/lingue" in cmd or "/languages" in cmd or "/langues" in cmd:
            return self._cmd_my_langs()
        if "/aiuto" in cmd or "/help" in cmd or "/aide" in cmd or "/hilfe" in cmd:
            return self.t("help")
        if "/stats" in cmd:
            return "__STATS__"
        if "/tts" in cmd:
            return "__TTS__"

        return None

    def _cmd_current(self) -> str:
        return self.t("current_lang", name=ALL_LANGUAGES.get(self.current, self.current))

    def _cmd_my_langs(self) -> str:
        lines = []
        for i, code in enumerate(self.my_languages, 1):
            name   = ALL_LANGUAGES.get(code, code)
            marker = " ◀" if code == self.current else ""
            lines.append(f"  {i}. [{code}] {name}{marker}")
        header = self.ui_msg("choose_lang")
        return "🌍 " + header + "\n" + "\n".join(lines)

    def _cmd_change(self) -> str:
        langs = self.my_languages
        if len(langs) == 1:
            return self.t("one_lang_only")

        print(self.ui_msg("choose_lang"))
        for i, code in enumerate(langs, 1):
            name   = ALL_LANGUAGES.get(code, code)
            marker = " ◀" if code == self.current else ""
            print(f"  {i}. [{code}] {name}{marker}")

        try:
            scelta = input("> ").strip()
            idx    = int(scelta) - 1
            code   = langs[idx]
            self.set_language(code)
            return self.t("lang_changed", name=ALL_LANGUAGES.get(code, code))
        except (ValueError, IndexError):
            return self.t("invalid")

    def _cmd_add(self, query: str) -> str:
        code = self._find_lang(query)
        if not code:
            # Mostra lista completa
            codes = list(ALL_LANGUAGES.keys())
            print("🌍 " + self.ui_msg("choose_lang"))
            for i, c in enumerate(codes, 1):
                print(f"  {i:2}. [{c}] {ALL_LANGUAGES[c]}")
            try:
                scelta = input("> ").strip()
                code   = codes[int(scelta) - 1]
            except (ValueError, IndexError):
                return self.t("invalid")
        ok, msg = self.add_language(code)
        return msg

    def _cmd_remove(self, query: str) -> str:
        code = self._find_lang(query)
        if not code:
            return self._cmd_my_langs() + "\n" + self.t("lang_not_found", query=query)
        ok, msg = self.remove_language(code)
        return msg

    def _find_lang(self, query: str) -> Optional[str]:
        if not query:
            return None
        q = query.lower().strip()
        if q in ALL_LANGUAGES:
            return q
        for code, name in ALL_LANGUAGES.items():
            if q in name.lower() or name.lower().startswith(q):
                return code
        return None

    def format_status(self) -> str:
        name = ALL_LANGUAGES.get(self.current, self.current)
        n    = len(self.my_languages)
        return f"🌍 {name} | {n} lingua/e"


# ══════════════════════════════════════════════════════════════════════════════
# TEST STANDALONE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    lm = LanguageManager()

    if lm.is_first_run:
        lm.setup_first_run()

    print(lm.format_status())
    print(f"Wake words: {lm.wake_words}")
    print(f"Sleep phrases: {lm.sleep_phrases}")
    print(f"Sistema: {lm.system_instruction}")
    print()

    while True:
        try:
            cmd = input("> ").strip()
            if not cmd: continue
            r = lm.handle_slash(cmd)
            if r: print(r)
            else: print(f"(non slash: '{cmd}')")
        except KeyboardInterrupt:
            print("\nStop.")
            break