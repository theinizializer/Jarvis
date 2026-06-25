#!/usr/bin/env python3
"""
JARVIS — Agent Module
Sistema di pianificazione autonoma:
obiettivo → piano → sotto-task → esecuzione → verifica → adattamento → completamento

Il modulo intercetta richieste complesse e le gestisce con un loop agente
invece di eseguire alla cieca.
"""

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable
from datetime import datetime


# ══════════════════════════════════════════════════════════════════════════════
# STRUTTURE DATI
# ══════════════════════════════════════════════════════════════════════════════

class TaskStatus(Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    SKIPPED   = "skipped"
    ADAPTED   = "adapted"


@dataclass
class SubTask:
    id:          int
    description: str
    status:      TaskStatus = TaskStatus.PENDING
    result:      str        = ""
    attempts:    int        = 0
    adapted:     bool       = False

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "description": self.description,
            "status":      self.status.value,
            "result":      self.result[:200] if self.result else "",
            "attempts":    self.attempts,
        }


@dataclass
class AgentPlan:
    objective:  str
    tasks:      list[SubTask]      = field(default_factory=list)
    created_at: str                = field(default_factory=lambda: datetime.now().isoformat())
    completed:  bool               = False
    success:    bool               = False
    summary:    str                = ""

    @property
    def pending(self)  -> list[SubTask]:
        return [t for t in self.tasks if t.status == TaskStatus.PENDING]

    @property
    def done(self)     -> list[SubTask]:
        return [t for t in self.tasks if t.status == TaskStatus.DONE]

    @property
    def failed(self)   -> list[SubTask]:
        return [t for t in self.tasks if t.status == TaskStatus.FAILED]

    @property
    def progress(self) -> str:
        total = len(self.tasks)
        done  = len(self.done)
        return f"{done}/{total}"


# ══════════════════════════════════════════════════════════════════════════════
# RILEVAMENTO OBIETTIVI COMPLESSI
# ══════════════════════════════════════════════════════════════════════════════

# Pattern che indicano una richiesta complessa che beneficia della pianificazione
_COMPLEX_PATTERNS = re.compile(
    r'\b('
    # Installazioni e setup
    r'installa.*e\s+configura|setup\s+completo|configura\s+tutto|'
    r'installe.*et\s+configure|install.*and\s+set\s*up|'
    # Sviluppo software
    r'crea.*progetto|crea.*app|crea.*sito|crea.*applicazione|'
    r'create.*project|create.*app|create.*website|crée.*projet|'
    # Operazioni su file multiple
    r'organizza.*file|rinomina.*tutti|sposta.*tutti|'
    r'organize.*files|rename.*all|move.*all|'
    # Task multi-step esplicite
    r'prima.*poi.*infine|step\s+by\s+step|passo\s+per\s+passo|'
    r'prima\s+di\s+tutto|first.*then.*finally|'
    # Automazione e script
    r'automatizza|scrivi.*script.*che|crea.*script.*per|'
    r'automate|write.*script.*that|create.*script.*for|'
    # Deploy e produzione
    r'deploy|metti.*in\s+produzione|configura.*server|'
    r'set\s+up.*server|configure.*nginx|configure.*apache|'
    # Backup e migrazione
    r'backup.*e\s+|migra|migrazione|'
    r'migrate|migration|backup.*and|'
    # Task con più di 3 step impliciti
    r'tutto\s+il\s+necessario|tutto\s+quello\s+che\s+serve|'
    r'everything\s+needed|all\s+that\s+is\s+needed'
    r')\b',
    re.IGNORECASE
)

# Soglia minima di parole per considerare una richiesta come complessa
_MIN_WORDS_FOR_AGENT = 8


def should_use_agent(user_msg: str) -> bool:
    """
    Decide se attivare il sistema agente per questo messaggio.
    Attiva per richieste complesse e multi-step.
    """
    words = user_msg.strip().split()
    if len(words) < _MIN_WORDS_FOR_AGENT:
        return False
    return bool(_COMPLEX_PATTERNS.search(user_msg))


# ══════════════════════════════════════════════════════════════════════════════
# AGENT CORE
# ══════════════════════════════════════════════════════════════════════════════

