"""Model window for Jarvis: choose and load the language model in LM Studio.

Open it from the panel settings ("Load model...") or by saying "Jarvis, change the model". Or run:
    python model_setup.py

Automatic means he uses whatever is loaded in LM Studio. No setup, and he follows it if you load a different one.
"""
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import jarvis_llm as llm
from online_setup import OnlineTab

TYPE_NAMES = {"llm": "Text", "vlm": "Vision", "embeddings": "Embedding"}
AUTO_VISION = "Automatic"
NO_VISION = "Off (read the text on the screen)"
AUTO_CODE = "Automatic (a loaded coder model)"
NO_CODE = "Off (use the main model for code)"


class ModelWindow(tk.Tk):
    def __init__(self, tab="local"):
        super().__init__()
        self.title("Jarvis - Language models")
        self.geometry("680x820")
        self.minsize(640, 720)
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")
        self.models = []
        self.busy = False

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=6, pady=6)
        body = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(body, text="Local models (LM Studio)")
        self.online = OnlineTab(self.tabs)
        self.tabs.add(self.online, text="Online models")
        if tab == "online":
            self.tabs.select(self.online)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        # --- LM Studio
        server = ttk.LabelFrame(body, text="LM Studio", padding=8)
        server.grid(row=0, column=0, sticky="ew")
        server.columnconfigure(1, weight=1)
        self.url = tk.StringVar(value=llm.settings()["lm_url"])
        ttk.Label(server, text="Server address").grid(row=0, column=0, sticky="w")
        ttk.Entry(server, textvariable=self.url).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(server, text="Refresh", command=self.refresh).grid(row=0, column=2)
        self.connection = tk.StringVar(value="Looking for LM Studio...")
        ttk.Label(server, textvariable=self.connection, wraplength=560, foreground="#555").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # --- which model
        choice = ttk.LabelFrame(body, text="Which model should Jarvis use?", padding=8)
        choice.grid(row=1, column=0, sticky="ew", pady=10)
        s = llm.settings()
        self.mode = tk.StringVar(value="fixed" if s["mode"] == "fixed" and s["model"] else "auto")
        ttk.Radiobutton(choice, text="Automatic: whatever is loaded in LM Studio (recommended)",
                        variable=self.mode, value="auto").pack(anchor="w")
        ttk.Radiobutton(choice, text="Always this one: the model selected in the list below",
                        variable=self.mode, value="fixed").pack(anchor="w", pady=(4, 0))

        # --- the list
        listing = ttk.Frame(body)
        listing.grid(row=2, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(listing, columns=("model", "type", "loaded", "tools"), show="headings",
                                  height=9, selectmode="browse")
        for column, title, width in (("model", "Model", 290), ("type", "Type", 70), ("loaded", "Loaded", 70),
                                     ("tools", "Uses tools", 80)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w" if column == "model" else "center")
        self.table.tag_configure("loaded", background="#e3f3e6")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.load_button = ttk.Button(actions, text="Load selected", command=self.load_selected)
        self.load_button.pack(side="left")
        self.replace = tk.BooleanVar(value=False)             # off: you can keep a coder AND a chat model loaded together
        ttk.Checkbutton(actions, text="Unload the others first (frees memory)", variable=self.replace).pack(
            side="left", padx=8)
        self.test_button = ttk.Button(actions, text="Test", command=self.test)
        self.test_button.pack(side="right")

        # --- coding
        coding = ttk.LabelFrame(body, text="Coding", padding=8)
        coding.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        coding.columnconfigure(1, weight=1)
        ttk.Label(coding, text="Coder model").grid(row=0, column=0, sticky="w")
        self.code = tk.StringVar(value=AUTO_CODE)
        self.code_box = ttk.Combobox(coding, textvariable=self.code, state="readonly")
        self.code_box.grid(row=0, column=1, sticky="ew", padx=6)
        s0 = llm.settings()
        self.fallback = tk.BooleanVar(value=bool(s0["fallback"]))
        self.on_demand = tk.BooleanVar(value=bool(s0["code_on_demand"]))
        ttk.Checkbutton(coding, text="If a model fails, try another loaded one", variable=self.fallback).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(coding, text="Let LM Studio load the coder when code is asked (slow, may unload others)",
                        variable=self.on_demand).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(coding, text="Code questions go to a coder model when one is loaded. Everything else uses the main "
                               "model, which is faster for conversation and actions.", wraplength=560,
                  foreground="#555").grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # --- pictures
        seeing = ttk.LabelFrame(body, text="Reading the screen (pictures)", padding=8)
        seeing.grid(row=5, column=0, sticky="ew", pady=10)
        seeing.columnconfigure(1, weight=1)
        ttk.Label(seeing, text="Vision model").grid(row=0, column=0, sticky="w")
        self.vision = tk.StringVar(value=AUTO_VISION)
        self.vision_box = ttk.Combobox(seeing, textvariable=self.vision, state="readonly")
        self.vision_box.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(seeing, text="Automatic uses a loaded vision model if there is one. Without one, he reads the text "
                               "on the screen instead.", wraplength=560, foreground="#555").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        bottom = ttk.Frame(body)
        bottom.grid(row=6, column=0, sticky="ew")
        ttk.Button(bottom, text="Save", command=self.save).pack(side="left")
        ttk.Button(bottom, text="Close", command=self.destroy).pack(side="left", padx=8)
        self.status = tk.StringVar()
        ttk.Label(body, textvariable=self.status, wraplength=580).grid(row=7, column=0, sticky="w", pady=(10, 0))

        self.refresh()

    # ------------------------------------------------------------ background work (never touch Tk from a thread)

    def run_in_background(self, work, done):
        results = queue.Queue()
        threading.Thread(target=lambda: results.put(work()), daemon=True).start()

        def check():
            try:
                value = results.get_nowait()
            except queue.Empty:
                self.after(100, check)
                return
            done(value)

        self.after(100, check)

    def set_busy(self, busy, message=""):
        self.busy = busy
        state = ["disabled"] if busy else ["!disabled"]
        for button in (self.load_button, self.test_button):
            button.state(state)
        if message:
            self.status.set(message)

    # ------------------------------------------------------------ the list

    def refresh(self):
        llm.save_settings(lm_url=self.url.get())
        self.connection.set("Looking for LM Studio...")
        self.run_in_background(lambda: llm.list_models(refresh=True), self.show_models)

    def show_models(self, models):
        self.models = models
        self.table.delete(*self.table.get_children())
        shown = [m for m in models if m["type"] != "embeddings"]
        for m in sorted(shown, key=lambda m: (not m["loaded"], m["id"])):
            tools = {True: "Yes", False: "No", None: "?"}[m["tools"]]
            kind = "Coder" if m.get("coder") else TYPE_NAMES.get(m["type"], m["type"])
            self.table.insert("", "end", iid=m["id"], tags=("loaded",) if m["loaded"] else (),
                              values=(m["id"], kind,
                                      "Loaded" if m["loaded"] else "", tools))
        s = llm.settings()
        if s["mode"] == "fixed" and s["model"] in self.table.get_children():
            self.table.selection_set(s["model"])
        elif self.table.get_children():
            first_loaded = next((m["id"] for m in shown if m["loaded"]), None)
            if first_loaded:
                self.table.selection_set(first_loaded)

        coders = [m["id"] for m in shown if m.get("coder")]
        self.code_box.config(values=[AUTO_CODE, NO_CODE] + coders)
        code_now = s["code"]
        self.code.set(AUTO_CODE if code_now in ("auto", "") else NO_CODE if code_now == "off" else code_now)

        vision_models = [m["id"] for m in shown if m["type"] == "vlm"]
        self.vision_box.config(values=[AUTO_VISION, NO_VISION] + vision_models)
        current = s["vision"]
        self.vision.set(AUTO_VISION if current in ("auto", "") else NO_VISION if current == "off" else current)

        if not llm.reachable():
            self.connection.set("Can't reach LM Studio. Open LM Studio, go to the Developer tab, and turn on "
                                "the server. Then press Refresh.")
        else:
            loaded = [m["id"] for m in shown if m["loaded"]]
            if loaded:
                self.connection.set(f"Connected. Loaded now: {', '.join(loaded)}")
            else:
                self.connection.set("Connected, but no model is loaded. Select one below and press Load selected.")

    def selected(self):
        chosen = self.table.selection()
        return chosen[0] if chosen else None

    # ------------------------------------------------------------ actions

    def load_selected(self):
        model = self.selected()
        if not model:
            messagebox.showinfo("Jarvis", "Select a model in the list first.")
            return
        replace = self.replace.get()                          # read here: Tk can't be used from the worker thread
        self.set_busy(True, f"Loading {model}. This can take a minute...")
        self.run_in_background(lambda: llm.load_model(model, replace=replace),
                               lambda result: self.loaded(model, result))

    def loaded(self, model, result):
        ok, message = result
        self.set_busy(False, ("Loaded " + model + ". ") if ok else message)
        self.refresh()

    def test(self):
        model = self.selected() if self.mode.get() == "fixed" else None
        loaded_ids = [m["id"] for m in self.models if m["loaded"]]
        model = model or (self.selected() if self.selected() in loaded_ids else None) or llm.chat_model()
        self.set_busy(True, f"Testing {model}...")
        self.run_in_background(lambda: llm.test_model(model), lambda result: self.tested(model, result))

    def tested(self, model, result):
        ok, _seconds, message = result
        self.set_busy(False, f"{model}: {message}")

    def save(self):
        mode = self.mode.get()
        model = self.selected() or ""
        if mode == "fixed" and not model:
            messagebox.showinfo("Jarvis", "Select the model to always use, or choose Automatic.")
            return
        vision = {AUTO_VISION: "auto", NO_VISION: "off"}.get(self.vision.get(), self.vision.get())
        code = {AUTO_CODE: "auto", NO_CODE: "off"}.get(self.code.get(), self.code.get())
        llm.save_settings(lm_url=self.url.get(), mode=mode, model=model if mode == "fixed" else "", vision=vision,
                          code=code, fallback=self.fallback.get(), code_on_demand=self.on_demand.get())
        self.status.set("Saved. Jarvis uses it from his next request, with no restart.")


if __name__ == "__main__":
    ModelWindow(tab="online" if "--tab" in sys.argv and "online" in sys.argv else "local").mainloop()