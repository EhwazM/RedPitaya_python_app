"""Saved Red Pitaya connections (hostname / IPv4 / password) kept in a JSON file.

Public API used by main.py:

    store = SavedConnectionsStore()
    store.next_default_name()                 # "rp_1", "rp_2", ... first free
    store.upsert({...})                       # add or overwrite by name
    store.update(old_name, {...})             # edit in place (may rename)
    dialog = SavedConnectionsDialog(store, parent)
    if dialog.exec() == QDialog.Accepted:
        entry = dialog.selected               # dict with the keys below

Each entry is a dict:  {"name", "hostname", "ip", "password"}

SECURITY NOTE: passwords are stored in PLAIN TEXT. The file is created
with owner-only permissions (0600) where the OS supports it, but anyone
with access to your user account can read it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

# Change this if you'd rather keep the file next to the application.
DEFAULT_PATH = Path.home() / ".red_pitaya_suite" / "saved_connections.json"

_KEYS = ("name", "hostname", "ip", "password")


def _normalize(entry: dict) -> dict:
    return {
        k: (str(entry.get(k, "")) if k == "password" else str(entry.get(k, "")).strip())
        for k in _KEYS
    }


# ============================================================================
# Storage (no Qt)
# ============================================================================

class SavedConnectionsStore:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            raw = data["connections"]
            if not isinstance(raw, list):
                raise ValueError("'connections' is not a list")
        except (OSError, ValueError, KeyError, TypeError):
            # Unreadable / malformed: keep a copy instead of silently
            # overwriting it on the next save.
            try:
                self.path.replace(self.path.with_suffix(".json.bak"))
            except OSError:
                pass
            return []

        entries = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            entry = {k: str(item.get(k, "")) for k in _KEYS}
            if entry["name"]:
                entries.append(entry)
        return entries

    def _write(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename, so a crash can't leave a
        # half-written JSON behind.
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=".tmp_", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"connections": entries}, fh, indent=2)
            try:
                os.chmod(tmp_name, 0o600)
            except OSError:
                pass  # e.g. Windows
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def exists(self, name: str) -> bool:
        return any(e["name"] == name for e in self.load())

    def upsert(self, entry: dict) -> bool:
        """Add the entry, or replace the one with the same name.
        Returns True if an existing entry was replaced."""
        entry = _normalize(entry)
        entries = self.load()
        for i, existing in enumerate(entries):
            if existing["name"] == entry["name"]:
                entries[i] = entry
                self._write(entries)
                return True
        entries.append(entry)
        self._write(entries)
        return False

    def update(self, old_name: str, entry: dict) -> None:
        """Replace the entry called `old_name` (keeping its position in the
        list). The name may change; any *other* entry already using the new
        name is dropped, so callers should confirm that with the user first."""
        entry = _normalize(entry)
        entries = self.load()
        if not any(e["name"] == old_name for e in entries):
            self.upsert(entry)
            return
        result = []
        for existing in entries:
            if existing["name"] == old_name:
                result.append(entry)
            elif existing["name"] != entry["name"]:
                result.append(existing)
        self._write(result)

    def next_default_name(self) -> str:
        """First unused name of the form rp_1, rp_2, ..."""
        used = {e["name"] for e in self.load()}
        n = 1
        while f"rp_{n}" in used:
            n += 1
        return f"rp_{n}"

    def delete(self, name: str) -> bool:
        entries = self.load()
        kept = [e for e in entries if e["name"] != name]
        if len(kept) == len(entries):
            return False
        self._write(kept)
        return True


# ============================================================================
# Dialogs
# ============================================================================

class ConnectionDialog(QDialog):
    """Form used both to add a new connection and to edit an existing one."""

    def __init__(self, store: SavedConnectionsStore, parent=None, entry: dict | None = None):
        super().__init__(parent)
        self.store = store
        self._original_name = entry["name"] if entry else None
        self.entry: dict | None = None
        self.setWindowTitle("Edit Red Pitaya" if entry else "Add Red Pitaya")
        self.setMinimumWidth(340)

        form = QFormLayout()
        self.name_edit = QLineEdit(entry["name"] if entry else store.next_default_name())
        self.name_edit.setPlaceholderText("rp_1")
        self.host_edit = QLineEdit(entry["hostname"] if entry else "")
        self.host_edit.setPlaceholderText("rp-f0XXXXX.local")
        self.ip_edit = QLineEdit(entry["ip"] if entry else "")
        self.ip_edit.setPlaceholderText("192.168.1.100")
        self.password_edit = QLineEdit(entry["password"] if entry else "")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password_check = QCheckBox("Show password")
        self.show_password_check.toggled.connect(
            lambda on: self.password_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        form.addRow("Name:", self.name_edit)
        form.addRow("Hostname:", self.host_edit)
        form.addRow("IPv4 address:", self.ip_edit)
        form.addRow("Password:", self.password_edit)
        form.addRow("", self.show_password_check)

        ok_button = QPushButton("Save" if entry else "Add")
        ok_button.setDefault(True)
        ok_button.clicked.connect(self._on_accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(ok_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def _on_accept(self) -> None:
        hostname = self.host_edit.text().strip()
        ip = self.ip_edit.text().strip()
        password = self.password_edit.text()
        name = self.name_edit.text().strip() or self.store.next_default_name()

        if not (hostname or ip):
            QMessageBox.warning(
                self, "Missing Host",
                "Enter a hostname or an IPv4 address.",
            )
            return
        if not password:
            QMessageBox.warning(
                self, "Missing Password", "Enter the Red Pitaya's password."
            )
            return

        if name != self._original_name and self.store.exists(name):
            reply = QMessageBox.question(
                self, "Overwrite?",
                f"A saved connection named \"{name}\" already exists.\n"
                "Replace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        entry = {
            "name": name, "hostname": hostname, "ip": ip, "password": password,
        }
        try:
            if self._original_name is None:
                self.store.upsert(entry)
            else:
                self.store.update(self._original_name, entry)
        except OSError as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not write file:\n{exc}")
            return
        self.entry = entry
        self.accept()


class SavedConnectionsDialog(QDialog):
    """Pick a saved connection; Add and Delete live here too."""

    def __init__(self, store: SavedConnectionsStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.selected: dict | None = None
        self.setWindowTitle("Saved Red Pitayas")
        self.setMinimumSize(440, 300)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._on_select())
        self.list_widget.currentItemChanged.connect(
            lambda *_: self._update_buttons()
        )

        self.empty_label = QLabel("No saved connections yet. Use \"Add\".")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.select_button = QPushButton("Select")
        self.select_button.setDefault(True)
        self.select_button.clicked.connect(self._on_select)
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self._on_add)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._on_edit)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._on_delete)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(self.select_button)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(self.empty_label)
        layout.addLayout(buttons)

        self._reload()

    def _reload(self, select_name: str | None = None) -> None:
        self.list_widget.clear()
        for entry in self.store.load():
            address = entry["ip"] or entry["hostname"]
            item = QListWidgetItem(f"{entry['name']}   ({address})")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.list_widget.addItem(item)
            if entry["name"] == select_name:
                self.list_widget.setCurrentItem(item)
        if self.list_widget.currentItem() is None and self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self.empty_label.setVisible(self.list_widget.count() == 0)
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_selection = self.list_widget.currentItem() is not None
        self.select_button.setEnabled(has_selection)
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def _current_entry(self) -> dict | None:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _on_select(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        self.selected = entry
        self.accept()

    def _on_add(self) -> None:
        dialog = ConnectionDialog(self.store, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.entry:
            self._reload(select_name=dialog.entry["name"])

    def _on_edit(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        dialog = ConnectionDialog(self.store, self, entry=entry)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.entry:
            self._reload(select_name=dialog.entry["name"])

    def _on_delete(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        reply = QMessageBox.question(
            self, "Delete Connection",
            f"Delete \"{entry['name']}\" from the saved connections?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(entry["name"])
        except OSError as exc:
            QMessageBox.critical(self, "Delete Failed", f"Could not write file:\n{exc}")
            return
        self._reload()