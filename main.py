import tkinter as tk
from tkinter import ttk
from datetime import datetime
import io

try:
    from PIL import Image, ImageTk   # pip install pillow
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from app_core import AppCore


# --- Design tokens -----------------------------------------------------------
LIGHT = {
    "BG":       "#FAFAF7",
    "SURFACE":  "#FFFFFF",
    "BORDER":   "#E8E6DF",
    "TEXT":     "#1F1F1C",
    "MUTED":    "#6B6A65",
    "TERTIARY": "#A09F99",
    "ACCENT":   "#1D9E75",
    "HOVER":    "#F1EFE8",
    "PILL_BG":  "#F1EFE8",
    "PILL_FG":  "#6B6A65",
    "REMOTE_BG":   "#E6F1FB",   # soft blue tint for incoming packets
    "REMOTE_EDGE": "#378ADD",
    "SELECT_BG":   "#EEEDFE",   # soft purple for selected rows
    "ERROR":    "#D64545",
}

DARK = {
    "BG":       "#1A1A18",
    "SURFACE":  "#242422",
    "BORDER":   "#35352F",
    "TEXT":     "#EDEBE4",
    "MUTED":    "#A09F99",
    "TERTIARY": "#6B6A65",
    "ACCENT":   "#5DCAA5",
    "HOVER":    "#2E2E2A",
    "PILL_BG":  "#2E2E2A",
    "PILL_FG":  "#A09F99",
    "REMOTE_BG":   "#0C447C",
    "REMOTE_EDGE": "#85B7EB",
    "SELECT_BG":   "#3C3489",
    "ERROR":    "#F07A7A",
}

FONT_UI      = ("Segoe UI", 10)
FONT_UI_BOLD = ("Segoe UI", 11, "bold")
FONT_MONO    = ("Consolas", 9)
FONT_SMALL   = ("Segoe UI", 9)


# --- Theme manager (same pattern as before) ---------------------------------
class ThemeManager:
    def __init__(self, root, mode="dark"):
        self.root = root
        self.mode = mode
        self._subscribers = []
        self._canvas_items = []
        self._on_change = []

    @property
    def t(self):
        return LIGHT if self.mode == "light" else DARK

    def register(self, widget, **roles):
        self._subscribers.append((widget, roles))
        self._apply(widget, roles)
        return widget

    def register_canvas_item(self, canvas, item_id, **roles):
        self._canvas_items.append((canvas, item_id, roles))
        self._apply_canvas(canvas, item_id, roles)

    def on_change(self, fn):
        self._on_change.append(fn)

    def toggle(self):
        self.mode = "dark" if self.mode == "light" else "light"
        self.apply_all()

    def apply_all(self):
        for widget, roles in list(self._subscribers):
            try: self._apply(widget, roles)
            except tk.TclError: pass
        for canvas, item_id, roles in list(self._canvas_items):
            try: self._apply_canvas(canvas, item_id, roles)
            except tk.TclError: pass
        for fn in self._on_change:
            fn()

    def _apply(self, widget, roles):
        widget.configure(**{k: self.t[v] for k, v in roles.items()})

    def _apply_canvas(self, canvas, item_id, roles):
        canvas.itemconfigure(item_id, **{k: self.t[v] for k, v in roles.items()})


# --- A row = its own object ---------------------------------------------------
# Rows got complex enough (checkbox, thumbnail, hover, selection, remote style)
# that pulling them into a class keeps main() readable.

