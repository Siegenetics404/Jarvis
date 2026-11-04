"""Plain commands that don't need the language model.

"open discord", "close spotify", "launch visual studio code", "open the luxara project",
"pause the music", "resume", "next song", "play the music in Spotify", "what's on my screen"

The local model sometimes answers "Closing discord." in words without actually calling the tool, or asks you a
question instead of acting. For these simple commands the program can do it by itself: it checks the name against
the apps and projects that really exist on your PC and calls the tool directly. It is also faster, because the
model isn't involved. Anything it isn't sure about goes to the model as before.
"""
import json
import re

import learning
import session_memory

OPEN_VERBS = {"open", "launch", "start", "run"}
CLOSE_VERBS = {"close", "quit", "exit", "kill"}

_LEADING = {"the", "my", "this", "that", "up", "a", "an", "please", "me", "for", "to", "open"}
_TRAILING = {"app", "application", "program", "please", "now", "again", "sir", "jarvis", "project", "folder"}
_PRONOUNS = {"it", "that", "this", "them", "everything", "all", "these", "those"}
_NOT_AN_APP = {"tab", "tabs", "window", "windows", "browser", "page", "pages", "website", "site", "video"}
_WEB_NAMES = {"youtube", "google", "gmail", "facebook", "github", "netflix", "twitter", "instagram", "tiktok",
              "reddit", "chatgpt", "claude", "wikipedia", "amazon", "maps", "drive", "linkedin"}

ROUTE_AT = 0.70            # how alike the name must be to an installed app or project
ROUTE_GAP = 0.12           # and how much better than the runner-up, otherwise the model decides


# Code: "write a python function that...", "explain this code", "how do I validate an email in laravel"
_CODE_VERBS = {"write", "create", "generate", "implement", "build", "code", "fix", "debug", "refactor", "optimize",
               "optimise", "convert", "translate", "review", "explain", "comment", "test", "rewrite", "make"}
_CODE_NOUNS = {"function", "functions", "script", "scripts", "class", "method", "query", "regex", "component",
               "endpoint", "algorithm", "code", "program", "bug", "snippet", "loop", "array", "variable", "migration",
               "controller", "route", "hook", "api", "schema", "unittest", "unit"}
_LANGUAGES = {"python", "javascript", "typescript", "php", "laravel", "react", "vue", "sql", "mysql", "postgres",
              "html", "css", "java", "rust", "golang", "node", "nodejs", "bash", "powershell", "tailwind", "inertia",
              "django", "flask", "regex", "jquery", "kotlin", "swift", "ruby", "c++", "csharp"}
_NOT_CODE = {"email", "emails", "mail", "reminder", "alarm", "playlist", "song", "music", "tab", "tabs", "message",
             "note", "notes", "timer", "folder", "file", "meeting", "event", "calendar", "list", "shopping", "poem",
             "story", "letter", "essay", "caption", "post", "tweet"}
_CODE_PHRASES = ("explain this code", "explain this function", "explain this script", "what does this code",
                 "what does this function", "what does this script", "review this code", "review my code",
                 "fix this code", "fix my code", "fix this function", "fix this script", "debug this code",
                 "debug my code", "debug this function", "optimize this code", "refactor this", "convert this code",
                 "comment this code", "add comments to this code", "write tests for this", "find the bug")


def _code_call(words, text):
    ws = set(words)
    phrase = " ".join(words)
    if ws & _NOT_CODE and not ws & {"code", "function", "script", "regex", "query"} and not ws & _LANGUAGES:
        return None
    if phrase.startswith(_CODE_PHRASES):
        return "code_helper", json.dumps({"request": text})
    if words[0] in _CODE_VERBS and (ws & _CODE_NOUNS or ws & _LANGUAGES) and len(words) >= 4:
        return "code_helper", json.dumps({"request": text})
    if phrase.startswith(("how do i", "how can i", "how to", "how would i", "how should i")) and \
            ws & _LANGUAGES and len(words) >= 5:
        return "code_helper", json.dumps({"request": text})
    return None


# Model: "which model are you using?", "change the model", "load a model"
_MODEL_STATUS = ("which model are you", "what model are you", "which model is loaded", "what model is loaded",
                 "which model is running", "what model is running", "what model do you use", "which model do you use",
                 "what is your model", "whats your model", "what brain are you", "which brain are you",
                 "what are you running on", "which model am i")
_MODEL_OPEN = ("change the model", "change model", "change your model", "change your brain", "switch the model",
               "switch model", "switch your model", "switch models", "load a model", "load another model",
               "load a different model", "load model", "load the model", "open model settings",
               "open the model settings", "model settings", "choose a model", "pick a model", "select a model",
               "open the model window")


