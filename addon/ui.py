from __future__ import annotations

from datetime import date, timedelta
import uuid
from typing import Any, Dict, List, Optional

from aqt import mw
from aqt.qt import (
    QAction,
    QBrush,
    QCheckBox,
    QColor,
    QComboBox,
    QDialog,
    QFormLayout,
    QHeaderView,
    QGuiApplication,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    Qt,
    QWidget,
)

from .config import DEFAULT_CONFIG, config_to_dict, normalize_config
from .schedule import (
    filter_deck_names_for_schedule,
    match_deck_names,
    preview_schedule,
    rebalance_schedule_offsets,
)


VALID_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
STAGGER_OPTIONS = [
    ("Stable balanced", "stable"),
    ("Off", "none"),
]
STAGGER_DESCRIPTIONS = {
    "stable": "Keep existing deck offsets stable and place newly matched decks into the lightest phase.",
    "none": "Do not offset decks. Matching decks follow the same schedule on the same days.",
}
TYPE_DESCRIPTIONS = {
    "every_n_days": "Introduce new cards on a repeating cycle, such as 1 card every 3 days.",
    "dow": "Set a separate new-card limit for each weekday.",
}
PREVIEW_DAYS = 14
SCHEDULE_LIST_WIDTH = 260


def _copy_schedule(sched: Dict[str, Any]) -> Dict[str, Any]:
    copied: Dict[str, Any] = {
        "_uid": str(sched.get("_uid", "")),
        "id": str(sched.get("id", "")),
        "type": str(sched.get("type", "every_n_days")),
        "targets": list(sched.get("targets", []) or []),
        "leaf_only": bool(sched.get("leaf_only", True)),
    }
    if copied["type"] == "every_n_days":
        copied["m"] = int(sched.get("m", 1))
        copied["n"] = int(sched.get("n", 3))
    else:
        copied["by_day"] = dict(sched.get("by_day", {}) or {})

    stagger = sched.get("stagger")
    if stagger:
        copied["stagger"] = dict(stagger)
    stagger_state = sched.get("stagger_state")
    if isinstance(stagger_state, dict):
        copied["stagger_state"] = {
            "assignments": dict(stagger_state.get("assignments", {}) or {}),
            "schedule_type": str(stagger_state.get("schedule_type", "")),
            "cycle_length": int(stagger_state.get("cycle_length", 0) or 0),
        }

    return copied