class ClipRow:
    def __init__(self, parent, theme, packet, on_select_change, on_copy):
        self.theme = theme
        self.packet = packet
        self.on_select_change = on_select_change
        self.on_copy = on_copy
        self.selected = tk.BooleanVar(value=False)
        self._photo = None   # must hold a ref or Tk garbage-collects images
        self._restore_after_id = None

        is_remote = packet.get("source") == "remote"
        self.is_remote = is_remote

        self.frame = theme.register(tk.Frame(parent), bg="SURFACE")
        self.frame.pack(fill="x", padx=4, pady=1)

        # Inner padding frame — this is what hover/select colors fill
        self.inner = theme.register(tk.Frame(self.frame), bg="SURFACE")
        self.inner.pack(fill="x", padx=2)

        # Checkbox — only broadcastable, locally-captured items make sense to resend
        self.check = tk.Checkbutton(
            self.inner, variable=self.selected, command=self._on_check,
            bg=theme.t["SURFACE"], activebackground=theme.t["SURFACE"],
            highlightthickness=0, borderwidth=0,
        )
        theme.register(self.check, bg="SURFACE", activebackground="SURFACE")
        self.check.pack(side="left", padx=(6, 4))

        # Remote marker — a small dot + "📡" style label on the left edge
        if is_remote:
            self.marker = tk.Label(
                self.inner, text="⬇", font=FONT_SMALL,
                bg=theme.t["SURFACE"], fg=theme.t["REMOTE_EDGE"],
            )
            theme.register(self.marker, bg="SURFACE", fg="REMOTE_EDGE")
            self.marker.pack(side="left", padx=(0, 6))

        # Timestamp
        ts = datetime.fromtimestamp(packet["timestamp"]).strftime("%H:%M:%S")
        self.ts_lbl = theme.register(
            tk.Label(self.inner, text=ts, font=FONT_MONO, width=9, anchor="w"),
            bg="SURFACE", fg="TERTIARY",
        )
        self.ts_lbl.pack(side="left", padx=(4, 12), pady=8)

        # Copy button — packed on the RIGHT so content (packed later with
        # expand=True) fills the middle. Clicking it writes this row's
        # content back to the system clipboard.
        self.copy_lbl = theme.register(
            tk.Label(self.inner, text="📋", font=FONT_UI,
                     cursor="hand2", padx=6),
            bg="SURFACE", fg="MUTED",
        )
        self.copy_lbl.pack(side="right", padx=(6, 10), pady=8)
        self.copy_lbl.bind("<Button-1>", self._on_copy_click)

        # Content varies by type
        self._build_content()

        # Bind hover on the whole row
        for w in self._hover_targets():
            w.bind("<Enter>", self._on_hover)
            w.bind("<Leave>", self._on_leave)

    def _build_content(self):
        t = self.packet["type"]
        if t == "image" and PIL_AVAILABLE:
            self._build_image_content()
        elif t == "files":
            self._build_files_content()
        else:
            self._build_text_content()

    def _build_text_content(self):
        content = str(self.packet.get("content", "")).replace("\n", " ⏎ ")
        if len(content) > 120:
            content = content[:117] + "..."
        self.content_lbl = self.theme.register(
            tk.Label(self.inner, text=content, font=FONT_UI,
                     anchor="w", justify="left"),
            bg="SURFACE", fg="TEXT",
        )
        self.content_lbl.pack(side="left", fill="x", expand=True, pady=8)

    def _build_files_content(self):
        paths = self.packet.get("content", [])
        if len(paths) == 1:
            text = f"📄  {paths[0]}"
        else:
            text = f"📄  {len(paths)} files"
        self.content_lbl = self.theme.register(
            tk.Label(self.inner, text=text, font=FONT_UI, anchor="w"),
            bg="SURFACE", fg="TEXT",
        )
        self.content_lbl.pack(side="left", fill="x", expand=True, pady=8)

    def _build_image_content(self):
        try:
            img = Image.open(io.BytesIO(self.packet["content"]))
            img.thumbnail((80, 60))
            self._photo = ImageTk.PhotoImage(img)
        except Exception:
            self._build_text_content()
            return

        thumb = tk.Label(self.inner, image=self._photo,
                         bg=self.theme.t["SURFACE"], borderwidth=0)
        self.theme.register(thumb, bg="SURFACE")
        thumb.pack(side="left", padx=(0, 12), pady=4)

        w = self.packet.get("width", "?")
        h = self.packet.get("height", "?")
        size_kb = len(self.packet["content"]) // 1024
        desc = f"🖼  Image  {w}×{h}  ·  {size_kb} KB"
        self.content_lbl = self.theme.register(
            tk.Label(self.inner, text=desc, font=FONT_UI, anchor="w"),
            bg="SURFACE", fg="TEXT",
        )
        self.content_lbl.pack(side="left", fill="x", expand=True, pady=8)

    # --- Hover / selection visuals ------------------------------------------
    def _hover_targets(self):
        return [w for w in (self.inner, self.ts_lbl,
                            getattr(self, "content_lbl", None),
                            getattr(self, "marker", None),
                            getattr(self, "copy_lbl", None))
                if w is not None]

    def _current_bg(self):
        if self.selected.get():
            return self.theme.t["SELECT_BG"]
        if self.is_remote:
            return self.theme.t["REMOTE_BG"]
        return self.theme.t["SURFACE"]

    def _on_hover(self, _):
        for w in self._hover_targets():
            w.configure(bg=self.theme.t["HOVER"])
        self.check.configure(bg=self.theme.t["HOVER"],
                             activebackground=self.theme.t["HOVER"])

    def _on_leave(self, _):
        bg = self._current_bg()
        for w in self._hover_targets():
            w.configure(bg=bg)
        self.check.configure(bg=bg, activebackground=bg)

    def _on_check(self):
        self._on_leave(None)   # repaint with select color if now selected
        self.on_select_change()

    # --- Copy handling ------------------------------------------------------
    def _on_copy_click(self, _event=None):
        # Cancel any pending icon restore from a previous rapid click
        if self._restore_after_id is not None:
            try: self.copy_lbl.after_cancel(self._restore_after_id)
            except tk.TclError: pass
            self._restore_after_id = None

        ok = self.on_copy(self.packet)
        if ok:
            self.copy_lbl.configure(text="✓", fg=self.theme.t["ACCENT"])
        else:
            self.copy_lbl.configure(text="✗", fg=self.theme.t["ERROR"])
        self._restore_after_id = self.copy_lbl.after(
            900, self._restore_copy_icon)

    def _restore_copy_icon(self):
        self._restore_after_id = None
        try:
            self.copy_lbl.configure(text="📋", fg=self.theme.t["MUTED"])
        except tk.TclError:
            pass  # row got destroyed mid-timer


