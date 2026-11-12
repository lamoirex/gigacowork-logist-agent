#!/usr/bin/env python3
"""Минимальный агентный harness вокруг GigaChat API для проверки навыка.

Эмулирует GigaCowork: системный промпт агента + SKILL.md навыка, пользователь
прикладывает файлы и отправляет сообщение без текста. Модель сама вызывает
функцию навыка (function calling), скрипт принимает все решения, модель пишет ответ.

    export GIGACHAT_CREDENTIALS=<ключ авторизации из личного кабинета developers.sber.ru>
    python3 tools/gigachat_agent.py testdata/datasets/ds1_base/input --runs 3 \
        --expected testdata/datasets/ds1_base/expected.json --model GigaChat-2-Max

Режимы:
  --mode agent   (по умолчанию) модель получает описание функции и вызывает её сама;
  --mode simple  упрощённый режим из ТЗ: скрипт запускается напрямую, модель получает
                 его вывод и пишет ответ пользователю.

TLS: GigaChat использует сертификат НУЦ Минцифры. Укажите --ca-bundle путь_к_russian_trusted_root_ca.cer
или (только для теста) --insecure.

Логи каждого прогона: runs/<имя набора>/gigachat_<режим>_<N>/ (transcript.md, log.json, результат агента).
Только стандартная библиотека Python.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "agent", "skill", "logist-distribution")
SYSTEM_PROMPT = os.path.join(ROOT, "agent", "system_prompt.txt")
AUTH_URL = os.environ.get("GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
DEFAULT_BASE = "https://gigachat.devices.sberbank.ru/api/v1"

FUNCTION = {
    "name": "run_logist_distribution",
    "description": (
        "Запускает скрипт навыка logist-distribution: распределяет новые ФУЛ между логистами "
        "и готовит строки реестра Cloud. Передай все приложенные пользователем Excel-файлы."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Пути ко всем приложенным Excel-файлам в любом порядке",
            },
        },
        "required": ["files"],
    },
    "return_parameters": {
        "type": "object",
        "properties": {
            "exit_code": {"type": "integer", "description": "0 — успех, 2 — ошибка входных данных"},
            "stdout": {"type": "string", "description": "вывод скрипта"},
            "chat_message": {"type": "string", "description": "готовый ответ для чата (содержимое chat_message.txt)"},
        },
    },
}


DECISION_FUNCTION = {
    "name": "get_decision",
    "description": (
        "Возвращает решение агента из журнала решений по номеру заказа (S0001234) или номеру ФУЛ: "
        "статус обработки, группу, логиста, правило и обоснование. Используй для ответов на вопросы "
        "«почему заказ отдан этому логисту», «почему строка не создана» и т. п. Работает после распределения."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Номер заказа или номер ФУЛ из вопроса пользователя"}
        },
        "required": ["query"],
    },
    "return_parameters": {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "description": "найденные решения из журнала",
                "items": {
                    "type": "object",
                    "properties": {
                        "ful": {"type": "string", "description": "номер ФУЛ"},
                        "order": {"type": "string", "description": "номер заказа"},
                        "outcome": {"type": "string", "description": "статус обработки ФУЛ"},
                        "group": {"type": "string", "description": "группа логистов"},
                        "logist": {"type": "string", "description": "назначенный логист (пусто — не назначен)"},
                        "rule": {"type": "string", "description": "правило распределения"},
                        "reason": {"type": "string", "description": "обоснование решения"},
                        "candidates": {
                            "type": "string",
                            "description": "сотрудники группы: загрузка или причина недоступности",
                        },
                    },
                },
            },
            "note": {"type": "string", "description": "пояснение, если ничего не найдено"},
        },
    },
}


def get_decision(query, out_dir):
    """Поиск решения в summary.json последнего распределения (то же, что лист «Журнал решений»)."""
    path = Path(out_dir, "summary.json")
    if not path.exists():
        return {"error": "Распределение ещё не выполнено: сначала запусти run_logist_distribution."}
    q = str(query or "").lower()
    found = [
        a
        for a in json.loads(path.read_text(encoding="utf-8"))["assignments"]
        if (a.get("order") and a["order"].lower() in q) or (a.get("ful") and a["ful"].lower() in q)
    ]
    return {"decisions": found[:5]} if found else {"decisions": [], "note": f"По запросу «{query}» решений не найдено."}


class GigaChat:
    def __init__(self, credentials, scope, base_url, ctx, model, timeout=60):
        self.credentials, self.scope, self.base, self.ctx = credentials, scope, base_url.rstrip("/"), ctx
        self.model, self.timeout = model, timeout
        self.token, self.exp = None, 0

    def _post(self, url, data, headers):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:2000]
            raise SystemExit(f"GigaChat API: HTTP {exc.code} {exc.reason} — {url}\n{body}") from None

    def auth(self):
        if self.token and time.time() * 1000 < self.exp - 60_000:
            return
        r = self._post(
            AUTH_URL,
            f"scope={self.scope}".encode(),
            {
                "Authorization": f"Basic {self.credentials}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        self.token, self.exp = r["access_token"], r["expires_at"]

    def chat(self, messages, functions=None):
        self.auth()
        body = {"model": self.model, "messages": messages, "temperature": 0.01, "max_tokens": 1500}
        if functions:
            body["functions"] = functions
            body["function_call"] = "auto"
        return self._post(
            f"{self.base}/chat/completions",
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json"},
        )


def run_skill(files, out_dir):
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, os.path.join(SKILL_DIR, "scripts", "distribute.py"), *files, "--out", out_dir],
        capture_output=True,
        text=True,
        timeout=120,
    )
    res = {"exit_code": r.returncode, "stdout": r.stdout + (r.stderr[-2000:] if r.stderr else "")}
    cm = os.path.join(out_dir, "chat_message.txt")
    if r.returncode == 0 and os.path.exists(cm):
        res["chat_message"] = Path(cm).read_text(encoding="utf-8")
    return res, time.perf_counter() - t0


def list_attachments(path):
    if os.path.isdir(path):
        return [
            os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith((".xlsx", ".xls", ".xlsm"))
        ]
    return [path]


def system_messages():
    sp = Path(SYSTEM_PROMPT).read_text(encoding="utf-8")
    skill = Path(SKILL_DIR, "SKILL.md").read_text(encoding="utf-8")
    return [{"role": "system", "content": sp + "\n\n# Подключённый навык\n\n" + skill}]


def user_message(files):
    # Текста запроса нет: как в GigaCowork, платформа сообщает модели только о вложениях.
    names = "\n".join(f"- {f}" for f in files)
    return {"role": "user", "content": f"[Вложения пользователя:]\n{names}"}


def agent_turn(gc, messages, files, out_dir, log, max_steps=4):
    """Один ответ агента: модель может вызывать функции, пока не ответит текстом. -> (ответ, время скрипта)."""
    script_time = 0.0
    for _ in range(max_steps):
        resp = gc.chat(messages, functions=[FUNCTION, DECISION_FUNCTION])
        msg = resp["choices"][0]["message"]
        log["steps"].append(
            {"model": msg, "usage": resp.get("usage"), "finish_reason": resp["choices"][0].get("finish_reason")}
        )
        fc = msg.get("function_call")
        if not fc:
            return msg.get("content", ""), script_time
        args = fc.get("arguments") or {}
        if isinstance(args, str):
            args = json.loads(args or "{}")
        if fc.get("name") == DECISION_FUNCTION["name"]:
            res = get_decision(args.get("query"), out_dir)
        else:
            res, dt_ = run_skill(args.get("files") or files, out_dir)
            script_time += dt_
        log["steps"].append({"tool": fc.get("name"), "args": args, "result": res})
        assistant = {"role": "assistant", "content": msg.get("content") or "", "function_call": fc}
        if msg.get("functions_state_id"):
            assistant["functions_state_id"] = msg["functions_state_id"]
        messages.append(assistant)
        messages.append({"role": "function", "name": fc.get("name"), "content": json.dumps(res, ensure_ascii=False)})
    return "", script_time


def check_followup(answer, must_contain, must_not_contain=()):
    """Проверка ответа на уточняющий вопрос по фактам из журнала решений (без учёта регистра).

    must_contain — каждый пункт обязателен; пункт-список означает «любой из вариантов»
    (формы слова и синонимы: «уволен» / «уволил…»). must_not_contain — утверждения, которых в
    журнале нет и которые делают ответ неверным (например, «наименьшая загрузка», если логист выбран
    по истории). -> список проблем; пустой список — ответ засчитан.
    """
    text = (answer or "").lower().replace("ё", "е")
    problems = []
    for item in must_contain:
        options = item if isinstance(item, list) else [item]
        if not any(o.lower().replace("ё", "е") in text for o in options):
            problems.append(" | ".join(options))
    for bad in must_not_contain:
        if bad.lower().replace("ё", "е") in text:
            problems.append(f"неверное утверждение: «{bad}»")
    return problems


def run_followups(gc, messages, files, out_dir, log, final, followups):
    messages.append({"role": "assistant", "content": final or ""})
    results = []
    for q in followups:
        messages.append({"role": "user", "content": q["question"]})
        t0 = time.perf_counter()
        answer, _ = agent_turn(gc, messages, files, out_dir, log)
        missing = check_followup(answer, q["must_contain"], q.get("must_not_contain", ()))
        results.append(
            {
                "question": q["question"],
                "answer": answer,
                "passed": not missing,
                "missing": missing,
                "time_s": round(time.perf_counter() - t0, 2),
            }
        )
        messages.append({"role": "assistant", "content": answer or ""})
    return results


def one_run(gc, files, out_dir, mode, followups=None):
    os.makedirs(out_dir, exist_ok=True)
    log = {"mode": mode, "model": gc.model, "files": files, "steps": []}
    t0 = time.perf_counter()
    messages = [*system_messages(), user_message(files)]
    script_time = 0.0
    final = None
    if mode == "simple":
        res, script_time = run_skill(files, out_dir)
        log["steps"].append({"tool": FUNCTION["name"], "args": {"files": files}, "result": res})
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "function_call": {"name": FUNCTION["name"], "arguments": {"files": files}},
            }
        )
        messages.append({"role": "function", "name": FUNCTION["name"], "content": json.dumps(res, ensure_ascii=False)})
        resp = gc.chat(messages)
        final = resp["choices"][0]["message"].get("content", "")
        log["steps"].append({"model": resp["choices"][0]["message"], "usage": resp.get("usage")})
    else:
        final, script_time = agent_turn(gc, messages, files, out_dir, log)
    total = time.perf_counter() - t0
    if followups and mode == "agent":
        log["followups"] = run_followups(gc, messages, files, out_dir, log, final, followups)
        log["followups_passed"] = sum(f["passed"] for f in log["followups"])
        log["followups_total"] = len(log["followups"])
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from import_harness_runs import check_answer, template_from_stdout

    stdout = next((st["result"]["stdout"] for st in log["steps"] if st.get("tool") == FUNCTION["name"]), "")
    exact, leaked = check_answer(final or "", template_from_stdout(stdout))
    log.update(
        {
            "final_answer": final,
            "time_total_s": round(total, 2),
            "time_script_s": round(script_time, 2),
            "time_llm_s": round(total - script_time, 2),
            "answer_matches_template": exact,
            "answer_has_service_markers": leaked,
        }
    )
    with open(os.path.join(out_dir, "log.json"), "w", encoding="utf-8") as fh:
        json.dump(log, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "transcript.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# Прогон GigaChat ({gc.model}, режим {mode})\n\n**Пользователь:** (пустое сообщение) + вложения:\n")
        fh.write("\n".join(f"- `{os.path.basename(f)}`" for f in files) + "\n\n")
        for st in log["steps"]:
            if st.get("tool") == FUNCTION["name"]:
                fh.write(
                    f"**Вызов навыка** `{st['tool']}` (код выхода {st['result']['exit_code']})\n\n```\n{st['result']['stdout']}\n```\n\n"
                )
        fh.write(
            f"**Ответ агента:**\n\n```text\n{final}\n```\n\n---\nВремя: всего {total:.1f} с (скрипт {script_time:.2f} с).\n"
        )
        for f in log.get("followups", []):
            mark = "✅" if f["passed"] else "❌ не хватает: " + "; ".join(f["missing"])
            fh.write(f"\n**Пользователь:** {f['question']}\n\n**Агент:** {f['answer']}\n\n{mark} ({f['time_s']} с)\n")
    return log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", help="папка с вложениями или файл")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--mode", choices=("agent", "simple"), default="agent")
    ap.add_argument("--model", default=os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Max"))
    ap.add_argument("--scope", default=os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"))
    ap.add_argument("--base-url", default=os.environ.get("GIGACHAT_BASE_URL", DEFAULT_BASE))
    ap.add_argument("--ca-bundle", default=os.environ.get("GIGACHAT_CA_BUNDLE"))
    ap.add_argument("--insecure", action="store_true", help="не проверять TLS-сертификат (только для теста)")
    ap.add_argument("--expected", help="expected.json — посчитать метрики после каждого прогона")
    ap.add_argument("--runs-dir", default=os.path.join(ROOT, "runs"))
    ap.add_argument(
        "--followups",
        help="JSON с уточняющими вопросами после распределения (по умолчанию <набор>/followups.json, если есть)",
    )
    ap.add_argument("--no-followups", action="store_true", help="не задавать уточняющие вопросы")
    a = ap.parse_args()

    cred = os.environ.get("GIGACHAT_CREDENTIALS")
    if not cred:
        sys.exit("Задайте переменную окружения GIGACHAT_CREDENTIALS (ключ авторизации из developers.sber.ru).")
    if a.insecure:
        ctx = ssl._create_unverified_context()
    else:
        ctx = ssl.create_default_context(cafile=a.ca_bundle) if a.ca_bundle else ssl.create_default_context()
    gc = GigaChat(cred, a.scope, a.base_url, ctx, a.model)

    files = list_attachments(a.inputs)
    ds = (
        os.path.basename(os.path.dirname(os.path.abspath(a.inputs)))
        if os.path.basename(a.inputs.rstrip("/")) == "input"
        else "custom"
    )
    fu_path = a.followups or os.path.join(os.path.dirname(os.path.abspath(a.inputs)), "followups.json")
    followups = None
    if not a.no_followups and os.path.exists(fu_path):
        followups = json.loads(Path(fu_path).read_text(encoding="utf-8"))["questions"]
    summary = []
    for i in range(1, a.runs + 1):
        out = os.path.join(a.runs_dir, ds, f"gigachat_{a.mode}_{i}")
        log = one_run(gc, files, out, a.mode, followups)
        row = {
            "run": i,
            "time_total_s": log["time_total_s"],
            "time_llm_s": log["time_llm_s"],
            "called_skill": any(s.get("tool") == FUNCTION["name"] for s in log["steps"]),
        }
        res_path = os.path.join(out, "distribution_result.xlsx")
        if a.expected and os.path.exists(res_path):
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            from evaluate import evaluate

            m = evaluate(a.expected, res_path)
            row.update({k: m[k] for k in ("full_match_rate", "cloud_form_ok", "invented_values", "content_hash")})
        # ответ модели = шаблон скрипта, без служебных строк? (модель ничего не исказила)
        row["answer_ok"] = log["answer_matches_template"] and not log["answer_has_service_markers"]
        if "followups" in log:
            row["followups"] = f"{log['followups_passed']}/{log['followups_total']}"
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False))
    with open(os.path.join(a.runs_dir, ds, f"gigachat_{a.mode}_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