def _normalized_config_dict(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = normalize_config(raw or DEFAULT_CONFIG)
    return {
        "epoch": normalized.epoch,
        "schedules": [_copy_schedule(sched) for sched in normalized.schedules],
        "defaults": dict(normalized.defaults),
    }


class SchedulerConfigDialog(QDialog):
    def __init__(self, module: str, parent=None) -> None:
        super().__init__(parent)
        self.module = module
        self.setWindowTitle("Fractional Scheduler Config")
        self.resize(920, 560)

        raw_config = mw.addonManager.getConfig(module) if mw is not None else None
        self.config: Dict[str, Any] = _normalized_config_dict(raw_config)

        self._current_uid: Optional[str] = None
        self._building = False
        self._preview_width_update_in_progress = False

        self._build_ui()
        self._fit_to_screen()
        self._load_defaults()
        self._set_editor_enabled(False)
        self._load_schedule_list()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        # Left: schedule list + add/remove
        left = QVBoxLayout()
        left.setSpacing(8)
        self.schedule_list = QListWidget()
        self.schedule_list.setMinimumWidth(SCHEDULE_LIST_WIDTH)
        self.schedule_list.setMaximumWidth(SCHEDULE_LIST_WIDTH)
        self.schedule_list.setAlternatingRowColors(True)
        self.schedule_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.schedule_list.setMinimumHeight(180)
        self.schedule_list.model().rowsMoved.connect(self._on_schedule_reordered)
        self.schedule_list.currentRowChanged.connect(self._on_schedule_selected)
        self.schedule_heading = QLabel("Schedules")
        self.schedule_summary = QLabel("No schedules configured yet.")
        self.schedule_summary.setWordWrap(True)
        self.schedule_summary.setStyleSheet("color: palette(mid);")
        left.addWidget(self.schedule_heading)
        left.addWidget(self.schedule_summary)
        left.addWidget(self.schedule_list)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        self.btn_copy = QPushButton("Copy")
        self.btn_remove = QPushButton("Remove")
        self.btn_add.clicked.connect(self._add_schedule)
        self.btn_copy.clicked.connect(self._copy_schedule)
        self.btn_remove.clicked.connect(self._remove_schedule)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_copy)
        btn_row.addWidget(self.btn_remove)
        left.addLayout(btn_row)

        root.addLayout(left, 0)

        # Right: editor
        right = QVBoxLayout()
        self.tabs = QTabWidget()

        # Type-specific stack
        self.stack = QStackedWidget()
        self.every_widget = self._build_every_widget()
        self.dow_widget = self._build_dow_widget()
        self.stack.addWidget(self.every_widget)
        self.stack.addWidget(self.dow_widget)

        schedule_tab = QWidget()
        schedule_tab_layout = QVBoxLayout(schedule_tab)
        schedule_tab_layout.setContentsMargins(0, 0, 0, 0)
        schedule_tab_layout.setSpacing(10)

        autosave_note = QLabel("Changes save automatically.")
        autosave_note.setStyleSheet("color: palette(mid);")
        schedule_tab_layout.addWidget(autosave_note)

        action_row = QHBoxLayout()
        self.rebalance_btn = QPushButton("Rebalance Offsets")
        self.rebalance_btn.setToolTip(
            "Recompute stable stagger offsets for the selected schedule using the decks that match right now."
        )
        self.rebalance_btn.clicked.connect(self._rebalance_current_schedule)
        self.rebalance_help = QLabel(
            "Useful after deck additions or moves. This updates the preview only; apply limits separately."
        )
        self.rebalance_help.setWordWrap(True)
        self.rebalance_help.setStyleSheet("color: palette(mid);")
        action_row.addWidget(self.rebalance_btn)
        action_row.addWidget(self.rebalance_help, 1)
        schedule_tab_layout.addLayout(action_row)

        schedule_box = QGroupBox("Schedule")
        schedule_form = QFormLayout(schedule_box)

        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("Shown in the schedule list")

        self.type_combo = QComboBox()
        self.type_combo.addItems(["Every N Days", "Day of Week"])
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        self.leaf_only_check = QCheckBox("Apply to leaf decks only")
        self.leaf_only_check.setChecked(True)
        self.leaf_only_check.setToolTip(
            "Skip container decks that only exist to hold subdecks."
        )
        self.leaf_only_check.stateChanged.connect(self._refresh_preview)

        name_label = QLabel("Name")
        name_label.setToolTip("Shown in the schedule list.")
        type_label = QLabel("Rule type")
        type_label.setToolTip("Determines how daily new-card limits are computed.")
        self.type_combo.setToolTip(
            "Every N Days: spread cards evenly across a cycle. Day of Week: set per-weekday limits."
        )
        self.type_help = QLabel()
        self.type_help.setWordWrap(True)
        self.type_help.setStyleSheet("color: palette(mid);")
        self.leaf_only_help = QLabel(
            "Recommended for wildcard targets so parent container decks do not get limits."
        )
        self.leaf_only_help.setWordWrap(True)
        self.leaf_only_help.setStyleSheet("color: palette(mid);")

        schedule_form.addRow(name_label, self.id_edit)
        schedule_form.addRow(type_label, self.type_combo)
        schedule_form.addRow("", self.type_help)
        schedule_form.addRow("", self.leaf_only_check)
        schedule_form.addRow("", self.leaf_only_help)
        schedule_tab_layout.addWidget(schedule_box)

        self.rule_box = QGroupBox("Rule")
        rule_layout = QVBoxLayout(self.rule_box)
        rule_layout.setContentsMargins(12, 12, 12, 12)
        rule_layout.addWidget(self.stack, 0, Qt.AlignmentFlag.AlignTop)
        schedule_tab_layout.addWidget(self.rule_box)
        self.rule_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        # Targets
        target_box = QGroupBox("Deck targets")
        target_layout = QVBoxLayout(target_box)
        self.target_list = QListWidget()
        self.target_list.setMinimumHeight(80)
        self.target_list.setMaximumHeight(110)
        self.target_help = QLabel(
            "Pick deck adds an exact target immediately. Use Add wildcard... or type patterns like Deck::Subdeck*."
        )
        self.target_help.setWordWrap(True)
        self.target_help.setStyleSheet("color: palette(mid);")
        self.target_summary = QLabel("No targets yet.")
        self.target_summary.setWordWrap(True)
        self.target_summary.setStyleSheet("color: palette(mid);")
        target_layout.addWidget(self.target_help)
        target_layout.addWidget(self.target_summary)
        target_layout.addWidget(self.target_list)

        target_add_row = QHBoxLayout()
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("Deck::Subdeck or Deck::*")
        self.target_pick = QPushButton("Pick deck...")
        self.target_pick_wildcard = QPushButton("Add wildcard...")
        self.target_add = QPushButton("Add target")
        self.target_remove = QPushButton("Remove selected")
        self.target_pick.clicked.connect(self._pick_target)
        self.target_pick_wildcard.clicked.connect(self._pick_wildcard_target)
        self.target_add.clicked.connect(self._add_target)
        self.target_remove.clicked.connect(self._remove_target)
        target_add_row.addWidget(self.target_input, 1)
        target_add_row.addWidget(self.target_pick)
        target_add_row.addWidget(self.target_pick_wildcard)
        target_add_row.addWidget(self.target_add)
        target_layout.addLayout(target_add_row)
        target_layout.addWidget(self.target_remove)
        schedule_tab_layout.addWidget(target_box)
        target_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        # Preview
        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        self.preview_summary = QLabel("Select a schedule to preview it.")
        self.preview_summary.setWordWrap(True)
        self.preview_summary.setStyleSheet("color: palette(mid);")
        self.preview_table = QTableWidget()
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setWordWrap(False)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.verticalHeader().setDefaultSectionSize(26)
        self.preview_table.horizontalHeader().setStretchLastSection(False)
        self.preview_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.preview_table.horizontalHeader().setMinimumSectionSize(44)
        self.preview_table.horizontalHeader().setSectionsClickable(False)
        self.preview_table.horizontalHeader().setHighlightSections(False)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.preview_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.preview_table.horizontalHeader().sectionResized.connect(
            self._on_preview_column_resized
        )
        self.preview_table.setShowGrid(True)
        self.preview_table.setGridStyle(Qt.PenStyle.SolidLine)
        self.preview_table.setStyleSheet(
            "QTableWidget { gridline-color: #b8c4d3; alternate-background-color: #fafcff; }"
            "QHeaderView::section { background: #eef4ff; padding: 6px 4px; font-weight: 600; border-top: 1px solid #b8c4d3; border-left: 1px solid #b8c4d3; border-right: 1px solid #8fa1b8; border-bottom: 1px solid #8fa1b8; }"
        )
        self.preview_table.setMinimumHeight(180)
        self.preview_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        preview_layout.addWidget(self.preview_summary)
        preview_layout.addWidget(self.preview_table, 1)
        schedule_tab_layout.addWidget(preview_box, 1)
        preview_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        schedule_tab_layout.addStretch(1)

        global_tab = QWidget()
        global_layout = QVBoxLayout(global_tab)
        global_layout.setContentsMargins(0, 0, 0, 0)
        global_layout.setSpacing(10)
        global_help = QLabel(
            "These settings apply to the addon as a whole, not the selected schedule."
        )
        global_help.setWordWrap(True)
        global_help.setStyleSheet("color: palette(mid);")
        global_layout.addWidget(global_help)

        defaults_box = QGroupBox("Automatic apply")
        defaults_layout = QVBoxLayout(defaults_box)
        self.apply_on_profile_open_check = QCheckBox("On profile open")
        self.apply_on_collection_open_check = QCheckBox("On collection open")
        self.apply_once_per_day_check = QCheckBox("At most once per day")
        self.apply_on_sync_check = QCheckBox("After sync")
        self.apply_on_profile_open_check.setToolTip(
            "Apply schedules automatically when Anki opens a profile."
        )
        self.apply_on_collection_open_check.setToolTip(
            "Apply schedules automatically when the collection finishes loading."
        )
        self.apply_once_per_day_check.setToolTip(
            "Skip repeated automatic applications on the same Anki day. Manual apply still always runs."
        )
        self.apply_on_sync_check.setToolTip("Apply schedules automatically when a sync finishes.")
        self.apply_on_profile_open_check.stateChanged.connect(self._on_defaults_changed)
        self.apply_on_collection_open_check.stateChanged.connect(self._on_defaults_changed)
        self.apply_once_per_day_check.stateChanged.connect(self._on_defaults_changed)
        self.apply_on_sync_check.stateChanged.connect(self._on_defaults_changed)
        defaults_layout.addWidget(self.apply_on_profile_open_check)
        defaults_layout.addWidget(self.apply_on_collection_open_check)
        defaults_layout.addWidget(self.apply_once_per_day_check)
        defaults_layout.addWidget(self.apply_on_sync_check)
        global_layout.addWidget(defaults_box)
        global_layout.addStretch(1)

        self.tabs.addTab(self._wrap_scroll_tab(schedule_tab), "Schedule")
        self.tabs.addTab(self._wrap_scroll_tab(global_tab), "Global settings")

        # Buttons
        buttons = QHBoxLayout()
        self.close_btn = QPushButton("Close")
        self.close_btn.setDefault(True)
        self.close_btn.clicked.connect(self._close_dialog)
        buttons.addWidget(self.close_btn)

        right.addWidget(self.tabs, 1)
        right.addLayout(buttons)

        root.addLayout(right, 1)
        root.setStretch(0, 0)
        root.setStretch(1, 1)

        self._update_type_help()
        self._sync_rule_stack_height()

    def _fit_to_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        target_width = min(940, max(820, available.width() - 80))
        target_height = min(560, max(460, available.height() - 140))
        self.resize(target_width, target_height)

    def _wrap_scroll_tab(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    def _build_every_widget(self):
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QFormLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setFormAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.m_spin = QSpinBox()
        self.m_spin.setMinimum(0)
        self.m_spin.setMaximum(365)

        self.n_spin = QSpinBox()
        self.n_spin.setMinimum(1)
        self.n_spin.setMaximum(365)

        self.stagger_mode = QComboBox()
        self._populate_stagger_combo(self.stagger_mode)

        self.stagger_seed = QLineEdit()
        self.stagger_seed.setEnabled(False)
        self.stagger_seed.setPlaceholderText("Unused in stable mode")

        m_label = QLabel("Cards per cycle")
        m_label.setToolTip("How many new cards to introduce per cycle.")
        n_label = QLabel("Cycle length (days)")
        n_label.setToolTip("Length of the cycle in days. The cards are spread evenly across this cycle.")
        layout.addRow(m_label, self.m_spin)
        layout.addRow(n_label, self.n_spin)
        stagger_label = QLabel("Stagger")
        stagger_label.setToolTip("Spread decks across days so they don't all introduce cards on the same day.")
        self.stagger_mode.setToolTip("Controls whether matching decks are offset from each other.")
        self.stagger_help = QLabel()
        self.stagger_help.setWordWrap(True)
        self.stagger_help.setStyleSheet("color: palette(mid);")
        self.stagger_seed_label = QLabel("Advanced seed")
        self.stagger_seed_label.setToolTip("No longer used.")
        self._set_seed_controls_visible(
            self.stagger_seed_label, self.stagger_seed, False
        )
        layout.addRow(stagger_label, self.stagger_mode)
        layout.addRow("", self.stagger_help)
        layout.addRow(self.stagger_seed_label, self.stagger_seed)

        self.m_spin.editingFinished.connect(self._refresh_preview)
        self.n_spin.editingFinished.connect(self._refresh_preview)
        self.m_spin.valueChanged.connect(self._on_numeric_value_changed)
        self.n_spin.valueChanged.connect(self._on_numeric_value_changed)
        self.stagger_mode.currentIndexChanged.connect(self._on_stagger_mode_changed)
        self.stagger_seed.editingFinished.connect(self._refresh_preview)

        return w

    def _build_dow_widget(self):
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        grid = QGridLayout(w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.dow_spins = {}
        for idx, day in enumerate(VALID_DAYS):
            label = QLabel(day)
            spin = QSpinBox()
            spin.setMinimum(0)
            spin.setMaximum(365)
            spin.editingFinished.connect(self._refresh_preview)
            spin.valueChanged.connect(self._on_numeric_value_changed)
            self.dow_spins[day] = spin
            grid.addWidget(label, idx, 0)
            grid.addWidget(spin, idx, 1)

        self.dow_stagger_mode = QComboBox()
        self._populate_stagger_combo(self.dow_stagger_mode)
        self.dow_stagger_mode.setToolTip("Controls whether matching decks are offset from each other.")
        self.dow_stagger_seed = QLineEdit()
        self.dow_stagger_seed.setEnabled(False)
        self.dow_stagger_seed.setPlaceholderText("Unused in stable mode")

        stagger_label = QLabel("Stagger")
        stagger_label.setToolTip("Spread decks across days so they don't all introduce cards on the same day.")
        grid.addWidget(stagger_label, len(VALID_DAYS), 0)
        grid.addWidget(self.dow_stagger_mode, len(VALID_DAYS), 1)
        self.dow_stagger_help = QLabel()
        self.dow_stagger_help.setWordWrap(True)
        self.dow_stagger_help.setStyleSheet("color: palette(mid);")
        grid.addWidget(self.dow_stagger_help, len(VALID_DAYS) + 1, 0, 1, 2)
        self.dow_stagger_seed_label = QLabel("Advanced seed")
        self.dow_stagger_seed_label.setToolTip("No longer used.")
        self._set_seed_controls_visible(
            self.dow_stagger_seed_label, self.dow_stagger_seed, False
        )
        grid.addWidget(self.dow_stagger_seed_label, len(VALID_DAYS) + 2, 0)
        grid.addWidget(self.dow_stagger_seed, len(VALID_DAYS) + 2, 1)

        self.dow_stagger_mode.currentIndexChanged.connect(self._on_dow_stagger_mode_changed)
        self.dow_stagger_seed.editingFinished.connect(self._refresh_preview)

        return w

    def _load_schedule_list(self) -> None:
        self.schedule_list.clear()
        for sched in self.config.get("schedules", []):
            item = QListWidgetItem(str(sched.get("id", "(unnamed)")))
            item.setData(Qt.ItemDataRole.UserRole, str(sched.get("_uid", "")))
            self.schedule_list.addItem(item)

        self._update_schedule_summary()

        if self.schedule_list.count() > 0:
            self.schedule_list.setCurrentRow(0)
            self.btn_copy.setEnabled(True)
        else:
            self._current_uid = None
            self._clear_form()
            self._set_editor_enabled(False)
            self.btn_copy.setEnabled(False)

    def _clear_form(self) -> None:
        self._building = True
        self.id_edit.setText("")
        self.type_combo.setCurrentIndex(0)
        self.leaf_only_check.setChecked(True)
        self.m_spin.setValue(1)
        self.n_spin.setValue(3)
        self._set_stagger_mode(self.stagger_mode, "stable")
        self.stagger_seed.setText("")
        for day in VALID_DAYS:
            self.dow_spins[day].setValue(0)
        self._set_stagger_mode(self.dow_stagger_mode, "stable")
        self.dow_stagger_seed.setText("")
        self.target_list.clear()
        self.target_summary.setText("No targets yet.")
        self._clear_preview_table()
        self.preview_summary.setText("Select a schedule to preview it.")
        self._building = False

    def _on_type_changed(self) -> None:
        idx = self.type_combo.currentIndex()
        self.stack.setCurrentIndex(idx)
        self._update_type_help()
        self._sync_rule_stack_height()
        self._refresh_preview()

    def _on_numeric_value_changed(self, _value: int) -> None:
        self._refresh_preview()

    def _on_schedule_selected(self, row: int) -> None:
        if self._building:
            return
        if self._current_uid is not None:
            self._commit_current()
        self._current_uid = self._selected_uid()
        self.btn_copy.setEnabled(row >= 0)
        self._load_current()

    def _load_current(self) -> None:
        sched = self._current_schedule()
        if sched is None:
            self._clear_form()
            self._set_editor_enabled(False)
            return

        self._building = True
        self._set_editor_enabled(True)

        self.id_edit.setText(str(sched.get("id", "")))
        self.leaf_only_check.setChecked(bool(sched.get("leaf_only", True)))

        sched_type = sched.get("type", "every_n_days")
        self.type_combo.setCurrentIndex(0 if sched_type == "every_n_days" else 1)

        if sched_type == "every_n_days":
            self.m_spin.setValue(int(sched.get("m", 1)))
            self.n_spin.setValue(int(sched.get("n", 3)))
            self._load_stagger(sched, self.stagger_mode, self.stagger_seed)
        else:
            by_day = sched.get("by_day") or {}
            for day in VALID_DAYS:
                self.dow_spins[day].setValue(int(by_day.get(day, 0) or 0))
            self._load_stagger(sched, self.dow_stagger_mode, self.dow_stagger_seed)

        self.target_list.clear()
        for t in sched.get("targets", []) or []:
            self.target_list.addItem(QListWidgetItem(str(t)))

        self._building = False
        self._refresh_preview()

    def _load_stagger(self, sched: Dict[str, Any], mode_combo: QComboBox, seed_edit: QLineEdit) -> None:
        stagger = sched.get("stagger")
        if not stagger:
            self._set_stagger_mode(mode_combo, "none")
            seed_edit.setText("")
            if seed_edit is self.stagger_seed:
                self._set_seed_controls_visible(
                    self.stagger_seed_label, self.stagger_seed, False
                )
                self._update_stagger_help(self.stagger_help, "none")
            if seed_edit is self.dow_stagger_seed:
                self._set_seed_controls_visible(
                    self.dow_stagger_seed_label, self.dow_stagger_seed, False
                )
                self._update_stagger_help(self.dow_stagger_help, "none")
            return
        mode = "stable" if stagger.get("mode") in {"stable", "balanced", "hash"} else "none"
        self._set_stagger_mode(mode_combo, mode)
        seed_edit.setText("")
        if seed_edit is self.stagger_seed:
            self._set_seed_controls_visible(self.stagger_seed_label, self.stagger_seed, False)
            self._update_stagger_help(self.stagger_help, mode)
        if seed_edit is self.dow_stagger_seed:
            self._set_seed_controls_visible(
                self.dow_stagger_seed_label, self.dow_stagger_seed, False
            )
            self._update_stagger_help(self.dow_stagger_help, mode)
        self._sync_rule_stack_height()

    def _commit_current(self) -> None:
        sched = self._current_schedule()
        if sched is None:
            return

        current_uid = str(sched.get("_uid", ""))
        sched_type = "every_n_days" if self.type_combo.currentIndex() == 0 else "dow"
        targets = [self.target_list.item(i).text() for i in range(self.target_list.count())]

        raw_id = self.id_edit.text().strip() or sched.get("id", "schedule")
        new_id = self._ensure_unique_id(raw_id, exclude_uid=current_uid)
        updated: Dict[str, Any] = {
            "_uid": current_uid,
            "id": new_id,
            "type": sched_type,
            "targets": targets,
            "leaf_only": self.leaf_only_check.isChecked(),
        }
        if isinstance(sched.get("stagger_state"), dict):
            updated["stagger_state"] = dict(sched["stagger_state"])

        if sched_type == "every_n_days":
            updated["m"] = int(self.m_spin.value())
            updated["n"] = int(self.n_spin.value())
            self._store_stagger(updated, self.stagger_mode, self.stagger_seed)
        else:
            updated["by_day"] = {day: int(self.dow_spins[day].value()) for day in VALID_DAYS}
            self._store_stagger(updated, self.dow_stagger_mode, self.dow_stagger_seed)

        schedules = self.config.get("schedules", [])
        for idx, existing in enumerate(schedules):
            if str(existing.get("_uid", "")) == updated["_uid"]:
                schedules[idx] = updated
                break
        self.config["schedules"] = schedules
        self._persist_config()
        item = self._item_for_uid(current_uid)
        if item:
            item.setText(updated["id"])
            item.setData(Qt.ItemDataRole.UserRole, updated["_uid"])

    def _store_stagger(self, sched: Dict[str, Any], mode_combo: QComboBox, seed_edit: QLineEdit) -> None:
        mode = self._stagger_mode_value(mode_combo)
        if mode == "none":
            sched.pop("stagger", None)
            return
        sched["stagger"] = {"mode": "stable"}

    def _add_schedule(self) -> None:
        if self._current_uid is not None:
            self._commit_current()

        schedules = self.config.get("schedules", [])
        new_id = self._next_schedule_name()
        sched = {
            "_uid": str(uuid.uuid4()),
            "id": new_id,
            "type": "every_n_days",
            "m": 1,
            "n": 3,
            "targets": [],
            "leaf_only": True,
            "stagger": {"mode": "stable"},
        }
        schedules.append(sched)
        self.config["schedules"] = schedules
        self._persist_config()

        item = QListWidgetItem(new_id)
        item.setData(Qt.ItemDataRole.UserRole, sched["_uid"])
        self.schedule_list.addItem(item)
        self.schedule_list.setCurrentRow(self.schedule_list.count() - 1)
        self._current_uid = sched["_uid"]
        self._set_editor_enabled(True)
        self._update_schedule_summary()

    def _copy_schedule(self) -> None:
        row = self.schedule_list.currentRow()
        if row < 0:
            return
        if self._current_uid is not None:
            self._commit_current()
        schedules = self.config.get("schedules", [])
        base = self._current_schedule()
        if base is None:
            return
        new_id = self._ensure_unique_id(f"{base.get('id', 'Schedule')} Copy")
        copied = {
            "_uid": str(uuid.uuid4()),
            "id": new_id,
            "type": base.get("type", "every_n_days"),
            "targets": list(base.get("targets", [])),
            "leaf_only": bool(base.get("leaf_only", True)),
            "stagger": dict(base.get("stagger", {})) if base.get("stagger") else {"mode": "stable"},
        }
        if copied["type"] == "every_n_days":
            copied["m"] = int(base.get("m", 1))
            copied["n"] = int(base.get("n", 3))
        else:
            copied["by_day"] = dict(base.get("by_day", {}))

        schedules.append(copied)
        self.config["schedules"] = schedules
        self._persist_config()
        item = QListWidgetItem(copied["id"])
        item.setData(Qt.ItemDataRole.UserRole, copied["_uid"])
        self.schedule_list.addItem(item)
        self.schedule_list.setCurrentRow(self.schedule_list.count() - 1)
        self._current_uid = copied["_uid"]
        self._set_editor_enabled(True)
        self._update_schedule_summary()

    def _remove_schedule(self) -> None:
        row = self.schedule_list.currentRow()
        if row < 0:
            return
        uid = self._selected_uid()
        schedules = self.config.get("schedules", [])
        self.config["schedules"] = [
            sched for sched in schedules if str(sched.get("_uid", "")) != uid
        ]
        self._persist_config()
        self.schedule_list.takeItem(row)
        self._update_schedule_summary()
        if self.schedule_list.count() > 0:
            self.schedule_list.setCurrentRow(min(row, self.schedule_list.count() - 1))
        else:
            self._current_uid = None
            self._clear_form()
            self._set_editor_enabled(False)

    def _on_schedule_reordered(self) -> None:
        if self._current_uid is not None:
            self._commit_current()
        schedules = self.config.get("schedules", [])
        if len(schedules) != self.schedule_list.count():
            return
        by_uid = {str(sched.get("_uid", "")): sched for sched in schedules}
        new_order: List[Dict[str, Any]] = []
        for i in range(self.schedule_list.count()):
            item = self.schedule_list.item(i)
            uid = item.data(Qt.ItemDataRole.UserRole) if item else None
            sched = by_uid.get(str(uid))
            if sched is not None:
                new_order.append(sched)
        if len(new_order) != len(schedules):
            return
        self.config["schedules"] = new_order
        self._persist_config()

    def _add_target(self) -> None:
        text = self.target_input.text().strip()
        if not text:
            return
        self._append_target(text)
        self.target_input.setText("")

    def _pick_target(self) -> None:
        dialog = DeckPickerDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selection = dialog.selected_deck()
        if not selection:
            return
        self._append_target(selection)

    def _pick_wildcard_target(self) -> None:
        dialog = DeckPickerDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selection = dialog.selected_deck()
        if not selection:
            return
        self._append_target(f"{selection}*")

    def _remove_target(self) -> None:
        row = self.target_list.currentRow()
        if row < 0:
            return
        self.target_list.takeItem(row)
        self._refresh_preview()

    def _append_target(self, text: str) -> None:
        normalized = text.strip()
        if not normalized:
            return
        for row in range(self.target_list.count()):
            item = self.target_list.item(row)
            if item is not None and item.text() == normalized:
                self.target_list.setCurrentRow(row)
                self._refresh_preview()
                return
        self.target_list.addItem(QListWidgetItem(normalized))
        self.target_list.setCurrentRow(self.target_list.count() - 1)
        self._refresh_preview()

    def _rebalance_current_schedule(self) -> None:
        if self._building or mw is None or mw.col is None:
            return
        self._commit_current()
        sched = self._current_schedule()
        if sched is None:
            return
        rebalance_schedule_offsets(mw.col, sched)
        self._persist_config()
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self._building or mw is None or mw.col is None:
            return
        self._commit_current()
        if self._current_uid is None:
            return

        sched = self._current_schedule()
        if sched is None:
            return

        deck_names = _all_deck_names()
        raw_matches = match_deck_names(sched.get("targets", []), deck_names)
        matches = filter_deck_names_for_schedule(sched, deck_names)
        skipped = max(0, len(raw_matches) - len(matches))
        self._update_target_summary(
            raw_matches,
            matches,
            skipped,
            bool(sched.get("leaf_only", True)),
        )
        if not matches:
            self.preview_summary.setText("No decks will receive limits with the current targets.")
            self._clear_preview_table()
            return

        data = preview_schedule(
            mw.col,
            sched,
            matches,
            self.config.get("epoch", "2026-01-01"),
            days=PREVIEW_DAYS,
        )
        summary_kind = "leaf decks" if sched.get("leaf_only", True) else "decks"
        self.preview_summary.setText(
            f"{len(matches)} matching {summary_kind}. Scroll to see all decks and daily totals."
        )
        self._populate_preview_table(self._sorted_preview_names(matches, data), data)

    def _load_defaults(self) -> None:
        self._building = True
        defaults = self.config.get("defaults", DEFAULT_CONFIG["defaults"])
        self.apply_on_profile_open_check.setChecked(
            bool(defaults.get("apply_on_profile_open", True))
        )
        self.apply_on_collection_open_check.setChecked(
            bool(defaults.get("apply_on_collection_open", True))
        )
        self.apply_once_per_day_check.setChecked(
            bool(defaults.get("apply_once_per_day", True))
        )
        self.apply_on_sync_check.setChecked(bool(defaults.get("apply_on_sync", False)))
        self._building = False

    def _on_defaults_changed(self) -> None:
        if self._building:
            return
        if self._current_uid is not None:
            self._commit_current()
        defaults = dict(self.config.get("defaults", DEFAULT_CONFIG["defaults"]))
        defaults["apply_on_profile_open"] = self.apply_on_profile_open_check.isChecked()
        defaults["apply_on_collection_open"] = self.apply_on_collection_open_check.isChecked()
        defaults["apply_once_per_day"] = self.apply_once_per_day_check.isChecked()
        defaults["apply_on_sync"] = self.apply_on_sync_check.isChecked()
        self.config["defaults"] = defaults
        self._persist_config()

    def _close_dialog(self) -> None:
        self._commit_current()
        self._persist_config()
        self.accept()

    def reject(self) -> None:
        self._commit_current()
        self._persist_config()
        super().reject()

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self.id_edit,
            self.type_combo,
            self.leaf_only_check,
            self.m_spin,
            self.n_spin,
            self.stagger_mode,
            self.stagger_seed,
            self.dow_stagger_mode,
            self.dow_stagger_seed,
            self.target_input,
            self.target_pick,
            self.target_pick_wildcard,
            self.target_add,
            self.target_remove,
            self.target_list,
            self.rebalance_btn,
        ):
            widget.setEnabled(enabled)
        self.btn_add.setAutoDefault(False)
        self.btn_copy.setAutoDefault(False)
        self.btn_remove.setAutoDefault(False)
        self.target_pick.setAutoDefault(False)
        self.target_pick_wildcard.setAutoDefault(False)
        self.target_add.setAutoDefault(False)
        self.target_remove.setAutoDefault(False)
        self.btn_copy.setEnabled(enabled and self.schedule_list.currentRow() >= 0)
        if not enabled:
            self._set_seed_controls_visible(
                self.stagger_seed_label, self.stagger_seed, False
            )
            self._set_seed_controls_visible(
                self.dow_stagger_seed_label, self.dow_stagger_seed, False
            )

    def _on_stagger_mode_changed(self) -> None:
        mode = self._stagger_mode_value(self.stagger_mode)
        self._set_seed_controls_visible(self.stagger_seed_label, self.stagger_seed, False)
        self._update_stagger_help(self.stagger_help, mode)
        self._sync_rule_stack_height()
        self._refresh_preview()

    def _on_dow_stagger_mode_changed(self) -> None:
        mode = self._stagger_mode_value(self.dow_stagger_mode)
        self._set_seed_controls_visible(self.dow_stagger_seed_label, self.dow_stagger_seed, False)
        self._update_stagger_help(self.dow_stagger_help, mode)
        self._sync_rule_stack_height()
        self._refresh_preview()

    def _populate_stagger_combo(self, combo: QComboBox) -> None:
        for label, value in STAGGER_OPTIONS:
            combo.addItem(label, value)

    def _stagger_mode_value(self, combo: QComboBox) -> str:
        value = combo.currentData()
        return str(value) if value else "stable"

    def _set_stagger_mode(self, combo: QComboBox, mode: str) -> None:
        idx = combo.findData(mode)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _update_stagger_help(self, label: QLabel, mode: str) -> None:
        label.setText(STAGGER_DESCRIPTIONS.get(mode, ""))

    def _update_type_help(self) -> None:
        sched_type = "every_n_days" if self.type_combo.currentIndex() == 0 else "dow"
        self.type_help.setText(TYPE_DESCRIPTIONS.get(sched_type, ""))

    def _clear_preview_table(self) -> None:
        self.preview_table.clear()
        self.preview_table.setRowCount(0)
        self.preview_table.setColumnCount(0)

    def _populate_preview_table(
        self, deck_names: List[str], data: Dict[str, List[int]]
    ) -> None:
        headers = ["Deck", *self._preview_day_headers(PREVIEW_DAYS)]
        self._preview_width_update_in_progress = True
        self.preview_table.clear()
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        self.preview_table.setRowCount(len(deck_names) + 1)
        self.preview_table.setColumnWidth(0, 280)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        for column in range(1, len(headers)):
            self.preview_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Interactive
            )
            self.preview_table.setColumnWidth(column, 52)
        self._apply_preview_column_widths(len(headers))

        totals = [0] * PREVIEW_DAYS
        for row, deck_name in enumerate(deck_names):
            self._set_preview_item(
                row,
                0,
                deck_name,
                align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                tooltip=deck_name,
            )
            seq = data.get(deck_name, [])
            for day, value in enumerate(seq):
                totals[day] += int(value)
                self._set_preview_value_cell(row, day + 1, int(value))

        total_row = len(deck_names)
        self._set_preview_item(
            total_row,
            0,
            "Daily total",
            align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            bold=True,
            background=QColor("#e8f1ff"),
        )
        for day, total in enumerate(totals):
            self._set_preview_item(
                total_row,
                day + 1,
                str(total),
                align=Qt.AlignmentFlag.AlignCenter,
                bold=True,
                background=QColor("#e8f1ff"),
                foreground=QColor("#0b5394"),
            )

        self.preview_table.resizeRowsToContents()
        self._preview_width_update_in_progress = False

    def _preview_day_headers(self, days: int) -> List[str]:
        headers = []
        for offset in range(days):
            current = date.today() + timedelta(days=offset)
            headers.append(f"{current.strftime('%a')}\n{current.strftime('%d')}")
        return headers

    def _sorted_preview_names(
        self, deck_names: List[str], data: Dict[str, List[int]]
    ) -> List[str]:
        return sorted(
            deck_names,
            key=lambda name: (tuple(int(v) for v in data.get(name, [])), name.lower()),
        )

    def _apply_preview_column_widths(self, column_count: int) -> None:
        widths = self._saved_preview_column_widths()
        if not widths:
            return
        for column in range(min(column_count, len(widths))):
            width = widths[column]
            if width > 24:
                self.preview_table.setColumnWidth(column, width)

    def _saved_preview_column_widths(self) -> List[int]:
        defaults = self.config.get("defaults", {})
        widths = defaults.get("preview_column_widths")
        if not isinstance(widths, list):
            return []
        saved: List[int] = []
        for value in widths:
            try:
                width = int(value)
            except Exception:
                continue
            if width > 0:
                saved.append(width)
        return saved

    def _store_preview_column_widths(self) -> None:
        widths = [
            int(self.preview_table.columnWidth(column))
            for column in range(self.preview_table.columnCount())
        ]
        defaults = dict(self.config.get("defaults", DEFAULT_CONFIG["defaults"]))
        defaults["preview_column_widths"] = widths
        self.config["defaults"] = defaults

    def _on_preview_column_resized(
        self, _logical_index: int, _old_size: int, _new_size: int
    ) -> None:
        if self._preview_width_update_in_progress or self._building:
            return
        self._store_preview_column_widths()

    def _set_preview_value_cell(self, row: int, column: int, value: int) -> None:
        if value <= 0:
            self._set_preview_item(
                row,
                column,
                "0",
                align=Qt.AlignmentFlag.AlignCenter,
                background=QColor("#f5f7fa"),
                foreground=QColor("#97a1af"),
            )
            return
        self._set_preview_item(
            row,
            column,
            str(value),
            align=Qt.AlignmentFlag.AlignCenter,
            bold=True,
            background=QColor("#dff2e4"),
            foreground=QColor("#1f6b36"),
        )

    def _set_preview_item(
        self,
        row: int,
        column: int,
        text: str,
        *,
        align: Qt.AlignmentFlag,
        bold: bool = False,
        background: Optional[QColor] = None,
        foreground: Optional[QColor] = None,
        tooltip: Optional[str] = None,
    ) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(int(align))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable & ~Qt.ItemFlag.ItemIsSelectable)
        if tooltip:
            item.setToolTip(tooltip)
        if background is not None:
            item.setBackground(QBrush(background))
        if foreground is not None:
            item.setForeground(QBrush(foreground))
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        self.preview_table.setItem(row, column, item)

    def _set_seed_controls_visible(
        self, label: QLabel, field: QLineEdit, visible: bool
    ) -> None:
        label.setVisible(visible)
        field.setVisible(visible)
        field.setEnabled(visible)

    def _sync_rule_stack_height(self) -> None:
        current = self.stack.currentWidget()
        if current is None:
            return
        current.adjustSize()
        layout = current.layout()
        content_height = (
            layout.sizeHint().height() if layout is not None else current.sizeHint().height()
        )
        self.stack.setFixedHeight(content_height)
        self.rule_box.setFixedHeight(content_height + 38)

    def _next_schedule_name(self) -> str:
        index = 1
        while True:
            candidate = f"Schedule {index}"
            if candidate == self._ensure_unique_id(candidate):
                return candidate
            index += 1

    def _update_schedule_summary(self) -> None:
        count = self.schedule_list.count()
        self.schedule_heading.setText(f"Schedules ({count})")
        if count == 0:
            self.schedule_summary.setText("No schedules configured yet.")
        elif count == 1:
            self.schedule_summary.setText("1 schedule configured.")
        else:
            self.schedule_summary.setText(f"{count} schedules configured.")

    def _update_target_summary(
        self,
        raw_matches: List[str],
        applied_matches: List[str],
        skipped: int,
        leaf_only: bool,
    ) -> None:
        if not raw_matches:
            self.target_summary.setText("No matching decks for the current targets.")
            return
        if leaf_only and skipped:
            self.target_summary.setText(
                f"{len(raw_matches)} decks matched. {len(applied_matches)} leaf decks will receive limits; {skipped} parent decks are skipped."
            )
            return
        self.target_summary.setText(
            f"{len(applied_matches)} decks will receive limits with the current targets."
        )

    def _ensure_unique_id(self, base_id: str, exclude_uid: Optional[str] = None) -> str:
        existing = []
        for sched in self.config.get("schedules", []):
            if exclude_uid is not None and str(sched.get("_uid", "")) == exclude_uid:
                continue
            existing.append(str(sched.get("id", "")))
        if base_id not in existing:
            return base_id
        i = 2
        while f"{base_id}-{i}" in existing:
            i += 1
        return f"{base_id}-{i}"

    def _selected_item(self) -> Optional[QListWidgetItem]:
        row = self.schedule_list.currentRow()
        if row < 0:
            return None
        return self.schedule_list.item(row)

    def _item_for_uid(self, uid: Optional[str]) -> Optional[QListWidgetItem]:
        if not uid:
            return None
        for row in range(self.schedule_list.count()):
            item = self.schedule_list.item(row)
            if item and str(item.data(Qt.ItemDataRole.UserRole) or "") == uid:
                return item
        return None

    def _selected_uid(self) -> Optional[str]:
        item = self._selected_item()
        if item is None:
            return None
        uid = item.data(Qt.ItemDataRole.UserRole)
        return str(uid) if uid else None

    def _current_schedule(self) -> Optional[Dict[str, Any]]:
        if self._current_uid is None:
            return None
        for sched in self.config.get("schedules", []):
            if str(sched.get("_uid", "")) == self._current_uid:
                return sched
        return None

    def _persist_config(self) -> None:
        if mw is None:
            return
        mw.addonManager.writeConfig(self.module, config_to_dict(self.config))