# --- The app -----------------------------------------------------------------
class ClipboardApp:
    def __init__(self, root):
        self.root = root
        root.title("Clipboard viewer")
        root.geometry("620x500")
        root.minsize(480, 360)

        self.theme = ThemeManager(root, mode="dark")
        self.theme.register(root, bg="BG")

        self._setup_ttk_styles()
        self.theme.on_change(self._setup_ttk_styles)

        outer = self.theme.register(tk.Frame(root), bg="BG")
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        card = self.theme.register(
            tk.Frame(outer, highlightthickness=1),
            bg="SURFACE", highlightbackground="BORDER",
        )
        card.pack(fill="both", expand=True)

        self._build_header(card)
        self._build_list(card)
        self._build_footer(card)

        self.rows = []   # list of ClipRow instances, newest first

        self.core = AppCore(on_packet=self.on_packet)
        self.core.start()
        self.root.after(50, self.tick)

    # --- ttk styling --------------------------------------------------------
    def _setup_ttk_styles(self):
        t = self.theme.t
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Ghost.TButton",
                        background=t["SURFACE"], foreground=t["TEXT"],
                        bordercolor=t["BORDER"], focuscolor=t["SURFACE"],
                        relief="flat", padding=(10, 6), font=FONT_SMALL)
        style.map("Ghost.TButton",
                  background=[("active", t["HOVER"]), ("pressed", t["HOVER"])])

        style.configure("Primary.TButton",
                        background=t["ACCENT"], foreground="#FFFFFF",
                        bordercolor=t["ACCENT"], focuscolor=t["ACCENT"],
                        relief="flat", padding=(14, 6),
                        font=("Segoe UI", 9, "bold"))
        style.map("Primary.TButton",
                  background=[("active", t["ACCENT"]), ("pressed", t["ACCENT"]),
                              ("disabled", t["BORDER"])],
                  foreground=[("disabled", t["TERTIARY"])])

        style.configure("Vertical.TScrollbar",
                        background=t["SURFACE"], troughcolor=t["SURFACE"],
                        bordercolor=t["SURFACE"], arrowcolor=t["MUTED"],
                        lightcolor=t["SURFACE"], darkcolor=t["SURFACE"])

    # --- Header -------------------------------------------------------------
    def _build_header(self, parent):
        header = self.theme.register(tk.Frame(parent), bg="SURFACE")
        header.pack(fill="x", padx=16, pady=(14, 10))

        left = self.theme.register(tk.Frame(header), bg="SURFACE")
        left.pack(side="left")

        dot_canvas = self.theme.register(
            tk.Canvas(left, width=10, height=10, highlightthickness=0),
            bg="SURFACE",
        )
        dot_id = dot_canvas.create_oval(1, 1, 9, 9,
                                         fill=self.theme.t["ACCENT"],
                                         outline=self.theme.t["ACCENT"])
        self.theme.register_canvas_item(dot_canvas, dot_id,
                                         fill="ACCENT", outline="ACCENT")
        dot_canvas.pack(side="left", padx=(0, 8), pady=(2, 0))

        self.theme.register(
            tk.Label(left, text="LAN Clipboard", font=FONT_UI_BOLD),
            bg="SURFACE", fg="TEXT",
        ).pack(side="left")

        self.theme.register(
            tk.Label(left, text="Listening", font=FONT_SMALL, padx=8, pady=2),
            bg="PILL_BG", fg="PILL_FG",
        ).pack(side="left", padx=(10, 0))

        self._theme_btn = ttk.Button(header, text="☾ Dark",
                                      style="Ghost.TButton",
                                      command=self._toggle_theme, width=8)
        self._theme_btn.pack(side="right", padx=(6, 0))

        ttk.Button(header, text="⚙  Settings", style="Ghost.TButton",
                   command=self.open_settings).pack(side="right")

        self.theme.register(tk.Frame(parent, height=1), bg="BORDER"
                            ).pack(fill="x")

    def _toggle_theme(self):
        self.theme.toggle()
        self._theme_btn.configure(
            text="☀ Light" if self.theme.mode == "dark" else "☾ Dark")

    # --- List ---------------------------------------------------------------
    def _build_list(self, parent):
        wrap = self.theme.register(tk.Frame(parent), bg="SURFACE")
        wrap.pack(fill="both", expand=True, padx=6, pady=6)

        self.canvas = self.theme.register(
            tk.Canvas(wrap, highlightthickness=0), bg="SURFACE")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.rows_frame = self.theme.register(
            tk.Frame(self.canvas), bg="SURFACE")
        self._rows_window = self.canvas.create_window(
            (0, 0), window=self.rows_frame, anchor="nw")

        def _on_canvas_configure(e):
            self.canvas.itemconfigure(self._rows_window, width=e.width)
        self.canvas.bind("<Configure>", _on_canvas_configure)
        self.rows_frame.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind_all("<MouseWheel>",
            lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        # Empty-state placeholder
        self._placeholder = self.theme.register(
            tk.Label(self.rows_frame,
                     text="(waiting for clipboard...)",
                     font=FONT_UI, pady=20),
            bg="SURFACE", fg="MUTED",
        )
        self._placeholder.pack()

    # --- Footer (selection count + broadcast button) ------------------------
    def _build_footer(self, parent):
        self.theme.register(tk.Frame(parent, height=1), bg="BORDER"
                            ).pack(fill="x")

        footer = self.theme.register(tk.Frame(parent), bg="SURFACE")
        footer.pack(fill="x", padx=16, pady=10)

        self.selection_var = tk.StringVar(value="0 selected")
        self.theme.register(
            tk.Label(footer, textvariable=self.selection_var,
                     font=FONT_SMALL),
            bg="SURFACE", fg="MUTED",
        ).pack(side="left")

        self.broadcast_btn = ttk.Button(
            footer, text="📡  Broadcast selected",
            style="Primary.TButton",
            command=self.broadcast_selected, state="disabled",
        )
        self.broadcast_btn.pack(side="right")

    # --- Event handlers -----------------------------------------------------
    def tick(self):
        self.core.drain()
        self.root.after(50, self.tick)

    def on_packet(self, packet):
        # Drop placeholder on first real packet
        if self._placeholder is not None:
            self._placeholder.destroy()
            self._placeholder = None

        row = ClipRow(self.rows_frame, self.theme, packet,
                      on_select_change=self._update_selection_ui,
                      on_copy=self._copy_packet)

        # Newest first
        if self.rows:
            row.frame.pack_configure(before=self.rows[0].frame)
        self.rows.insert(0, row)

        # Cap history
        while len(self.rows) > 200:
            old = self.rows.pop()
            old.frame.destroy()

    def _update_selection_ui(self):
        selected = [r for r in self.rows if r.selected.get()]
        n = len(selected)
        self.selection_var.set(f"{n} selected" if n != 1 else "1 selected")
        self.broadcast_btn.configure(
            state=("normal" if n > 0 else "disabled"))

    def _copy_packet(self, packet):
        """Called by a ClipRow when the user clicks its copy button.
        Returns True/False so the row can flash ✓/✗."""
        return self.core.copy_to_clipboard(packet)

    def broadcast_selected(self):
        sent = 0
        for row in self.rows:
            if row.selected.get():
                # Strip UI-only fields before sending; the network layer
                # shouldn't see 'source' (the receiver adds its own).
                packet = {k: v for k, v in row.packet.items() if k != "source"}
                self.core.broadcast(packet)
                sent += 1
                # Uncheck after sending — feels right
                row.selected.set(False)
                row._on_check()

        if sent:
            print(f"Broadcast {sent} item(s)")

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("360x240")
        self.theme.register(win, bg="BG")

        card = self.theme.register(
            tk.Frame(win, highlightthickness=1),
            bg="SURFACE", highlightbackground="BORDER",
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        self.theme.register(
            tk.Label(card, text="Settings", font=FONT_UI_BOLD),
            bg="SURFACE", fg="TEXT",
        ).pack(anchor="w", padx=16, pady=(14, 4))

        self.theme.register(
            tk.Label(card,
                     text="Keybinds, peer list, and auto-broadcast\n"
                          "will live here.",
                     font=FONT_SMALL, justify="left"),
            bg="SURFACE", fg="MUTED",
        ).pack(anchor="w", padx=16, pady=(0, 14))


if __name__ == "__main__":
    root = tk.Tk()
    ClipboardApp(root)
    root.mainloop()