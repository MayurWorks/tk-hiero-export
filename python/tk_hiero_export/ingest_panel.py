# MIT License
#
# Copyright (c) 2026 SlateX
#
# "SlateX: Ingest EXR Plates" - a panel inside Hiero. Pick EXR sequences from
# the file browser (or drop them in); each one is matched to a ShotGrid Shot
# and Sequence, missing ones are created by the normal ingest, and the plates
# are published. The ingest rules/gate are the same ones the export uses.

import os

from sgtk.platform.qt import QtCore, QtGui

try:
    from . import ingest, ingest_naming, ingest_plan, ingest_scan, ingest_timeline
except ImportError:  # pragma: no cover - tests load the modules flat
    import ingest, ingest_naming, ingest_plan, ingest_scan, ingest_timeline

COLUMNS = ["Use", "Sequence", "Shot", "Frames", "Size", "Match", "Notes / result"]
C_USE, C_SEQ, C_SHOT, C_FRAMES, C_SIZE, C_MATCH, C_NOTES = range(7)
POLL_MS = 1000
POLL_TIMEOUT_S = 15 * 60


class IngestPanel(QtGui.QWidget):
    def __init__(self, app, parent=None):
        super(IngestPanel, self).__init__(parent)
        self._app = app
        self._items = []
        self._planner = None
        self._running = False
        self._poll_started = None
        self._building = False
        self.setAcceptDrops(True)
        self.resize(1150, 640)

        # -- file browser ---------------------------------------------------
        root = app.get_setting("ingest_browse_root") or os.path.expanduser("~")
        self._model = QtGui.QFileSystemModel(self)
        self._model.setRootPath(root)
        self._model.setNameFilters(["*.exr"])
        self._model.setNameFilterDisables(False)
        self._tree = QtGui.QTreeView()
        self._tree.setModel(self._model)
        self._tree.setRootIndex(self._model.index(root))
        self._tree.setSelectionMode(QtGui.QAbstractItemView.ExtendedSelection)
        for col in (1, 2, 3):
            self._tree.hideColumn(col)
        add_sel = QtGui.QPushButton("Add selected →")
        add_sel.clicked.connect(self._add_selected)
        add_dir = QtGui.QPushButton("Add folder...")
        add_dir.clicked.connect(self._add_folder)
        left = QtGui.QVBoxLayout()
        left.addWidget(QtGui.QLabel("EXR files / folders (drag & drop works too)"))
        left.addWidget(self._tree)
        row = QtGui.QHBoxLayout()
        row.addWidget(add_sel)
        row.addWidget(add_dir)
        left.addLayout(row)
        left_w = QtGui.QWidget()
        left_w.setLayout(left)

        # -- plan table -----------------------------------------------------
        self._table = QtGui.QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemChanged.connect(self._on_item_changed)
        self._auto = QtGui.QCheckBox("Ingest automatically when plates are added")
        self._auto.setChecked(bool(app.get_setting("ingest_auto_start")))
        self._go = QtGui.QPushButton("Ingest now")
        self._go.clicked.connect(self.start)
        clear = QtGui.QPushButton("Clear list")
        clear.clicked.connect(self.clear)
        self._log = QtGui.QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(110)
        bar = QtGui.QHBoxLayout()
        bar.addWidget(self._auto)
        bar.addStretch(1)
        bar.addWidget(clear)
        bar.addWidget(self._go)
        right = QtGui.QVBoxLayout()
        right.addWidget(self._table)
        right.addLayout(bar)
        right.addWidget(self._log)
        right_w = QtGui.QWidget()
        right_w.setLayout(right)

        split = QtGui.QSplitter()
        split.addWidget(left_w)
        split.addWidget(right_w)
        split.setStretchFactor(1, 3)
        lay = QtGui.QVBoxLayout(self)
        lay.addWidget(split)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll_session)

    # -- input ----------------------------------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])

    def _add_selected(self):
        paths = [self._model.filePath(i) for i in self._tree.selectionModel().selectedRows(0)]
        self.add_paths(paths)

    def _add_folder(self):
        folder = QtGui.QFileDialog.getExistingDirectory(self, "Add folder of EXR plates")
        if folder:
            self.add_paths([folder])

    def clear(self):
        if self._running:
            return
        self._items = []
        self._table.setRowCount(0)

    def add_paths(self, paths):
        sequences, skipped = ingest_scan.find_sequences(paths)
        for s in skipped:
            self._say("skipped: %s" % s)
        if not sequences:
            self._say("no EXR sequences found")
            return
        known = set(i.sequence.pattern for i in self._items)
        sequences = [s for s in sequences if s.pattern not in known]
        if not sequences:
            return
        try:
            planner = self._make_planner()
        except Exception as exc:
            self._say("cannot reach ShotGrid: %s" % exc)
            return
        self._planner = planner
        self._items.extend(planner.build(sequences))
        planner.evaluate_all(self._items)
        self._refresh()
        if self._auto.isChecked():
            QtCore.QTimer.singleShot(0, self.start)

    def _make_planner(self):
        app = self._app
        rules = app.get_setting("ingest_sequence_rules") or None
        if rules:  # custom rules come first, the built-in E<n> -> EP_<n> after them
            rules = [dict(r) for r in rules] + list(ingest_naming.DEFAULT_RULES)
        strip = app.get_setting("ingest_strip_suffix")
        kwargs = {}
        if rules:
            kwargs["rules"] = rules
        if strip is not None and strip != "":
            kwargs["strip_suffix"] = strip
        return ingest_plan.Planner(
            ingest.load_shots(app), ingest.load_sequences(app),
            ingest.settings_from_app(app), **kwargs
        )

    # -- table ----------------------------------------------------------------
    def _refresh(self):
        self._building = True
        try:
            self._table.setRowCount(len(self._items))
            for r, it in enumerate(self._items):
                use = QtGui.QTableWidgetItem()
                use.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
                use.setCheckState(QtCore.Qt.Checked if it.enabled else QtCore.Qt.Unchecked)
                h = it.header or {}
                size = "%sx%s" % (h["width"], h["height"]) if h else "?"
                frames = "%d-%d (%d)" % (it.sequence.first, it.sequence.last, it.sequence.count)
                if it.sequence.missing:
                    frames += " %d missing" % len(it.sequence.missing)
                cells = {
                    C_USE: use,
                    C_SEQ: QtGui.QTableWidgetItem(it.sequence_code or ""),
                    C_SHOT: QtGui.QTableWidgetItem(it.shot_code or ""),
                    C_FRAMES: QtGui.QTableWidgetItem(frames),
                    C_SIZE: QtGui.QTableWidgetItem(size),
                    C_MATCH: QtGui.QTableWidgetItem(it.status),
                    C_NOTES: QtGui.QTableWidgetItem(it.message),
                }
                for c, cell in cells.items():
                    if c not in (C_USE, C_SEQ, C_SHOT):
                        cell.setFlags(QtCore.Qt.ItemIsEnabled)
                    self._table.setItem(r, c, cell)
                    if c != C_USE and not it.ok:
                        cell.setForeground(QtGui.QBrush(QtGui.QColor("#d9534f")))
        finally:
            self._building = False

    def _on_item_changed(self, cell):
        if self._building or self._running or not self._planner:
            return
        r, c = cell.row(), cell.column()
        if r >= len(self._items):
            return
        it = self._items[r]
        if c == C_USE:
            it.enabled = cell.checkState() == QtCore.Qt.Checked
        elif c == C_SHOT:
            self._planner.evaluate(it, shot_code=cell.text())
        elif c == C_SEQ:
            self._planner.evaluate(it, sequence_code=cell.text())
        else:
            return
        self._planner.evaluate_all(self._items)
        self._refresh()

    # -- running --------------------------------------------------------------
    def start(self):
        if self._running:
            return
        todo = ingest_plan.Planner.runnable([i for i in self._items if i.enabled])
        if not todo:
            self._say("nothing ready to ingest (fix the red rows)")
            return
        self._running = True
        self._go.setEnabled(False)
        try:
            sequences, failed = ingest_timeline.build_sequences(todo, self._say)
            for it, why in failed:
                it.own_errors.append("timeline: %s" % why)
            if not sequences or not ingest.run_ingest(self._app, sequences):
                self._finish()
                return
        except Exception as exc:
            self._say("ingest failed to start: %s" % exc)
            self._finish()
            return
        self._started = set(i.shot_code for i in todo)
        self._items = [i for i in self._items if i not in todo] + todo
        import time
        self._poll_started = time.time()
        self._timer.start()
        self._say("ingest started for %d plate(s)" % len(todo))

    def _poll_session(self):
        import time
        session = getattr(self._app, "ingest_session", None)
        results = dict(session.results) if session else {}
        for r, it in enumerate(self._items):
            if it.shot_code in results and it.shot_code in self._started:
                status, detail = results[it.shot_code]
                text = status + (": " + detail if detail else "")
                self._building = True
                self._table.item(r, C_NOTES).setText(text)
                self._building = False
        done = session is None or session.finalized
        if done or time.time() - self._poll_started > POLL_TIMEOUT_S:
            self._finish()

    def _finish(self):
        self._timer.stop()
        self._running = False
        self._go.setEnabled(True)
        self._say("done")

    def _say(self, text):
        self._log.appendPlainText(str(text))