class JarvisAgent:
    """
    Agente di pianificazione autonoma per JARVIS.

    Flusso:
        obiettivo → piano → [sotto-task → esecuzione → verifica → adattamento] → completamento
    """

    MAX_ATTEMPTS   = 3    # tentativi per sotto-task prima di fallire
    MAX_ITERATIONS = 20   # sicurezza anti-loop
    MAX_ADAPTATIONS = 3   # max adattamenti piano prima di rinunciare

    def __init__(self, call_model: Callable, execute_cmd: Callable,
                 execute_search: Callable = None, lang=None):
        """
        call_model:     funzione(prompt, history) → (text, tool_calls)
        execute_cmd:    funzione(command, explanation) → dict{status, output}
        execute_search: funzione(query, explanation) → dict{status, output}
        lang:           LanguageManager per messaggi localizzati
        """
        self._call    = call_model
        self._exec    = execute_cmd
        self._search  = execute_search
        self._lang    = lang
        self._current_plan: Optional[AgentPlan] = None

    # ── UI localizzata ────────────────────────────────────────────────────────

    def _ui(self, key: str, **kwargs) -> str:
        msgs = {
            "planning":    {"it": "🧠 Pianificando...",        "fr": "🧠 Planification...",      "en": "🧠 Planning..."},
            "executing":   {"it": "⚙️  Eseguo step {n}/{t}:",  "fr": "⚙️  Étape {n}/{t} :",      "en": "⚙️  Step {n}/{t}:"},
            "verifying":   {"it": "🔍 Verifico risultato...",  "fr": "🔍 Vérification...",        "en": "🔍 Verifying..."},
            "adapting":    {"it": "🔄 Adatto il piano...",     "fr": "🔄 Adaptation du plan...",  "en": "🔄 Adapting plan..."},
            "done":        {"it": "✅ Obiettivo completato!",  "fr": "✅ Objectif atteint !",     "en": "✅ Objective completed!"},
            "failed":      {"it": "❌ Obiettivo fallito.",     "fr": "❌ Objectif échoué.",       "en": "❌ Objective failed."},
            "step_ok":     {"it": "  ✅ Step {n} completato",  "fr": "  ✅ Étape {n} terminée",   "en": "  ✅ Step {n} done"},
            "step_fail":   {"it": "  ❌ Step {n} fallito",     "fr": "  ❌ Étape {n} échouée",    "en": "  ❌ Step {n} failed"},
            "retrying":    {"it": "  🔁 Riprovo ({a}/{m})...", "fr": "  🔁 Nouvel essai ({a}/{m})...", "en": "  🔁 Retry ({a}/{m})..."},
            "plan_header": {"it": "📋 Piano ({n} step):",      "fr": "📋 Plan ({n} étapes) :",    "en": "📋 Plan ({n} steps):"},
        }
        lang = self._lang.current if self._lang else "en"
        template = msgs.get(key, {}).get(lang) or msgs.get(key, {}).get("en", key)
        try:
            return template.format(**kwargs)
        except Exception:
            return template

    # ── FASE 1: Pianificazione ────────────────────────────────────────────────

    def _plan(self, objective: str, history: list) -> Optional[AgentPlan]:
        """Chiede al modello di creare un piano strutturato."""

        lang_name = self._lang.data.get("name", "English") if self._lang else "English"

        prompt = (
            f"Create a step-by-step execution plan for this objective:\n"
            f"OBJECTIVE: {objective}\n\n"
            f"Rules:\n"
            f"- Each step must be a single, concrete, executable action\n"
            f"- Steps must be in the correct order\n"
            f"- Maximum 10 steps\n"
            f"- No redundant steps\n"
            f"- Each step description in {lang_name}\n\n"
            f"Respond ONLY with valid JSON, no other text:\n"
            f'{{"steps": ["step 1 description", "step 2 description", ...]}}'
        )

        print(self._ui("planning"), flush=True)

        text, _ = self._call(prompt, history=history)

        # Estrai JSON
        try:
            m = re.search(r'\{.*?"steps"\s*:\s*\[.*?\]\s*\}', text, re.DOTALL)
            if m:
                data  = json.loads(m.group())
                steps = data.get("steps", [])
            else:
                # Fallback: estrai lista numerata dal testo
                steps = re.findall(r'(?:^\d+[\.\)]\s*|^[-*]\s*)(.+)', text, re.MULTILINE)

            if not steps:
                return None

            tasks = [SubTask(id=i+1, description=s.strip())
                     for i, s in enumerate(steps[:10]) if s.strip()]

            plan = AgentPlan(objective=objective, tasks=tasks)

            # Stampa piano
            print(f"\n{self._ui('plan_header', n=len(tasks))}", flush=True)
            for t in tasks:
                print(f"  {t.id}. {t.description}", flush=True)
            print()

            return plan

        except Exception as e:
            print(f"⚠️  Piano non parsato: {e}", flush=True)
            return None

    # ── FASE 2: Esecuzione singolo task ───────────────────────────────────────

    def _execute_task(self, task: SubTask, plan: AgentPlan, history: list) -> bool:
        """Esegue un singolo sotto-task tramite il modello."""

        task.status   = TaskStatus.RUNNING
        task.attempts += 1

        # Contesto: obiettivo + step completati + step corrente
        done_summary = ""
        if plan.done:
            done_summary = "Completed steps:\n" + "\n".join(
                f"  ✅ {t.id}. {t.description}: {t.result[:100]}"
                for t in plan.done
            ) + "\n\n"

        prompt = (
            f"OBJECTIVE: {plan.objective}\n\n"
            f"{done_summary}"
            f"CURRENT STEP {task.id}/{len(plan.tasks)}: {task.description}\n\n"
            f"Execute this step now. Use execute_terminal_command or web_search as needed.\n"
            f"Be direct — execute immediately without asking for confirmation."
        )

        print(self._ui("executing", n=task.id, t=len(plan.tasks)), flush=True)
        print(f"  → {task.description}", flush=True)

        text, tool_calls = self._call(prompt, history=history)

        # Esegui tool calls
        results = []
        for tc in tool_calls:
            fn   = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})

            if name == "execute_terminal_command":
                cmd  = args.get("command", "").strip()
                expl = args.get("explanation", "")
                if cmd:
                    r = self._exec(cmd, expl)
                    results.append(r)

            elif name == "web_search" and self._search:
                query = args.get("query", "").strip()
                expl  = args.get("explanation", "")
                if query:
                    r = self._search(query, expl)
                    results.append(r)

        # Aggrega output
        if results:
            task.result = "\n".join(
                r.get("output", "")[:300]
                for r in results
                if r.get("output")
            )
            # Fallito se tutti i risultati sono errori
            all_failed = all(r.get("status") == "error" for r in results)
            if all_failed:
                return False
        elif text and len(text) > 5:
            # Solo risposta testuale — considera successo se non contiene errori
            task.result = text[:300]
            if re.search(r'\b(error|errore|failed|fallito|cannot|impossibile)\b',
                         text, re.IGNORECASE):
                return False
        else:
            task.result = "No output"

        return True

    # ── FASE 3: Verifica ─────────────────────────────────────────────────────

    def _verify_task(self, task: SubTask, plan: AgentPlan, history: list) -> bool:
        """Chiede al modello se il task è stato completato con successo."""

        if not task.result or task.result == "No output":
            return False

        prompt = (
            f"Verify if this step was completed successfully:\n"
            f"Step: {task.description}\n"
            f"Result: {task.result}\n\n"
            f"Answer with ONLY 'SUCCESS' or 'FAILURE' and a brief reason."
        )

        print(self._ui("verifying"), flush=True)
        text, _ = self._call(prompt, history=[])  # history vuota — verifica rapida

        is_success = bool(re.search(r'\bSUCCESS\b', text, re.IGNORECASE))
        return is_success

    # ── FASE 4: Adattamento ───────────────────────────────────────────────────

    def _adapt_plan(self, task: SubTask, plan: AgentPlan, history: list) -> bool:
        """
        Se un task fallisce, chiede al modello di adattare il piano.
        Può sostituire il task fallito o aggiungere step intermedi.
        """
        print(self._ui("adapting"), flush=True)

        remaining = [t for t in plan.tasks
                     if t.status in (TaskStatus.PENDING, TaskStatus.FAILED)]

        prompt = (
            f"OBJECTIVE: {plan.objective}\n\n"
            f"Step {task.id} FAILED: {task.description}\n"
            f"Error: {task.result}\n\n"
            f"Remaining steps:\n"
            + "\n".join(f"  {t.id}. {t.description}" for t in remaining)
            + "\n\nProvide adapted steps to recover and complete the objective.\n"
            f"Respond ONLY with JSON:\n"
            f'{{"steps": ["new step 1", "new step 2", ...]}}'
        )

        text, _ = self._call(prompt, history=history)

        try:
            m = re.search(r'\{.*?"steps"\s*:\s*\[.*?\]\s*\}', text, re.DOTALL)
            if not m:
                return False

            data  = json.loads(m.group())
            new_steps = data.get("steps", [])
            if not new_steps:
                return False

            # Rimuovi task pending e sostituisci con adattati
            plan.tasks = [t for t in plan.tasks
                          if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED)]

            start_id = len(plan.tasks) + 1
            for i, s in enumerate(new_steps[:8]):
                plan.tasks.append(SubTask(
                    id=start_id + i,
                    description=s.strip(),
                    adapted=True
                ))

            print(f"  📋 Piano adattato: {len(new_steps)} nuovi step", flush=True)
            for t in plan.tasks:
                if t.status == TaskStatus.PENDING:
                    print(f"  → {t.id}. {t.description}", flush=True)
            print()
            return True

        except Exception as e:
            print(f"  ⚠️  Adattamento fallito: {e}", flush=True)
            return False

    # ── FASE 5: Sommario finale ───────────────────────────────────────────────

    def _summarize(self, plan: AgentPlan, history: list) -> str:
        """Chiede al modello un sommario del lavoro fatto."""

        done_tasks = "\n".join(
            f"  ✅ {t.description}: {t.result[:100]}"
            for t in plan.done
        )
        failed_tasks = "\n".join(
            f"  ❌ {t.description}: {t.result[:100]}"
            for t in plan.failed
        )

        status = "successfully completed" if plan.success else "partially completed"
        lang_name = self._lang.data.get("name", "English") if self._lang else "English"

        prompt = (
            f"Provide a concise summary in {lang_name} of the work done:\n\n"
            f"OBJECTIVE: {plan.objective}\n"
            f"STATUS: {status}\n"
            f"PROGRESS: {plan.progress}\n\n"
            + (f"Completed:\n{done_tasks}\n" if done_tasks else "")
            + (f"Failed:\n{failed_tasks}\n" if failed_tasks else "")
            + "\nBe concise and direct. Focus on what was achieved."
        )

        text, _ = self._call(prompt, history=history)
        return text.strip()

    # ── LOOP PRINCIPALE ───────────────────────────────────────────────────────

    def run(self, objective: str, history: list) -> str:
        """
        Esegue il ciclo agente completo.
        Ritorna un sommario testuale del risultato.
        """
        sep = "─" * 52
        print(f"\n{sep}")
        print(f"🎯 AGENTE: {objective[:60]}")
        print(sep)

        # FASE 1: Pianificazione
        plan = self._plan(objective, history)
        if not plan or not plan.tasks:
            return "❌ Impossibile creare un piano per questo obiettivo."

        self._current_plan = plan
        adaptations = 0
        iterations  = 0

        # LOOP: esecuzione → verifica → adattamento
        while plan.pending and iterations < self.MAX_ITERATIONS:
            iterations += 1
            task = plan.pending[0]

            # FASE 2: Esecuzione
            success = False
            for attempt in range(self.MAX_ATTEMPTS):
                task.attempts = attempt + 1
                if attempt > 0:
                    print(self._ui("retrying", a=attempt+1, m=self.MAX_ATTEMPTS), flush=True)

                exec_ok = self._execute_task(task, plan, history)

                if not exec_ok:
                    continue

                # FASE 3: Verifica
                verify_ok = self._verify_task(task, plan, history)
                if verify_ok:
                    success = True
                    break
                # Altrimenti riprova

            if success:
                task.status = TaskStatus.DONE
                print(self._ui("step_ok", n=task.id), flush=True)
            else:
                task.status = TaskStatus.FAILED
                print(self._ui("step_fail", n=task.id), flush=True)

                # FASE 4: Adattamento
                if adaptations < self.MAX_ADAPTATIONS:
                    adapted = self._adapt_plan(task, plan, history)
                    if adapted:
                        adaptations += 1
                        continue
                    else:
                        # Salta il task e prova il prossimo
                        task.status = TaskStatus.SKIPPED

                else:
                    # Troppi adattamenti — salta e continua
                    task.status = TaskStatus.SKIPPED
                    print(f"  ⚠️  Max adattamenti raggiunto — salto step", flush=True)

        # FASE 5: Completamento
        plan.completed = True
        plan.success   = len(plan.failed) == 0 and len(plan.done) > 0

        result_msg = self._ui("done") if plan.success else self._ui("failed")
        print(f"\n{sep}")
        print(f"{result_msg} [{plan.progress} step]")
        print(sep)

        # Sommario
        summary = self._summarize(plan, history)
        plan.summary = summary
        self._current_plan = None

        return summary

    @property
    def is_running(self) -> bool:
        return self._current_plan is not None

    def get_status(self) -> Optional[str]:
        """Ritorna lo stato corrente del piano (utile per debug)."""
        p = self._current_plan
        if not p:
            return None
        lines = [f"🎯 {p.objective[:50]}", f"📊 Progresso: {p.progress}"]
        for t in p.tasks:
            icon = {"pending": "⏳", "running": "🔄", "done": "✅",
                    "failed": "❌", "skipped": "⏭️", "adapted": "🔄"}.get(t.status.value, "?")
            lines.append(f"  {icon} {t.id}. {t.description[:50]}")
        return "\n".join(lines)