def _model_call(words):
    phrase = " ".join(words)
    if phrase.startswith(_MODEL_STATUS):
        return "model_status", "{}"
    if phrase.startswith(_MODEL_OPEN):
        return "open_model_settings", "{}"
    return None


# Email: "email Maria that I'll be late", "send it", "do I have new emails?", "read the first one"
_DRAFT_STARTS = ("send an email", "send a email", "send email", "draft an email", "draft a email", "draft email",
                 "write an email", "write a email", "write email", "compose an email", "compose email")
_REVISE_VERBS = {"change", "make", "add", "remove", "rewrite", "shorten", "lengthen", "fix", "replace", "edit",
                 "update", "include", "mention", "reword", "rephrase", "delete", "swap"}
_REVISE_TARGETS = {"it", "email", "draft", "subject", "greeting", "ending", "tone", "shorter", "longer", "formal",
                   "casual", "polite", "friendlier", "that", "line", "sentence", "body", "sign", "signoff"}


def _email_module(need_account=True):
    try:
        import importlib
        module = importlib.import_module("skills.email_assistant")
        return module if (not need_account or module.configured()) else None
    except Exception:
        return None


_SETUP_PHRASES = ("set up my email", "setup my email", "set up email", "setup email", "set up my gmail",
                  "set up my mail", "email settings", "open email settings", "open my email settings",
                  "connect my email", "connect my gmail", "connect email", "change my email", "change my email account",
                  "change my email settings", "add my email", "add my email account", "configure my email",
                  "configure email", "email setup", "log in to my email", "login to my email")


def _email_call(words, text):
    ws = set(words)
    phrase = " ".join(words)
    status_words = ws & {"connected", "connection", "connect", "working", "successfully", "status", "works",
                         "linked", "logged"} or phrase.startswith(("test my email", "test my gmail", "test email",
                                                                   "test the email"))
    if ws & {"email", "gmail", "mail", "inbox"} and status_words and \
            phrase.startswith(("is my", "is the", "is gmail", "is email", "did my", "does my", "are my", "am i", "check",
                               "test", "how is", "what is the status", "whats the status", "what's the status",
                               "has my", "do i")):
        if _email_module(need_account=False) is not None:           # also works before an account is connected
            return "email_status", "{}"
    if phrase.startswith(_SETUP_PHRASES) or phrase in {"set up email", "setup email"}:
        if _email_module(need_account=False) is not None:           # works before any account is connected
            return "setup_email", "{}"
    mail = _email_module()
    if mail is None:
        return None

    # Contacts: "save the sender as a contact", "save the copied email as Maria", "remove Maria from my contacts"
    if ws & {"contact", "contacts"} or phrase.startswith(("save the sender", "save this sender", "save the copied",
                                                          "add the copied", "save the email address",
                                                          "save them as", "save him as", "save her as",
                                                          "add the sender")):
        if phrase.startswith(("who are my", "list my", "show my", "read my", "what are my", "what contacts",
                              "how many contacts", "do i have any contacts", "who is in my")):
            return "list_contacts", "{}"
        if words[0] in {"remove", "delete", "forget"}:
            return "remove_contact", json.dumps({"request": text})
        if words[0] in {"add", "save", "make", "put"}:
            return "save_contact", json.dumps({"request": text})

    if phrase.startswith(_DRAFT_STARTS) or (words[0] == "email" and len(words) >= 3):
        return "draft_email", json.dumps({"request": text})
    if mail.has_read_email() and phrase.startswith(("reply to this email", "reply to that email", "reply to the email",
                                                    "reply to him", "reply to her", "reply saying", "reply that",
                                                    "reply with", "respond to this email", "respond to that email",
                                                    "write back")):
        return "reply_email", json.dumps({"request": text})

    if mail.drafting():
        if phrase in {"send it", "send the email", "send that", "send this email", "send the draft", "yes send it",
                      "go ahead and send it", "send it now", "send it please", "please send it", "send this"}:
            return "send_email", "{}"
        if phrase.startswith(("save it as a draft", "save the draft", "save this draft", "save it to drafts",
                              "save as draft", "save it as draft")):
            return "save_draft", "{}"
        if phrase.startswith(("discard the draft", "delete the draft", "discard it", "forget the draft",
                              "forget the email", "cancel the email", "cancel the draft", "throw away the draft",
                              "discard the email", "scrap the draft", "scrap it")):
            return "discard_draft", "{}"
        if phrase.startswith(("read the draft", "read it back", "read that back", "read the email back",
                              "read it again", "what does the draft say")):
            return "read_draft", "{}"
        if words[0] in _REVISE_VERBS and (ws & _REVISE_TARGETS or phrase.startswith(("add that", "mention that",
                                                                                    "include that"))):
            return "revise_draft", json.dumps({"instruction": text})

    sure_words = ws & {"spam", "junk", "trash", "drafts", "snoozed"}                    # only ever mean mail folders
    maybe_words = ws & {"bin", "important", "starred", "flagged", "sent"}               # need a mail word as well
    mail_context = ws & {"email", "emails", "mail", "mails", "inbox", "messages", "folder"}
    folder_word = sure_words or (maybe_words and mail_context) or "all mail" in phrase
    if folder_word and phrase.startswith(("how many", "do i have", "is there", "are there", "any", "check", "what",
                                          "whats", "show", "read", "list", "open", "tell me", "did i", "anything",
                                          "empty", "delete", "clear", "remove")) \
            and not ws & {"send", "write", "compose", "reply", "play", "set"}:
        return "check_folder", json.dumps({"where": text})
    if re.search(r"\b(did i send|have i sent|i sent|did i email|have i emailed|who did i email|what have i sent)\b",
                 phrase):
        return "check_folder", json.dumps({"where": text})
    mailbox = ws & {"email", "emails", "mail", "inbox"}
    if mailbox and "this" not in ws:
        asks = phrase.startswith(("do i have", "any new", "any unread", "check my", "check the", "how many", "read my",
                                  "show my", "show me my", "list my", "what are my latest", "my latest",
                                  "what are my", "whats in my", "what's in my", "tell me my", "what new",
                                  "what emails", "whats new in my"))
        if asks and not ws & {"send", "write", "draft", "compose", "reply"}:
            return "read_emails", json.dumps({"category": text})
        if words[0] in {"read", "open"} and ws & ({"from", "first", "second", "third", "fourth", "fifth", "latest",
                                                   "newest", "number", "about", "full", "whole"}):
            return "read_email", json.dumps({"which": text})
    categories = ws & {"primary", "social", "promotions", "promotion", "promotional", "promos", "updates", "forums"}
    if categories and not mailbox and phrase.startswith(("check", "any", "do i have", "read my", "how many",
                                                         "what", "whats", "anything", "show", "tell me")):
        return "read_emails", json.dumps({"category": text})
    if ("categories" in ws or "category" in ws) and ws & {"email", "emails", "mail", "unread", "inbox"}:
        return "read_emails", json.dumps({"category": text})
    if words[0] == "read" and ws & {"first", "second", "third"} and ws & {"one", "email", "mail"} and "this" not in ws:
        return "read_email", json.dumps({"which": text})
    if phrase in {"read it in full", "read the whole email", "read the full email", "read it all", "read the whole thing",
                  "read the full thing"} and mail.has_read_email():
        return "read_email", json.dumps({"which": "full"})
    return None


