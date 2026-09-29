from __future__ import annotations

from tkinter import filedialog

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from .. import config as C
from ..plots import export_figure
from .widgets import button, label


class FigureToolbar(NavigationToolbar2Tk):
    # Keep one export workflow with explicit format and DPI controls.
    toolitems = tuple(item for item in NavigationToolbar2Tk.toolitems if item[0] != "Save")


class PlotPanel(ctk.CTkFrame):
    def __init__(self, parent, app, empty_text):
        super().__init__(parent, fg_color=C.PANEL, corner_radius=12)
        self.app = app
        self.empty_text = empty_text
        self.figure = self.canvas = self.toolbar = None
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 8))
        label(top, "FIGURE", size=11, color=C.MUTED, bold=True).pack(side="left")
        self.export_button = button(top, "Export figure…", self.export, width=130, state="disabled")
        self.export_button.pack(side="right")
        self.dpi = ctk.CTkOptionMenu(
            top,
            values=["150 DPI", "300 DPI", "600 DPI"],
            width=105,
            fg_color=C.PANEL_ALT,
            button_color=C.PANEL_ALT,
        )
        self.dpi.set("300 DPI")
        self.dpi.pack(side="right", padx=10)
        self.host = ctk.CTkFrame(self, fg_color=C.BG, corner_radius=6)
        self.host.grid(row=1, column=0, sticky="nsew", padx=12)
        self.host.grid_rowconfigure(0, weight=1)
        self.host.grid_columnconfigure(0, weight=1)
        self.empty_label = label(self.host, empty_text, color=C.MUTED, size=16, justify="center")
        self.empty_label.grid(row=0, column=0, padx=30, pady=30)
        self.caption = label(
            self,
            "Full data · No figure yet",
            color=C.MUTED,
            anchor="w",
            justify="left",
            wraplength=750,
        )
        self.caption.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 12))
        self.bind(
            "<Configure>", lambda e: self.caption.configure(wraplength=max(240, e.width - 40))
        )

    def show(self, figure, description):
        # Draw successfully before discarding the previous valid figure.
        canvas = FigureCanvasTkAgg(figure, master=self.host)
        try:
            canvas.draw()
        except Exception:
            canvas.get_tk_widget().destroy()
            figure.clear()
            raise
        self.clear()
        self.empty_label.grid_remove()
        self.figure, self.canvas = figure, canvas
        canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self.toolbar = FigureToolbar(canvas, self.host, pack_toolbar=False)
        self.toolbar.grid(row=1, column=0, sticky="ew")
        self.caption.configure(text=description, text_color=C.MUTED)
        self.export_button.configure(state="normal")

    def clear(self, message=None):
        if self.toolbar:
            self.toolbar.destroy()
        if self.canvas:
            # TkAgg schedules draws and event-loop timers on its widget. Cancel
            # these before destroying it, including when a load invalidates plots.
            for attribute in ("_idle_draw_id", "_event_loop_id"):
                callback = getattr(self.canvas, attribute, None)
                if callback:
                    self.canvas.get_tk_widget().after_cancel(callback)
                    setattr(self.canvas, attribute, None)
            self.canvas.get_tk_widget().destroy()
        if self.figure:
            self.figure.clear()
        self.figure = self.canvas = self.toolbar = None
        self.empty_label.configure(text=message or self.empty_text)
        self.empty_label.grid()
        self.caption.configure(text="Full data · No figure yet", text_color=C.MUTED)
        self.export_button.configure(state="disabled")

    def error(self, message):
        self.caption.configure(text=f"Figure not updated: {message}", text_color=C.WARNING)

    def export(self):
        if self.figure is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export displayed figure",
            defaultextension=".png",
            initialfile="figure.png",
            filetypes=[("PNG image", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")],
        )
        if not path:
            return
        try:
            export_figure(self.figure, path, dpi=int(self.dpi.get().split()[0]), overwrite=True)
            self.app.set_status(f"Figure exported to {path}")
        except Exception as exc:
            self.app.show_error(exc)
