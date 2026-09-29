from __future__ import annotations

import customtkinter as ctk

from .. import config as C
from .data_tab import DataTab
from .distributions_tab import DistributionsTab
from .relationships_tab import RelationshipsTab
from .statistics_tab import StatisticsTab
from .widgets import button, configure_tables, label


class MainWindow(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title(C.APP_TITLE)
        self.geometry(C.WINDOW_SIZE)
        self.minsize(*C.MIN_WINDOW_SIZE)
        self.configure(fg_color=C.BG)
        configure_tables(self)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

    def build_interface(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))
        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.pack(side="left")
        label(brand, "CSV Analyzer", size=29, bold=True).pack(anchor="w")
        label(
            brand, "DATA  /  RELATIONSHIPS  /  DISTRIBUTIONS  /  STATISTICS", size=10, color=C.MUTED
        ).pack(anchor="w", pady=(3, 0))
        button(header, "Open dataset…", self.open_file, primary=True, width=155).pack(
            side="right", padx=(12, 0)
        )
        button(header, "Guide", self.show_guide, width=70).pack(side="right")
        self.info = ctk.CTkFrame(self, fg_color=C.PANEL, corner_radius=10)
        self.info.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.info.grid_columnconfigure(0, weight=1)
        self.filename = label(self.info, "No dataset loaded", bold=True, anchor="w")
        self.filename.grid(row=0, column=0, padx=16, pady=(12, 2), sticky="ew")
        self.metrics = label(
            self.info, "Open a CSV or whitespace DAT file to begin.", color=C.MUTED, anchor="w"
        )
        self.metrics.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="ew")
        self.filter_button = button(
            self.info, "Filters · none", self.edit_filters, width=145, state="disabled"
        )
        self.filter_button.grid(row=0, column=1, rowspan=2, padx=16, pady=12)
        self.tabs = ctk.CTkTabview(
            self,
            fg_color=C.BG,
            corner_radius=0,
            segmented_button_fg_color=C.PANEL,
            segmented_button_selected_color="#275E61",
            segmented_button_selected_hover_color="#33767A",
            segmented_button_unselected_color=C.PANEL,
            segmented_button_unselected_hover_color=C.PANEL_ALT,
            anchor="w",
        )
        self.tabs.grid(row=2, column=0, sticky="nsew", padx=24)
        for title in ("Data", "Relationships", "Distributions", "Statistics"):
            tab = self.tabs.add(title)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
        self.data_tab = DataTab(self.tabs.tab("Data"), self)
        self.relationships_tab = RelationshipsTab(self.tabs.tab("Relationships"), self)
        self.distributions_tab = DistributionsTab(self.tabs.tab("Distributions"), self)
        self.statistics_tab = StatisticsTab(self.tabs.tab("Statistics"), self)
        self.views = (
            self.data_tab,
            self.relationships_tab,
            self.distributions_tab,
            self.statistics_tab,
        )
        for view in self.views:
            view.grid(row=0, column=0, sticky="nsew")
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=24, pady=(4, 10))
        footer.grid_columnconfigure(0, weight=1)
        self.status = label(footer, "Ready", color=C.MUTED, anchor="w", height=24)
        self.status.grid(row=0, column=0, sticky="ew")
        self.progress = ctk.CTkProgressBar(
            footer, mode="indeterminate", width=100, height=4, progress_color=C.ACCENT
        )
        self.progress.grid(row=0, column=1, padx=12)
        self.progress.grid_remove()
        self.cancel_button = button(
            footer, "Cancel task", self.cancel_tasks, width=105, state="disabled"
        )
        self.cancel_button.grid(row=0, column=2)
        self.tabs.set("Data")
