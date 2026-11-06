"""Coding help by voice. Code requests go to your coder model, not the fast conversation model.

  "Write a Python function that reverses a string"          "Fix the bug in this function"  (copy it first)
  "Explain this code"  (copy it first)                      "How do I validate an email in Laravel?"

What happens:
  - A coder model loaded in LM Studio is used automatically. With none loaded, the main model does it instead.
  - If the first model fails, the next one in the list tries (see jarvis_llm.py).
  - He says a short explanation out loud. The code itself is copied to your clipboard, so you can paste it, and the
    full answer is saved in data/last_code_answer.md and printed in the terminal.
  - "this code" or "this function" means what is on your clipboard.

This is the first step toward a full coding assistant. For now it only answers and never edits your files.
"""
import re
import subprocess
from pathlib import Path

import jarvis_llm as llm
from skills import tool

ROOT = Path(__file__).resolve().parent.parent
ANSWER_FILE = ROOT / "data" / "last_code_answer.md"
MAX_CLIPBOARD = 8000            # how much copied code is given to the model
SPOKEN_CHARS = 320              # how much of the explanation he says out loud
EXPLAIN_CHARS = 560             # when you only asked him to explain, he says more and copies nothing
CODE_TIMEOUT = 300              # a coder on a laptop can take a while

SYSTEM = ("You are an expert programmer helping the user by voice. Reply in this exact shape: first one or two short "
          "plain sentences saying what you did or what the answer is, with no code in them. Then the complete code in "
          "ONE fenced code block with the language name. If the question needs no code, answer in three sentences "
          "at most and write no code block. Be correct and concise. Never invent functions that do not exist.")

_noted_fallback = False         # say "no coding model is loaded" only once per session


def _clipboard_text():
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True,
                                text=True, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.stdout
    except Exception:
        return ""


def _set_clipboard(text):
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "[Console]::InputEncoding=[Text.Encoding]::UTF8; $input | Set-Clipboard"],
                       input=text, text=True, encoding="utf-8", timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False


_EXPLAIN_ONLY = re.compile(r"^\W*(explain|what does|what is|what's|why|review|describe|summari[sz]e|how does|"
                           r"is this|are there any)\b", re.IGNORECASE)

_MENTIONS_COPIED = re.compile(r"\b(this|that|my|the|copied|clipboard)\b.*\b(code|function|script|snippet|class|query|"
                              r"file|component|method|bug|error)\b|\b(clipboard|copied)\b", re.IGNORECASE)


def _split(answer):
    """(explanation without code, the first code block or '')."""
    answer = re.sub(r"<think>.*?</think>", "", answer or "", flags=re.DOTALL).strip()
    blocks = re.findall(r"```[a-zA-Z0-9+#_.-]*\n(.*?)```", answer, flags=re.DOTALL)
    explanation = re.sub(r"```.*?```", " ", answer, flags=re.DOTALL)
    explanation = re.sub(r"[*_`#>]+", "", explanation)
    return " ".join(explanation.split()), (blocks[0].rstrip() if blocks else "")


def _first_sentences(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:stop + 1] if stop > 40 else cut.rsplit(" ", 1)[0]).strip()


def code_helper(request=""):
    global _noted_fallback
    request = (request or "").strip()
    if not request:
        return "What would you like me to code, sir?"

    pasted = ""
    if _MENTIONS_COPIED.search(request):
        pasted = _clipboard_text().strip()[:MAX_CLIPBOARD]
        if not pasted and re.search(r"\b(this|my|copied|clipboard)\b", request, re.IGNORECASE):
            return "I don't see any code on your clipboard, sir. Copy it first."

    prompt = request if not pasted else f"{request}\n\nThe code the user copied:\n```\n{pasted}\n```"
    try:
        response, used = llm.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                                      role="code", data="code", max_tokens=2000, temperature=0.2,
                                      timeout=CODE_TIMEOUT)
    except llm.LMStudioDown:
        return "I can't reach LM Studio, sir. Is its server running?"
    except llm.ModelError as e:
        print("[coding] no model answered:", e)
        return llm.problem() or "None of my models answered that, sir."

    answer = (response.choices[0].message.content or "").strip()
    explanation, code = _split(answer)
    print(f"\n[coding] answered by {used}:\n{answer}\n")
    try:
        ANSWER_FILE.parent.mkdir(parents=True, exist_ok=True)
        ANSWER_FILE.write_text(f"# {request}\n\n(answered by {used})\n\n{answer}\n", encoding="utf-8")
    except OSError:
        pass

    if _EXPLAIN_ONLY.match(request):                          # you asked about code, so don't replace what you copied
        spoken = _first_sentences(explanation, EXPLAIN_CHARS) or "Here you go, sir."
        if code:
            spoken += " The full answer is in the terminal."
    else:
        spoken = _first_sentences(explanation, SPOKEN_CHARS) or "Here you go, sir."
        if code and _set_clipboard(code):
            spoken += " The code is on your clipboard, sir."
        elif code:
            spoken += " The code is in the terminal and in data slash last code answer dot m d."
    if llm.last_used.get("online"):
        spoken += f" {used.replace(' (online)', '')} answered that online."
    elif not llm.is_coder(used) and not _noted_fallback:
        _noted_fallback = True
        spoken += " No coding model is loaded, so I used my main model."
    return spoken


HINT = ("Coding: for any programming request (write, fix, explain, review, convert or debug code, or a how-to in a "
        "programming language) use code_helper and pass the user's whole request as they said it. It hands the work "
        "to a coding model. When they say 'this code' they mean what is on their clipboard.")

SCHEMAS = [
    tool("code_helper", "Write, fix, explain or review code by handing the request to a coding model. Use it for "
         "programming questions and code requests.",
         {"request": "The user's whole request, in their own words"}),
]

TOOLS = {
    "code_helper": code_helper,
}