# "What's on my screen?", "read this error", "what am I looking at?"
_SCREEN_ASK = {"what", "whats", "what's", "read", "look", "see", "describe", "explain", "summarize", "summarise",
               "tell", "check", "show", "scan"}


_BROWSER_TITLE = re.compile(r"\s[-\u2013\u2014]\s(google chrome|microsoft edge|brave|opera|vivaldi)\b", re.IGNORECASE)
_PAGE_STARTS = ("read this page", "read the page", "summarize this page", "summarise this page",
                "summarize this article", "summarise this article", "read this article",
                "what does this page say", "what is this page about", "whats this page about",
                "what is on this page", "whats on this page", "what is this article about",
                "what does this article say", "read what i selected", "read the selected", "read my selection",
                "read what i highlighted", "what did claude say", "read claude")
_MESSAGE_WORDS = {"message", "messages", "email", "emails", "chat", "notification", "notifications",
                  "reply", "response", "answer"}


def _foreground_title():
    """The title of the window you are using, so he knows if you are in a web browser."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        buffer = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(handle) + 1)
        user32.GetWindowTextW(handle, buffer, len(buffer))
        return buffer.value
    except Exception:
        return ""


def _reader(question):
    """Reading something you are looking at: the page text from the browser, or the screen if it isn't a browser."""
    tool = "read_page" if _BROWSER_TITLE.search(_foreground_title()) else "look_at_screen"
    return tool, json.dumps({"question": question})


def _screen_call(words, text):
    ws = set(words)
    phrase = " ".join(words)
    if "screen" in ws and ws & _SCREEN_ASK and not ws & {"record", "recording", "brightness", "shot", "screenshot",
                                                            "lock", "off", "turn", "mirror", "share", "saver"}:
        return "look_at_screen", json.dumps({"question": text})              # the whole screen, always
    if phrase.startswith(_PAGE_STARTS):
        return _reader(text)
    if ws & _MESSAGE_WORDS and \
            (words[0] == "read" or phrase.startswith(("what does", "what do", "what is the", "whats the", "what's the"))) \
            and not ws & {"send", "write", "reply", "delete", "open", "close", "set", "remind", "play", "tell"}:
        return _reader(text)
    if phrase.startswith(("what am i looking at", "what do you see", "what can you see", "what does this say",
                          "what does this error say")):                       # "explain this error" stays with the clipboard skill
        return _reader(text)
    return None


