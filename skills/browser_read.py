"""Read the web page you are looking at, using the Jarvis Bridge extension in Chrome.

  "Read the last message"      "Summarize this page"      "What does this article say?"
  "Read what I selected"       "What did Claude say?"

The extension hands Jarvis the page's real text, so there is no screenshot and no text reading (OCR) involved.
The text goes only to your own PC: the extension talks to 127.0.0.1 and your local model answers.
Chrome, Edge, Brave and other Chromium browsers work if the extension is loaded in them.
It can't read browser pages like chrome://settings, or the Chrome Web Store.
"""
import re

from openai import OpenAI

import browser_bridge as bridge
from skills import tool

LM_URL = "http://localhost:1234/v1"
MODEL = "local-model"
TIMEOUT = 90
HEAD_CHARS = 12000          # how much of a page is given to the model for summaries and questions
TAIL_CHARS = 5000           # how much of the END of a page is used to find the latest message
MAX_READ_CHARS = 1200       # the longest message he will read out loud

OFFLINE = "I can't reach your browser, sir. Is the Jarvis Bridge extension switched on in Chrome?"
OUTDATED = "My browser extension is out of date, sir. Please reload it in Chrome."

_client = OpenAI(base_url=LM_URL, api_key="lm-studio", timeout=TIMEOUT)

SPOKEN = ("You are Jarvis, answering out loud. Reply in one to three short spoken sentences. "
          "No lists, markdown, or emojis. Never invent anything that isn't in the text. Address the user as 'sir'.")

READ_LAST = ("You are Jarvis, reading a message from a web page out loud. The text below is the END of the page, "
             "so it may contain buttons, menus, and input boxes such as 'Copy', 'Retry', 'Reply', 'Type a message', "
             "and timestamps. Find the most recent message (the last real message, usually near the bottom) and read "
             "it back word for word. Do not summarize or explain it. Leave out buttons, menus, timestamps, and "
             "disclaimers. If it is long, read all of it. If there is no message, say so in one short sentence.")


def _ask(system, prompt, max_tokens):
    reply = _client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return (reply.choices[0].message.content or "").strip()


def _clean(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"[*_`#>]+", "", text)
    return " ".join(text.split())


_SELECTION = re.compile(r"\b(selected|selection|highlighted|i highlighted|i selected)\b", re.IGNORECASE)
_LAST = re.compile(r"\b(last|latest|newest|most recent|recent)\b.*\b(message|reply|response|answer|post|comment|email)\b"
                   r"|\bwhat did \w+ (say|reply|write|answer)\b|\bread (the )?(message|reply|response|answer)\b",
                   re.IGNORECASE)
_SUMMARY = re.compile(r"\b(summar\w+|about|tl;?dr|gist|overview|main points?)\b", re.IGNORECASE)


def read_page(question=""):
    question = (question or "").strip() or "What is this page about?"
    try:
        reply = bridge.request("page_text")
    except Exception:
        return OFFLINE
    error = reply.get("error")
    if error:
        if "unknown action" in str(error):
            return OUTDATED
        if error == "no_tab":
            return "I don't see an active browser tab, sir."
        reason = str(reply.get("reason", "")).lower()
        if "permission" in reason or "host" in reason:
            return "I'm not allowed to read that page, sir. The extension needs permission for all websites."
        return "Chrome doesn't let me read that page, sir."

    title = (reply.get("title") or "").strip()
    text = (reply.get("text") or "").strip()
    selection = (reply.get("selection") or "").strip()
    head = f"The page is titled: {title}.\n" if title else ""

    try:
        if selection and _SELECTION.search(question):
            return _clean(selection)[:MAX_READ_CHARS]          # you highlighted it, so read exactly that

        if not text:
            return "That page has no readable text, sir."

        if _LAST.search(question):
            answer = _clean(_ask(READ_LAST, f"{head}Text from the end of the page:\n{text[-TAIL_CHARS:]}", 500))
            return answer[:MAX_READ_CHARS] or "I couldn't find a message on that page, sir."

        if _SUMMARY.search(question):
            prompt = f"{head}Page text:\n{text[:HEAD_CHARS]}\n\nSummarize this page for the user in two or three sentences."
        else:
            prompt = f"{head}Page text:\n{text[:HEAD_CHARS]}\n\nThe user asks: {question}"
        return _clean(_ask(SPOKEN, prompt, 260)) or "I couldn't make sense of that page, sir."
    except Exception as e:
        print("[browser_read] the model didn't answer:", e)
        return "My language model didn't answer, sir."


HINT = ("Web page: when the user asks to read, summarize, or ask about the web page, article, chat, or message they "
        "are looking at IN THE BROWSER, or to read what they selected, use read_page and pass their question as they "
        "said it. For anything that is not a web page, use look_at_screen.")

SCHEMAS = [
    tool("read_page",
         "Read the web page the user is looking at in the browser: read the latest message or reply on it, "
         "summarize it, read the text they selected, or answer a question about it.",
         {"question": "What the user wants, in their own words"},
         optional=("question",)),
]

TOOLS = {
    "read_page": read_page,
}