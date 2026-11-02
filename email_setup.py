"""Email settings window for Jarvis. No files to edit, no code to touch.

Open it by saying "Jarvis, set up my email", or run:  python email_setup.py

Tab 1 - Account: your address, app password, name, and a Test button that checks the login (sends nothing).
Tab 2 - Contacts: the people Jarvis may email.
"""
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import email_config as cfg

CATEGORIES = ["primary", "social", "promotions", "updates", "forums", "all"]


class SetupWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Jarvis - Email setup")
        self.geometry("560x560")
        self.minsize(520, 520)
        self.resizable(True, True)

        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")

        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)
        self.account = ttk.Frame(tabs, padding=14)
        self.people = ttk.Frame(tabs, padding=14)
        tabs.add(self.account, text="Account")
        tabs.add(self.people, text="Contacts")
        self._build_account()
        self._build_contacts()
        self._load_existing()

    # ------------------------------------------------------------ account tab

    def _build_account(self):
        frame = self.account
        frame.columnconfigure(1, weight=1)

        self.provider = tk.StringVar(value="Gmail")
        self.address = tk.StringVar()
        self.password = tk.StringVar()
        self.name = tk.StringVar()
        self.signature = tk.StringVar()
        self.imap = tk.StringVar()
        self.smtp = tk.StringVar()
        self.port = tk.StringVar()
        self.category = tk.StringVar(value="primary")
        self.status = tk.StringVar()

        row = 0
        ttk.Label(frame, text="Provider").grid(row=row, column=0, sticky="w", pady=4)
        box = ttk.Combobox(frame, textvariable=self.provider, values=list(cfg.PROVIDERS), state="readonly")
        box.grid(row=row, column=1, sticky="ew", pady=4)
        box.bind("<<ComboboxSelected>>", lambda _e: self._provider_changed())

        row += 1
        self.note = ttk.Label(frame, text="", wraplength=470, foreground="#555")
        self.note.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))

        row += 1
        self.help_button = ttk.Button(frame, text="How do I get an app password?", command=self._open_help)
        self.help_button.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10))

        for label, variable, show in (("Email address", self.address, ""),
                                      ("App password", self.password, "*"),
                                      ("Your name", self.name, ""),
                                      ("Sign emails as", self.signature, "")):
            row += 1
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(frame, textvariable=variable, show=show).grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        self.password_hint = ttk.Label(frame, text="", foreground="#555")
        self.password_hint.grid(row=row, column=1, sticky="w")

        row += 1
        ttk.Label(frame, text="Default folder").grid(row=row, column=0, sticky="w", pady=4)
        self.category_box = ttk.Combobox(frame, textvariable=self.category, values=CATEGORIES, state="readonly")
        self.category_box.grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        self.advanced = ttk.LabelFrame(frame, text="Servers (filled in for you)", padding=8)
        self.advanced.grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        self.advanced.columnconfigure(1, weight=1)
        for i, (label, variable) in enumerate((("Incoming (IMAP)", self.imap), ("Outgoing (SMTP)", self.smtp),
                                               ("Outgoing port", self.port))):
            ttk.Label(self.advanced, text=label).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(self.advanced, textvariable=variable).grid(row=i, column=1, sticky="ew", pady=2)

        row += 1
        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.test_button = ttk.Button(buttons, text="Test connection", command=self._test)
        self.test_button.pack(side="left")
        ttk.Button(buttons, text="Save", command=self._save).pack(side="left", padx=8)

        row += 1
        ttk.Label(frame, textvariable=self.status, wraplength=470).grid(row=row, column=0, columnspan=2,
                                                                       sticky="w", pady=10)
        self._provider_changed(keep_servers=False)

    def _provider_changed(self, keep_servers=False):
        info = cfg.PROVIDERS[self.provider.get()]
        self.note.config(text=info["note"])
        self.help_button.state(["!disabled"] if info["help"] else ["disabled"])
        if not keep_servers:
            self.imap.set(info["imap"])
            self.smtp.set(info["smtp"])
            self.port.set(str(info["port"]))
        gmail = self.provider.get() == "Gmail"
        self.category_box.config(state="readonly" if gmail else "disabled")
        if not gmail:
            self.category.set("all")
        elif self.category.get() == "all":
            self.category.set("primary")

    def _open_help(self):
        link = cfg.PROVIDERS[self.provider.get()]["help"]
        if link:
            webbrowser.open(link)

    def _load_existing(self):
        env = cfg.read_env()
        if not env.get("EMAIL_ADDRESS"):
            return
        self.address.set(env.get("EMAIL_ADDRESS", ""))
        self.name.set(env.get("EMAIL_NAME", ""))
        self.signature.set(env.get("EMAIL_SIGNATURE", ""))
        imap = env.get("EMAIL_IMAP_HOST", "")
        for provider, info in cfg.PROVIDERS.items():
            if info["imap"] and info["imap"] == imap:
                self.provider.set(provider)
                break
        else:
            self.provider.set("Other")
        self._provider_changed(keep_servers=False)
        self.imap.set(imap or self.imap.get())
        self.smtp.set(env.get("EMAIL_SMTP_HOST", self.smtp.get()))
        self.port.set(env.get("EMAIL_SMTP_PORT", self.port.get()))
        if env.get("EMAIL_DEFAULT_CATEGORY") in CATEGORIES and self.provider.get() == "Gmail":
            self.category.set(env["EMAIL_DEFAULT_CATEGORY"])
        saved = cfg.get_password(self.address.get(), env)
        self.password_hint.config(text="A password is already saved. Leave this empty to keep it."
                                  if saved else "")

    def _values(self):
        password = self.password.get().strip() or cfg.get_password(self.address.get().strip())
        return password

    def _test(self):
        # Read the fields here, on the window's own thread: Tk isn't safe to use from another thread.
        address, password = self.address.get(), self._values()
        imap, smtp, port = self.imap.get(), self.smtp.get(), self.port.get() or 465
        self.status.set("Testing...")
        self.test_button.state(["disabled"])
        results = queue.Queue()

        def work():
            results.put(cfg.test_connection(address, password, imap, smtp, port))

        threading.Thread(target=work, daemon=True).start()
        self._wait_for(results)

    def _wait_for(self, results):
        """The window checks for the answer every 100 ms, so the worker thread never touches Tk."""
        try:
            ok, message = results.get_nowait()
        except queue.Empty:
            self.after(100, lambda: self._wait_for(results))
            return
        self._test_done(ok, message)

    def _test_done(self, ok, message):
        self.test_button.state(["!disabled"])
        self.status.set(("OK: " if ok else "Problem: ") + message)

    def _save(self):
        address = self.address.get().strip()
        if "@" not in address:
            messagebox.showwarning("Jarvis", "Please type your email address.")
            return
        if not self.password.get().strip() and not cfg.get_password(address):
            messagebox.showwarning("Jarvis", "Please type your app password.")
            return
        try:
            port = int(self.port.get() or 465)
        except ValueError:
            messagebox.showwarning("Jarvis", "The outgoing port should be a number, such as 465.")
            return
        where = cfg.save(address, self.password.get(), self.name.get(), self.signature.get(),
                         self.imap.get(), self.smtp.get(), port,
                         self.category.get() if self.provider.get() == "Gmail" else "all")
        self.password.set("")
        self.password_hint.config(text="A password is saved. Leave this empty to keep it.")
        saved_in = {"keyring": "in Windows' password store", "env": "in the .env file"}.get(where, "as before")
        self.status.set(f"Saved. Your password is kept {saved_in}. You can close this window and tell Jarvis "
                        "to check your email.")

    # ------------------------------------------------------------ contacts tab

    def _build_contacts(self):
        frame = self.people
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text="Jarvis can only email people listed here, or reply to someone who emailed you.",
                  wraplength=500, foreground="#555").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.table = ttk.Treeview(frame, columns=("name", "address"), show="headings", height=10)
        self.table.heading("name", text="Name")
        self.table.heading("address", text="Email address")
        self.table.column("name", width=170)
        self.table.column("address", width=280)
        self.table.grid(row=1, column=0, columnspan=3, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=3, sticky="ns")

        self.new_name = tk.StringVar()
        self.new_address = tk.StringVar()
        adder = ttk.Frame(frame)
        adder.grid(row=2, column=0, columnspan=3, sticky="ew", pady=10)
        adder.columnconfigure(1, weight=1)
        adder.columnconfigure(3, weight=1)
        ttk.Label(adder, text="Name").grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(adder, textvariable=self.new_name).grid(row=0, column=1, sticky="ew")
        ttk.Label(adder, text="Email").grid(row=0, column=2, padx=(8, 4))
        ttk.Entry(adder, textvariable=self.new_address).grid(row=0, column=3, sticky="ew")
        ttk.Button(adder, text="Add", command=self._add_contact).grid(row=0, column=4, padx=(8, 0))
        ttk.Button(frame, text="Remove selected", command=self._remove_contact).grid(row=3, column=0, sticky="w")
        ttk.Label(frame, text="Tip: you can also say \"Jarvis, save the sender as a contact\" after he reads an email.",
                  wraplength=500, foreground="#555").grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self._refresh_contacts()

    def _refresh_contacts(self):
        self.table.delete(*self.table.get_children())
        for name, address in cfg.read_contacts():
            self.table.insert("", "end", values=(name, address))

    def _add_contact(self):
        name, address = self.new_name.get().strip(), self.new_address.get().strip()
        if not name or not cfg.find_address(address):
            messagebox.showwarning("Jarvis", "Please type a name and a valid email address.")
            return
        cfg.add_contact(name, cfg.find_address(address))
        self.new_name.set("")
        self.new_address.set("")
        self._refresh_contacts()

    def _remove_contact(self):
        for item in self.table.selection():
            cfg.remove_contact(self.table.item(item)["values"][0])
        self._refresh_contacts()


if __name__ == "__main__":
    SetupWindow().mainloop()