# Pause, resume, next and previous song, and "play the music in Spotify"
_MEDIA_FILLER = {"the", "my", "your", "this", "that", "current", "currently", "playing", "music", "song", "songs",
                 "track", "tracks", "playback", "audio", "video", "it", "please", "sir", "jarvis", "now", "in", "on",
                 "spotify", "yes", "yeah", "yep", "just", "a", "one", "then", "go", "to"}
_MUSIC_WORDS = {"music", "song", "songs", "track", "tracks", "playback", "audio", "video", "spotify", "it"}
_PAUSE_WORDS = {"pause", "resume", "unpause", "continue"}
_PAUSE_MISHEARD = {"post", "pose", "paws", "pours", "paus", "watch", "pass"}   # what Whisper sometimes hears for "pause"


def _media_call(words):
    ws = set(words)
    content = [w for w in words if w not in _MEDIA_FILLER or w == "go"]
    named = bool(ws & _MUSIC_WORDS)
    if len(content) == 1 and content[0] in _PAUSE_WORDS:
        return "media_control", json.dumps({"action": "play_pause"})
    if len(content) == 1 and content[0] in _PAUSE_MISHEARD and ws & (_MUSIC_WORDS - {"it", "video"}):
        return "media_control", json.dumps({"action": "play_pause"})
    if content == ["play"] and "spotify" in ws:
        return "play_spotify", "{}"
    last = session_memory.last_action()
    if last and last["tool"] == "play_youtube":                   # skipping inside YouTube is the YouTube tool's job
        return None
    wants_song = bool(ws & {"song", "songs", "track", "tracks", "music"})
    if content in (["next"], ["play", "next"]) and named:
        return "media_control", json.dumps({"action": "next"})
    if content == ["skip"] and wants_song:
        return "media_control", json.dumps({"action": "next"})
    if content in (["previous"], ["last"]) and wants_song:
        return "media_control", json.dumps({"action": "previous"})
    if content == ["go", "back"] and wants_song:
        return "media_control", json.dumps({"action": "previous"})
    return None


def _best(heard, names):
    top = learning.ranked(heard, names)[:2]
    if not top:
        return None, 0.0, 0.0
    second = top[1][0] if len(top) > 1 else 0.0
    return top[0][1], top[0][0], second


def quick_call(text):
    """Returns (tool, arguments_json) for a simple open/close command, or None to let the model decide."""
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if not words:
        return None
    model = _model_call(words)
    if model:
        return model
    email = _email_call(words, text)          # emails can be long ("email Maria that I'll be late and ask...")
    if email:
        return email
    code = _code_call(words, text)            # code requests are long too
    if code:
        return code
    if len(words) > 9:
        return None
    screen = _screen_call(words, text)
    if screen:
        return screen
    media = _media_call(words)
    if media:
        return media
    if len(words) < 2:
        return None
    verb, rest = words[0], words[1:]
    if verb in OPEN_VERBS:
        action = "open"
    elif verb in CLOSE_VERBS:
        action = "close"
    else:
        return None

    while rest and rest[0] in _LEADING:
        rest = rest[1:]
    while rest and rest[-1] in _TRAILING:
        rest = rest[:-1]
    if not rest or len(rest) > 4:
        return None
    if set(rest) & (_PRONOUNS | _NOT_AN_APP | {"and", "then", "also", "with", "on", "in"}):
        return None
    heard = " ".join(rest)

    # A name he learned earlier ("odo rico" means Odoriko)
    learned = learning._learned["apps"].get(heard) or (learning._learned["projects"].get(heard) if action == "open" else None)
    if learned:
        heard = learned

    apps = learning._candidates("apps")
    projects = learning._candidates("projects") if action == "open" else []
    if heard in apps:                                              # exact name: no doubt at all
        return ("open_app" if action == "open" else "close_app"), json.dumps({"name": heard})
    if heard in _WEB_NAMES:
        return None                                                # probably a website, let the model decide
    if len(heard) >= 4:                                            # part of exactly one app name: "cord" in "discord"
        inside = [name for name in apps if heard in name]
        if len(inside) == 1:
            return ("open_app" if action == "open" else "close_app"), json.dumps({"name": inside[0]})

    app, app_score, app_next = _best(heard, apps)
    project, project_score, project_next = _best(heard, projects)
    if action == "open" and project and project_score >= ROUTE_AT and project_score - app_score >= ROUTE_GAP \
            and project_score - project_next >= ROUTE_GAP:
        return "open_project", json.dumps({"name": project})
    if app and app_score >= ROUTE_AT and app_score - app_next >= ROUTE_GAP and app_score - project_score >= ROUTE_GAP:
        return ("open_app" if action == "open" else "close_app"), json.dumps({"name": app})
    return None