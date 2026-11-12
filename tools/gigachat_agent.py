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
import urllib.request
import uuid

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
            "stdout": {"type": "string", "description": "вывод скрипта, включая готовый ответ для чата"},
        },
    },
}


class GigaChat:
    def __init__(self, credentials, scope, base_url, ctx, model, timeout=60):
        self.credentials, self.scope, self.base, self.ctx = credentials, scope, base_url.rstrip("/"), ctx
        self.model, self.timeout = model, timeout
        self.token, self.exp = None, 0

    def _post(self, url, data, headers):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, context=self.ctx, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

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
    return {
        "exit_code": r.returncode,
        "stdout": r.stdout + (r.stderr[-2000:] if r.stderr else ""),
    }, time.perf_counter() - t0


def list_attachments(path):
    if os.path.isdir(path):
        return [
            os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith((".xlsx", ".xls", ".xlsm"))
        ]
    return [path]


def system_messages():
    sp = open(SYSTEM_PROMPT, encoding="utf-8").read()
    skill = open(os.path.join(SKILL_DIR, "SKILL.md"), encoding="utf-8").read()
    return [{"role": "system", "content": sp + "\n\n# Подключённый навык\n\n" + skill}]


def user_message(files):
    # Текста запроса нет: как в GigaCowork, платформа сообщает модели только о вложениях.
    names = "\n".join(f"- {f}" for f in files)
    return {"role": "user", "content": f"[Вложения пользователя:]\n{names}"}


def one_run(gc, files, out_dir, mode):
    os.makedirs(out_dir, exist_ok=True)
    log = {"mode": mode, "model": gc.model, "files": files, "steps": []}
    t0 = time.perf_counter()
    messages = system_messages() + [user_message(files)]
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
        for _ in range(4):  # не больше 4 шагов модели
            resp = gc.chat(messages, functions=[FUNCTION])
            msg = resp["choices"][0]["message"]
            log["steps"].append(
                {"model": msg, "usage": resp.get("usage"), "finish_reason": resp["choices"][0].get("finish_reason")}
            )
            fc = msg.get("function_call")
            if not fc:
                final = msg.get("content", "")
                break
            args = fc.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            call_files = args.get("files") or files
            res, dt_ = run_skill(call_files, out_dir)
            script_time += dt_
            log["steps"].append({"tool": fc.get("name"), "args": args, "result": res})
            assistant = {"role": "assistant", "content": msg.get("content") or "", "function_call": fc}
            if msg.get("functions_state_id"):
                assistant["functions_state_id"] = msg["functions_state_id"]
            messages.append(assistant)
            messages.append(
                {"role": "function", "name": fc.get("name"), "content": json.dumps(res, ensure_ascii=False)}
            )
    total = time.perf_counter() - t0
    log.update(
        {
            "final_answer": final,
            "time_total_s": round(total, 2),
            "time_script_s": round(script_time, 2),
            "time_llm_s": round(total - script_time, 2),
        }
    )
    with open(os.path.join(out_dir, "log.json"), "w", encoding="utf-8") as fh:
        json.dump(log, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "transcript.md"), "w", encoding="utf-8") as fh:
        fh.write(f"# Прогон GigaChat ({gc.model}, режим {mode})\n\n**Пользователь:** (пустое сообщение) + вложения:\n")
        fh.write("\n".join(f"- `{os.path.basename(f)}`" for f in files) + "\n\n")
        for st in log["steps"]:
            if "tool" in st:
                fh.write(
                    f"**Вызов навыка** `{st['tool']}` (код выхода {st['result']['exit_code']})\n\n```\n{st['result']['stdout']}\n```\n\n"
                )
        fh.write(f"**Ответ агента:**\n\n{final}\n\n---\nВремя: всего {total:.1f} с (скрипт {script_time:.2f} с).\n")
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
    summary = []
    for i in range(1, a.runs + 1):
        out = os.path.join(a.runs_dir, ds, f"gigachat_{a.mode}_{i}")
        log = one_run(gc, files, out, a.mode)
        row = {
            "run": i,
            "time_total_s": log["time_total_s"],
            "time_llm_s": log["time_llm_s"],
            "called_skill": any("tool" in s for s in log["steps"]),
        }
        res_path = os.path.join(out, "distribution_result.xlsx")
        if a.expected and os.path.exists(res_path):
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            from evaluate import evaluate

            m = evaluate(a.expected, res_path)
            row.update({k: m[k] for k in ("full_match_rate", "cloud_form_ok", "invented_values", "content_hash")})
        # ответ модели = шаблон скрипта? (модель ничего не выдумала и не потеряла)
        stdout = next((s["result"]["stdout"] for s in log["steps"] if "tool" in s), "")
        tmpl = stdout.split("=== ОТВЕТ ДЛЯ ЧАТА ===")[-1].split("=== КОНЕЦ ОТВЕТА ===")[0].strip() if stdout else ""
        row["answer_matches_template"] = bool(tmpl) and " ".join(tmpl.split()) in " ".join(
            (log["final_answer"] or "").split()
        )
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False))
    with open(os.path.join(a.runs_dir, ds, f"gigachat_{a.mode}_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
