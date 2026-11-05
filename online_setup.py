"""Online models: add Gemini, Claude, OpenAI and others, and choose what may be sent to them.

This is the "Online models" tab of the model window (model_setup.py). You can also run it on its own:
    python online_setup.py

Nothing here is needed to use Jarvis. With no online model added, everything stays on this PC.
"""
import queue
import re
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import jarvis_cloud as cloud
import jarvis_llm as llm

PRIVACY = [
    ("chat", "Conversation: what you say to him"),
    ("code", "Coding: your coding requests, and code you copied"),
    ("email", "Email: drafting and reading your email"),
    ("screen", "Screen: pictures of your screen and the text on it"),
    ("pages", "Web pages: the text of pages you ask him to read"),
]
ROLES = [("chat", "Conversation and actions"), ("code", "Coding"), ("vision", "Reading pictures")]


def run_in_background(widget, work, done):
    """Runs work() in a thread and calls done(result) on the window's own thread. Tk is never touched by the thread."""
    results = queue.Queue()
    threading.Thread(target=lambda: results.put(work()), daemon=True).start()

    def check():
        try:
            value = results.get_nowait()
        except queue.Empty:
            widget.after(100, check)
            return
        done(value)

    widget.after(100, check)


def slug(text, taken):
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "model"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


class ProviderDialog(tk.Toplevel):
    """Add or edit one online model."""

    def __init__(self, parent, provider=None, on_save=None):
        super().__init__(parent)
        self.title("Online model")
        self.geometry("560x640")
        self.resizable(False, True)
        self.transient(parent.winfo_toplevel())
        self.provider = dict(provider) if provider else None
        self.on_save = on_save
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        kinds = {preset["label"]: kind for kind, preset in cloud.PRESETS.items()}
        self.kind_by_label = kinds
        self.kind = tk.StringVar(value=(cloud.PRESETS[provider["kind"]]["label"] if provider
                                        else cloud.PRESETS["gemini"]["label"]))
        self.name = tk.StringVar(value=provider["name"] if provider else "")
        self.key = tk.StringVar()
        self.model = tk.StringVar(value=provider["model"] if provider else "")
        self.base = tk.StringVar(value=provider.get("base_url", "") if provider else "")
        self.limit = tk.StringVar(value=str(provider.get("daily_limit", cloud.DEFAULT_DAILY_LIMIT)) if provider
                                  else str(cloud.DEFAULT_DAILY_LIMIT))
        self.first = tk.StringVar(value="first" if provider and provider.get("first") else "backup")
        self.role_vars = {role: tk.BooleanVar(value=(role in provider.get("roles", [])) if provider
                                              else role == "chat") for role, _ in ROLES}
        self.status = tk.StringVar()
        self._auto_name = ""                                     # what the window suggested, so it can tell yours apart
        self._auto_model = ""

        row = 0
        ttk.Label(frame, text="Provider").grid(row=row, column=0, sticky="w", pady=4)
        box = ttk.Combobox(frame, textvariable=self.kind, values=list(kinds), state="disabled" if provider else "readonly")
        box.grid(row=row, column=1, sticky="ew", pady=4)
        box.bind("<<ComboboxSelected>>", lambda _e: self.kind_changed())
        row += 1
        self.note = ttk.Label(frame, text="", wraplength=500, foreground="#555")
        self.note.grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        self.key_button = ttk.Button(frame, text="Get an API key...", command=self.open_key_page)
        self.key_button.grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 8))
        row += 1
        ttk.Label(frame, text="Name").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.name).grid(row=row, column=1, sticky="ew", pady=4)
        row += 1
        ttk.Label(frame, text="API key").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.key, show="*").grid(row=row, column=1, sticky="ew", pady=4)
        row += 1
        self.key_hint = ttk.Label(frame, text="", foreground="#555")
        self.key_hint.grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(frame, text="Model").grid(row=row, column=0, sticky="w", pady=4)
        line = ttk.Frame(frame)
        line.grid(row=row, column=1, sticky="ew", pady=4)
        line.columnconfigure(0, weight=1)
        self.model_box = ttk.Combobox(line, textvariable=self.model)
        self.model_box.grid(row=0, column=0, sticky="ew")
        ttk.Button(line, text="Fetch models", command=self.fetch_models).grid(row=0, column=1, padx=(6, 0))
        row += 1
        self.base_label = ttk.Label(frame, text="Address")
        self.base_label.grid(row=row, column=0, sticky="w", pady=4)
        self.base_entry = ttk.Entry(frame, textvariable=self.base)
        self.base_entry.grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        roles = ttk.LabelFrame(frame, text="Use it for", padding=8)
        roles.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        for role, label in ROLES:
            ttk.Checkbutton(roles, text=label, variable=self.role_vars[role]).pack(anchor="w")
        row += 1
        order = ttk.LabelFrame(frame, text="When to use it", padding=8)
        order.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Radiobutton(order, text="Backup: only when my local models fail", variable=self.first,
                        value="backup").pack(anchor="w")
        ttk.Radiobutton(order, text="First choice: before my local models", variable=self.first,
                        value="first").pack(anchor="w")
        row += 1
        ttk.Label(frame, text="Daily limit (requests)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.limit, width=8).grid(row=row, column=1, sticky="w", pady=(8, 0))
        row += 1
        ttk.Label(frame, text="He stops using it after this many requests a day, so a free plan isn't used up and a "
                              "paid one can't surprise you. 0 means no limit.", wraplength=500,
                  foreground="#555").grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=2, sticky="ew", pady=14)
        self.test_button = ttk.Button(buttons, text="Test", command=self.test)
        self.test_button.pack(side="left")
        ttk.Button(buttons, text="Save", command=self.save).pack(side="left", padx=8)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left")
        row += 1
        ttk.Label(frame, textvariable=self.status, wraplength=500).grid(row=row, column=0, columnspan=2, sticky="w")
        self.kind_changed(first=True)
        if provider and cloud.has_key(provider["id"]):
            self.key_hint.config(text="A key is saved. Leave this empty to keep it.")

    # ---- helpers
    def kind_id(self):
        return self.kind_by_label[self.kind.get()]

    def kind_changed(self, first=False):
        preset = cloud.PRESETS[self.kind_id()]
        self.note.config(text=preset["note"])
        self.key_button.state(["!disabled"] if preset["key_url"] else ["disabled"])
        self.model_box.config(values=preset["models"])
        custom = self.kind_id() == "custom"
        self.base_entry.state(["!disabled"] if custom else ["disabled"])
        self.base_label.config(text="Address" if custom else "Address (set for you)")
        if not custom:
            self.base.set(preset["base_url"])
        if first and self.provider:
            return                                               # editing: keep what is saved
        # Suggest a name and a model, but never overwrite something you typed yourself
        suggested_name = "" if custom else preset["label"].replace("Anthropic ", "").replace("Google ", "")
        if not self.name.get() or self.name.get() == self._auto_name:
            self.name.set(suggested_name)
            self._auto_name = suggested_name
        suggested_model = preset["models"][0] if preset["models"] else ""
        if not self.model.get() or self.model.get() == self._auto_model:
            self.model.set(suggested_model)
            self._auto_model = suggested_model

    def open_key_page(self):
        webbrowser.open(cloud.PRESETS[self.kind_id()]["key_url"])

    def current(self):
        """The provider as typed (taking every Tk value on this thread)."""
        kind = self.kind_id()
        taken = {p["id"] for p in llm.settings()["providers"]}
        pid = self.provider["id"] if self.provider else slug(self.name.get() or kind, taken)
        try:
            limit = max(0, int(self.limit.get() or 0))
        except ValueError:
            limit = cloud.DEFAULT_DAILY_LIMIT
        return {"id": pid, "kind": kind, "name": self.name.get().strip() or cloud.PRESETS[kind]["label"],
                "model": self.model.get().strip(), "base_url": self.base.get().strip() if kind == "custom" else "",
                "roles": [role for role, var in self.role_vars.items() if var.get()],
                "first": self.first.get() == "first", "enabled": True, "daily_limit": limit}

    def key_text(self):
        return self.key.get().strip()

    # ---- actions
    def fetch_models(self):
        provider, key = self.current(), self.key_text() or None
        self.status.set("Asking the provider for its models...")

        def work():
            try:
                return cloud.list_models(provider, key), ""
            except cloud.CloudError as e:
                return [], f"{e.kind}: {e}"

        def done(result):
            names, error = result
            if error:
                self.status.set("Couldn't fetch the list: " + error + ". You can still type a model name.")
                return
            self.model_box.config(values=names)
            if names and not self.model.get():
                self.model.set(names[0])
            self.status.set(f"Found {len(names)} models. Pick one from the list.")

        run_in_background(self, work, done)

    def test(self):
        provider, key = self.current(), self.key_text() or None
        if not provider["model"]:
            self.status.set("Choose a model first.")
            return
        self.test_button.state(["disabled"])
        self.status.set("Testing (this uses one request)...")

        def done(result):
            self.test_button.state(["!disabled"])
            self.status.set(("OK: " if result[0] else "Problem: ") + result[1])

        run_in_background(self, lambda: cloud.test_provider(provider, key), done)

    def save(self):
        provider, key = self.current(), self.key_text()
        if not provider["model"]:
            messagebox.showwarning("Jarvis", "Please choose or type a model name.")
            return
        if provider["kind"] == "custom" and not provider["base_url"]:
            messagebox.showwarning("Jarvis", "Please type the address of the service.")
            return
        if not provider["roles"]:
            messagebox.showwarning("Jarvis", "Choose at least one thing to use it for.")
            return
        if not key and not cloud.has_key(provider["id"]):
            messagebox.showwarning("Jarvis", "Please type your API key.")
            return
        if key:
            cloud.set_key(provider["id"], key)
        others = [p for p in llm.settings()["providers"] if p["id"] != provider["id"]]
        position = next((i for i, p in enumerate(llm.settings()["providers"]) if p["id"] == provider["id"]), len(others))
        others.insert(position, provider)
        llm.save_settings(providers=others)
        if self.on_save:
            self.on_save()
        self.destroy()


class OnlineTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=14)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        ttk.Label(self, text="Online models are optional. With none added, everything stays on this PC. What you "
                             "allow below is sent to the provider you add, so choose what you are comfortable with.",
                  wraplength=580, foreground="#555").grid(row=0, column=0, sticky="w")

        self.enabled = tk.BooleanVar(value=llm.settings()["online_enabled"])
        ttk.Checkbutton(self, text="Use online models (switch off to stay fully local, or say \"go offline\")",
                        variable=self.enabled, command=self.master_changed).grid(row=1, column=0, sticky="w", pady=8)

        self.table = ttk.Treeview(self, columns=("name", "model", "use", "order", "key", "today"), show="headings",
                                  height=6, selectmode="browse")
        for column, title, width in (("name", "Name", 100), ("model", "Model", 150), ("use", "Used for", 180),
                                     ("order", "When", 60), ("key", "Key", 40), ("today", "Today", 60)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w")
        self.table.grid(row=2, column=0, sticky="nsew")

        buttons = ttk.Frame(self)
        buttons.grid(row=3, column=0, sticky="ew", pady=8)
        ttk.Button(buttons, text="Add online model...", command=self.add).pack(side="left")
        ttk.Button(buttons, text="Edit...", command=self.edit).pack(side="left", padx=6)
        ttk.Button(buttons, text="Remove", command=self.remove).pack(side="left")
        self.test_button = ttk.Button(buttons, text="Test", command=self.test)
        self.test_button.pack(side="right")

        privacy = ttk.LabelFrame(self, text="What may be sent to online models", padding=8)
        privacy.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        allowed = llm.settings()["online_allowed"]
        self.allowed = {key: tk.BooleanVar(value=allowed.get(key, False)) for key, _ in PRIVACY}
        for key, label in PRIVACY:
            ttk.Checkbutton(privacy, text=label, variable=self.allowed[key], command=self.privacy_changed).pack(anchor="w")
        ttk.Label(privacy, text="Your voice is always turned into text on this PC. Local models never send anything "
                                "out.", wraplength=560, foreground="#555").pack(anchor="w", pady=(6, 0))
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, wraplength=580).grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.refresh()

    def refresh(self):
        self.table.delete(*self.table.get_children())
        for p in llm.settings()["providers"]:
            used = cloud.usage_today(p["id"])[0]
            limit = p.get("daily_limit", 0)
            self.table.insert("", "end", iid=p["id"], values=(
                p["name"], p.get("model", ""), ", ".join(cloud.ROLE_NAMES.get(r, r) for r in p.get("roles", [])),
                "First" if p.get("first") else "Backup", "Yes" if cloud.has_key(p["id"]) else "No",
                f"{used}/{limit}" if limit else str(used)))

    def selected(self):
        chosen = self.table.selection()
        return next((p for p in llm.settings()["providers"] if chosen and p["id"] == chosen[0]), None)

    def master_changed(self):
        llm.save_settings(online_enabled=self.enabled.get())
        self.status.set("Online models are on." if self.enabled.get() else "Online models are off. Everything stays local.")

    def privacy_changed(self):
        llm.save_settings(online_allowed={key: var.get() for key, var in self.allowed.items()})
        self.status.set("Saved.")

    def add(self):
        ProviderDialog(self, on_save=self.refresh)

    def edit(self):
        provider = self.selected()
        if provider:
            ProviderDialog(self, provider, on_save=self.refresh)

    def remove(self):
        provider = self.selected()
        if provider and messagebox.askyesno("Jarvis", f"Remove {provider['name']} and its saved key?"):
            llm.save_settings(providers=[p for p in llm.settings()["providers"] if p["id"] != provider["id"]])
            cloud.delete_key(provider["id"])
            self.refresh()

    def test(self):
        provider = self.selected()
        if not provider:
            self.status.set("Select a model in the list first.")
            return
        self.test_button.state(["disabled"])
        self.status.set(f"Testing {provider['name']} (this uses one request)...")

        def done(result):
            self.test_button.state(["!disabled"])
            self.status.set(f"{provider['name']}: " + result[1])
            self.refresh()

        run_in_background(self, lambda: cloud.test_provider(provider), done)


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Jarvis - Online models")
    root.geometry("660x640")
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        style.theme_use("clam")
    tab = OnlineTab(root)
    tab.pack(fill="both", expand=True, padx=10, pady=10)
    root.mainloop()