"""Email settings and contacts, shared by the email skill and the setup window (email_setup.py).

Nobody has to edit files by hand: the setup window and voice commands write these for you.

  .env                 your account details (address, name, servers). Safe to share? No: keep it private.
  Windows Credential   your app password is kept here when the 'keyring' package is installed (pip install keyring).
  Manager              Without it, the password goes in .env instead.
  data/contacts.txt    the people he may email, one per line:  name = address

Developers can still edit .env and contacts.txt directly. Values in .env win.
"""
import imaplib
import re
import smtplib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
CONTACTS_FILE = ROOT / "data" / "contacts.txt"
KEYRING_SERVICE = "jarvis-email"

# What the setup window offers. Port 465 uses SSL straight away, 587 starts plain and upgrades (STARTTLS).
PROVIDERS = {
    "Gmail": {"imap": "imap.gmail.com", "smtp": "smtp.gmail.com", "port": 465,
              "help": "https://myaccount.google.com/apppasswords",
              "note": "Turn on 2-Step Verification, then create an App password (16 letters)."},
    "Yahoo": {"imap": "imap.mail.yahoo.com", "smtp": "smtp.mail.yahoo.com", "port": 465,
              "help": "https://login.yahoo.com/account/security",
              "note": "Create an app password under Account security."},
    "iCloud": {"imap": "imap.mail.me.com", "smtp": "smtp.mail.me.com", "port": 587,
               "help": "https://appleid.apple.com",
               "note": "Create an app-specific password in your Apple ID settings."},
    "Other": {"imap": "", "smtp": "", "port": 465, "help": "",
              "note": "Ask your email provider for the IMAP and SMTP server names. "
                      "Personal Outlook and Hotmail accounts don't allow this kind of login."},
}


# ------------------------------------------------------------------ the .env file

def read_env():
    values = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def write_env(updates, remove=()):
    """Change some keys in .env and keep everything else (comments, other settings) exactly as it was."""
    lines = []
    try:
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = ["# Jarvis settings. Private: never share or upload this file."]
    done = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in remove:
            continue
        if key in updates:
            out.append(f"{key}={updates[key]}")
            done.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in done:
            out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


# ------------------------------------------------------------------ the password

def _keyring():
    try:
        import keyring
        return keyring
    except Exception:
        return None


def get_password(address, env=None):
    env = env if env is not None else read_env()
    if env.get("EMAIL_APP_PASSWORD"):
        return env["EMAIL_APP_PASSWORD"].replace(" ", "")
    store = _keyring()
    if store and address:
        try:
            return (store.get_password(KEYRING_SERVICE, address) or "").replace(" ", "")
        except Exception:
            pass
    return ""


def set_password(address, password):
    """Keeps the password as safely as it can. Returns where it went: 'keyring' or 'env'."""
    password = (password or "").replace(" ", "")
    store = _keyring()
    if store:
        try:
            store.set_password(KEYRING_SERVICE, address, password)
            write_env({}, remove=("EMAIL_APP_PASSWORD",))          # not in the file as well
            return "keyring"
        except Exception:
            pass
    write_env({"EMAIL_APP_PASSWORD": password})
    return "env"


# ------------------------------------------------------------------ settings

def load():
    """The email settings as a dict, or None when email isn't set up yet."""
    env = read_env()
    address = env.get("EMAIL_ADDRESS", "").strip()
    password = get_password(address, env)
    if not address or not password:
        return None
    imap_host = env.get("EMAIL_IMAP_HOST", "imap.gmail.com").strip()
    try:
        port = int(env.get("EMAIL_SMTP_PORT", "465") or 465)
    except ValueError:
        port = 465
    return {
        "address": address,
        "password": password,
        "name": env.get("EMAIL_NAME", "").strip(),
        "signature": (env.get("EMAIL_SIGNATURE") or env.get("EMAIL_NAME") or "").strip(),
        "imap": imap_host,
        "smtp": env.get("EMAIL_SMTP_HOST", "smtp.gmail.com").strip(),
        "port": port,
        "category": (env.get("EMAIL_DEFAULT_CATEGORY") or
                     ("primary" if "gmail" in imap_host.lower() else "all")).strip().lower(),
    }


def save(address, password, name, signature, imap, smtp, port, category):
    """Used by the setup window. An empty password keeps the one already saved."""
    updates = {"EMAIL_ADDRESS": address.strip(), "EMAIL_NAME": name.strip(),
               "EMAIL_SIGNATURE": (signature or name).strip(), "EMAIL_IMAP_HOST": imap.strip(),
               "EMAIL_SMTP_HOST": smtp.strip(), "EMAIL_SMTP_PORT": str(port).strip(),
               "EMAIL_DEFAULT_CATEGORY": (category or "").strip().lower()}
    write_env({k: v for k, v in updates.items() if v != "" or k == "EMAIL_NAME"})
    return set_password(address.strip(), password) if password else None


def test_connection(address, password, imap, smtp, port):
    """Tries to log in to both servers. Returns (ok, message in plain words). Sends nothing."""
    address, password = address.strip(), (password or "").replace(" ", "")
    if not address or not password:
        return False, "Please fill in your email address and app password."
    try:
        mail = imaplib.IMAP4_SSL(imap.strip(), 993, timeout=20)
        try:
            mail.login(address, password)
        finally:
            try:
                mail.logout()
            except Exception:
                pass
    except imaplib.IMAP4.error:
        return False, ("The login was refused. Use an app password, not your normal password, "
                       "and check the address.")
    except Exception as e:
        return False, f"I couldn't reach the incoming mail server ({imap}): {e}"
    try:
        if int(port) == 587:
            with smtplib.SMTP(smtp.strip(), 587, timeout=20) as server:
                server.starttls()
                server.login(address, password)
        else:
            with smtplib.SMTP_SSL(smtp.strip(), int(port), timeout=20) as server:
                server.login(address, password)
    except smtplib.SMTPAuthenticationError:
        return False, "Reading works, but the sending server refused the login."
    except Exception as e:
        return False, f"Reading works, but I couldn't reach the sending server ({smtp}): {e}"
    return True, "Connected. Reading and sending both work."


# ------------------------------------------------------------------ contacts

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")


def find_address(text):
    match = _EMAIL.search(text or "")
    return match.group(0) if match else None


def read_contacts():
    """[(name as written, address)] in file order."""
    found = []
    try:
        for line in CONTACTS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, address = (part.strip() for part in line.split("=", 1))
            address = address.split(",")[0].strip()
            if name and "@" in address:
                found.append((name, address))
    except OSError:
        pass
    return found


def contacts():
    return {name.lower(): address for name, address in read_contacts()}


def display_names():
    return {name.lower(): name for name, _ in read_contacts()}


def add_contact(name, address):
    """Adds a contact, or changes the address of one with the same name. Returns 'added' or 'updated'."""
    name = " ".join(name.split())
    address = address.strip()
    CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = CONTACTS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = ["# People Jarvis may email. One per line:  name = email address"]
    result = "added"
    for i, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#") and line.split("=", 1)[0].strip().lower() == name.lower():
            lines[i] = f"{line.split('=', 1)[0].strip()} = {address}"      # keep the spelling already saved
            result = "updated"
            break
    else:
        lines.append(f"{name} = {address}")
    CONTACTS_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return result


def remove_contact(name):
    try:
        lines = CONTACTS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    kept = [line for line in lines if not ("=" in line and not line.lstrip().startswith("#")
                                           and line.split("=", 1)[0].strip().lower() == name.strip().lower())]
    if len(kept) == len(lines):
        return False
    CONTACTS_FILE.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    return True