def _all_deck_names() -> List[str]:
    if mw is None or mw.col is None:
        return []
    decks = mw.col.decks
    if hasattr(decks, "all_names_and_ids"):
        try:
            names = []
            for entry in decks.all_names_and_ids():
                if isinstance(entry, dict):
                    name = entry.get("name")
                    deck_id = entry.get("id")
                elif hasattr(entry, "name"):
                    name = getattr(entry, "name", None)
                    deck_id = getattr(entry, "id", None)
                else:
                    try:
                        name, deck_id = entry
                    except Exception:
                        name = None
                        deck_id = None
                if name:
                    deck = decks.get(deck_id) if deck_id is not None else None
                    if isinstance(deck, dict) and deck.get("dyn"):
                        continue
                    names.append(str(name))
            return names
        except Exception:
            pass
    if hasattr(decks, "all"):
        try:
            return [
                d.get("name")
                for d in decks.all()
                if isinstance(d, dict) and d.get("name") and not d.get("dyn")
            ]
        except Exception:
            pass
    if hasattr(decks, "all_names"):
        try:
            return list(decks.all_names())
        except Exception:
            pass
    return []


class DeckPickerDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick Deck")
        self.resize(420, 520)

        layout = QVBoxLayout(self)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter decks...")
        self.filter_edit.textChanged.connect(self._refresh_list)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._accept)

        btn_row = QHBoxLayout()
        self.ok_btn = QPushButton("Use Selected")
        self.cancel_btn = QPushButton("Cancel")
        self.ok_btn.clicked.connect(self._accept)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)

        layout.addWidget(self.filter_edit)
        layout.addWidget(self.list_widget, 1)
        layout.addLayout(btn_row)

        self._refresh_list()

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        needle = self.filter_edit.text().strip().lower()
        for name in _all_deck_names():
            if needle and needle not in name.lower():
                continue
            self.list_widget.addItem(QListWidgetItem(name))

    def _accept(self) -> None:
        if self.list_widget.currentItem() is None:
            return
        self.accept()

    def selected_deck(self) -> Optional[str]:
        item = self.list_widget.currentItem()
        if not item:
            return None
        return item.text()
