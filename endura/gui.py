"""endura-gui: toggle power tweaks, watch power draw and per-app cost."""
import json
import socket
import subprocess
import sys

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget)

from .i18n import T, tr, set_lang

SOCK = "/run/endura/endura.sock"
GROUPS = {"cpu": T("CPU", "CPU"), "firmware": T("Firmware / clock", "固件 / 时钟"),
          "display": T("Display", "显示"), "thermal": T("Heat & fan", "发热与风扇"), "audio": T("Audio", "音频"), "devices": T("Devices", "设备"),
          "system": T("System", "系统"), "apps": T("Applications", "应用")}
S = {
    "title": T("Endura · battery tuning", "Endura · 续航调优"),
    "no_daemon": T("Daemon not running", "守护进程未运行"),
    "budget": T("{h:.3g} h budget {w:.1f} W", "{h:.3g}h 预算 {w:.1f}W"),
    "legend": T("blue: system (battery)   grey: SoC", "蓝: 整机(电池)  灰蓝: SoC"),
    "waiting": T("waiting for data…", "等待数据…"),
    "on_bat": T("{w:.1f} W   ·   ~{h:.1f} h left", "{w:.1f} W   ·   剩余约 {h:.1f} h"),
    "on_bat_sub": T("10-min average {avg:.1f} W → {full:.1f} h on a full charge ({tgt:.3g} h needs ≤ {budget:.1f} W). "
                    "SoC {sock} · CPU {cpu:.0f}% · battery {pct}%",
                    "最近 10 分钟平均 {avg:.1f} W → 满电可用 {full:.1f} h（{tgt:.3g}h 需 ≤ {budget:.1f} W）。"
                    "SoC {sock} · CPU {cpu:.0f}% · 电量 {pct}%"),
    "on_ac": T("On AC · battery {pct}%", "接通电源 · 电量 {pct}%"),
    "opts": T("Battery options", "电池模式选项"), "boost": T("Turbo", "睿频"), "abm": T("ABM level", "ABM 级别"),
    "revert": T("Revert everything", "全部还原"),
    "revert_q": T("Turn every tweak off and restore the original system settings?", "关闭全部调优并还原系统原始设置？"),
    "tab_over": T("Overview", "总览"), "tab_set": T("Settings", "设置"),
    "g_budget": T("Battery-life target", "续航目标"),
    "budget_w": T("Power budget", "功耗预算"), "auto": T("auto (full charge ÷ 10 h)", "自动（满电 ÷ 10h）"),
    "target_h": T("= battery life on a full charge", "= 满电续航"),
    "budget_help": T("Drawn as the dashed line on the Overview graph. Battery capacity: {wh:.1f} Wh.",
                     "显示为总览曲线上的虚线。电池满电容量：{wh:.1f} Wh。"),
    "g_alert": T("Runaway app alerts", "高耗电应用提醒"),
    "alert_w": T("Alert when an app's estimated power exceeds", "应用估算功耗超过"),
    "alert_k": T("or its wakeups per second exceed", "或每秒唤醒次数超过"),
    "alert_m": T("for at least", "并持续"), "minutes": T(" min", " 分钟"), "off": T("off", "关闭"),
    "alert_ac": T("Also alert while on AC power", "插电时也提醒"),
    "ignore": T("Never alert for these apps", "不提醒的应用"),
    "ign_add": T("Add", "添加"), "ign_del": T("Remove selected", "移除选中"),
    "ign_ph": T("app name as shown in the Apps tab", "应用名，与“应用耗电”页一致"),
    "alert_help": T("Estimated power = power above the idle floor × the app's share of CPU time, smoothed over "
                    "~1 min. One notification per app per hour at most; the notification has a button to mute the app.",
                    "估算功耗 = 高于空闲底的功耗 × 该应用 CPU 时间占比，约 1 分钟平滑。同一应用每小时最多提醒一次，通知上有按钮可直接静音该应用。"),
    "g_ui": T("Interface", "界面"), "lang": T("Language", "语言"),
    "langs": [("auto", T("Follow system", "跟随系统")), ("en", "English"), ("zh", "中文")], "tab_apps": T("Apps", "应用耗电"), "tab_smooth": T("Smoothness", "流畅度"),
    "window": T("Window", "时间窗口"), "wins": [T("1 hour", "1 小时"), T("8 hours", "8 小时"), T("24 hours", "24 小时"), T("7 days", "7 天")],
    "cols": [T("App / service", "应用 / 服务"), T("Avg CPU (% of a core)", "平均 CPU（单核%）"),
             T("Wakeups/s", "唤醒 次/秒"), T("Est. W", "估算功耗 W")],
    "apps_note": T("{n} battery samples, average system {bat:.1f} W, SoC {sock}. Est. W = (SoC − {floor:.1f} W idle floor) "
                   "× the app's share of CPU time: for comparison only.",
                   "电池样本 {n} 个，平均整机 {bat:.1f} W、SoC {sock}。估算功耗 = (SoC − {floor:.1f}W 空闲底) × 该应用 CPU 时间占比，仅作相对比较。"),
    "apps_ac": T("(no battery samples in this window; showing AC time too)", "（此窗口内没有电池样本，显示含插电时段）"),
    "probe": T("Measure the selected app's real power (freeze/thaw A/B, ~2.5 min)", "实测选中应用的真实功耗（冻结/解冻 A/B，约 2.5 分钟）"),
    "probe_help": T("Freezes the app's cgroup for 20 s and thaws it for 20 s a few times and compares battery power. "
                    "Its windows do not respond while frozen. Apps with many wakeups often cost more than their CPU% suggests.",
                    "反复冻结该应用的 cgroup 20 秒、解冻 20 秒，比较整机电池功耗。冻结期间该应用窗口无响应。唤醒次数高的应用往往比 CPU% 显示的更耗电。"),
    "select": T("Select an app in the table first", "先在表格中选中一个应用"),
    "probing": T("Measuring {n} ({k}/{t})", "正在实测 {n}（{k}/{t}）"),
    "round": T("round {i}: running {a:.2f} W / frozen {b:.2f} W", "第{i}轮: 运行 {a:.2f} W / 冻结 {b:.2f} W"),
    "probe_res": T("{n} really costs ≈ {w:.2f} W", "{n} 实际耗电 ≈ {w:.2f} W"),
    "probe_fail": T("Measurement failed: {e}", "实测失败: {e}"),
    "smooth": T("<b>Smoothness criterion</b>: from idle, a UI-like task that takes 10 ms at full performance must finish "
                "with p90 ≤ 16.7 ms (one 60 Hz frame); a 2 ms task p90 ≤ 8.3 ms (one 120 Hz frame).",
                "<b>流畅度标准</b>：从空闲开始执行一段在最高性能下耗时 10ms 的 UI 类任务，p90 完成时间必须 ≤ 16.7ms（60Hz 一帧）；"
                "2ms 任务 p90 ≤ 8.3ms（120Hz 一帧）。"),
    "bench": T("Run smoothness test (~30 s)", "运行流畅度测试（约 30 秒）"), "running": T("running…", "运行中…"),
    "tags": {"error": T("error", "错误"), "na": T("not available", "不可用"), "reboot_pending": T("after reboot", "重启后生效"),
             "active": T("active", "生效中"), "bat": T("battery only", "仅电池"), "inactive": T("not active", "未生效"),
             "needs_reboot": T("needs reboot", "需重启")},
}


def call(**req):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(120)
    s.connect(SOCK)
    f = s.makefile("rwb")
    f.write((json.dumps(req) + "\n").encode())
    f.flush()
    resp = json.loads(f.readline())
    s.close()
    if isinstance(resp, dict) and "error" in resp and len(resp) == 1:
        raise RuntimeError(resp["error"])
    return resp


def ro_item(text, right=False):
    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)   # never editable
    if right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


class Graph(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(170)
        self.pts, self.budget, self.target = [], None, 10

    def set_data(self, hist, budget, target):
        self.pts = [(h["ts"], h["bat_w"], h["sock_w"]) for h in hist if h["bat_w"] is not None]
        self.budget, self.target = budget, target
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(40, 10, -10, -22)
        pal = self.palette()
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawRect(r)
        if len(self.pts) < 2:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, tr(S["waiting"]))
            return
        t0, t1 = self.pts[0][0], self.pts[-1][0]
        top = max(20.0, max(x[1] for x in self.pts) * 1.1)
        X = lambda t: r.left() + (t - t0) / max(1, t1 - t0) * r.width()
        Y = lambda w: r.bottom() - (w / top) * r.height()
        p.setFont(QFont(p.font().family(), 8))
        for w in range(0, int(top) + 1, 5):
            p.setPen(QPen(pal.mid().color(), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(r.left(), Y(w)), QPointF(r.right(), Y(w)))
            p.setPen(pal.text().color())
            p.drawText(QRectF(0, Y(w) - 8, 36, 16), Qt.AlignmentFlag.AlignRight, f"{w}W")
        if self.budget:
            p.setPen(QPen(QColor("#d97706"), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(r.left(), Y(self.budget)), QPointF(r.right(), Y(self.budget)))
            p.drawText(QPointF(r.left() + 4, Y(self.budget) - 4), tr(S["budget"], h=self.target, w=self.budget))
        for idx, color in ((2, "#7c9cbf"), (1, "#2563eb")):
            path, first = QPainterPath(), True
            for pt in self.pts:
                if pt[idx] is None:
                    continue
                q = QPointF(X(pt[0]), Y(pt[idx]))
                path.moveTo(q) if first else path.lineTo(q)
                first = False
            p.setPen(QPen(QColor(color), 2 if idx == 1 else 1))
            p.drawPath(path)
        p.setPen(pal.text().color())
        p.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 16), Qt.AlignmentFlag.AlignLeft, f"-{(t1 - t0) / 60:.0f} min")
        p.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 16), Qt.AlignmentFlag.AlignRight, tr(S["legend"]))


class TweakRow(QFrame):
    def __init__(self, win, t):
        super().__init__()
        self.win, self.id = win, t["id"]
        lay = QGridLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.cb = QCheckBox()
        f = self.cb.font(); f.setBold(True); self.cb.setFont(f)
        self.cb.clicked.connect(self.toggle)
        self.badge, self.desc, self.detail = QLabel(), QLabel(), QLabel()
        self.desc.setWordWrap(True)
        self.detail.setStyleSheet("color: gray; font-size: 9pt")
        lay.addWidget(self.cb, 0, 0)
        lay.addWidget(self.badge, 0, 1, Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.desc, 1, 0, 1, 2)
        lay.addWidget(self.detail, 2, 0, 1, 2)
        self.update_from(t)

    def update_from(self, t):
        fmt = t.get("fmt") or {}
        self.cb.blockSignals(True)
        self.cb.setText(tr(t["title"], **fmt))
        self.cb.setChecked(t["enabled"])
        self.cb.setEnabled(t["available"])
        self.cb.blockSignals(False)
        risk = tr(t["risk"]) if t.get("risk") else ""
        self.desc.setText(tr(t["desc"], **fmt) + (f"\n⚠ {risk}" if risk else ""))
        g, tags = S["tags"], []
        if t.get("error"):
            tags.append((g["error"], "#dc2626"))
        elif not t["available"]:
            tags.append((g["na"], "gray"))
        elif t.get("pending_reboot"):
            tags.append((g["reboot_pending"], "#d97706"))
        elif t["enabled"] and t.get("active"):
            tags.append((g["active"], "#16a34a"))
        elif t["enabled"] and t.get("battery_only"):
            tags.append((g["bat"], "#2563eb"))
        elif t["enabled"]:
            tags.append((g["inactive"], "#d97706"))
        if t.get("reboot"):
            tags.append((g["needs_reboot"], "gray"))
        self.badge.setText("  ".join(f"<span style='color:{c}'>●&nbsp;{tr(s)}</span>" for s, c in tags))
        self.detail.setText(t.get("error") or t.get("detail") or "")
        self.detail.setStyleSheet("color: #dc2626; font-size: 9pt" if t.get("alert") else "color: gray; font-size: 9pt")

    def toggle(self, on):
        try:
            self.win.apply_status(call(op="set", id=self.id, enabled=on))
        except Exception as e:
            QMessageBox.warning(self, "Endura", str(e))


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.opts, self.last_now = {}, {}
        self.setWindowTitle(tr(S["title"]))
        self.resize(860, 920)
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        ov = QWidget(); v = QVBoxLayout(ov)
        self.big = QLabel("—"); self.big.setStyleSheet("font-size: 22pt; font-weight: 600")
        self.sub = QLabel(""); self.sub.setWordWrap(True)
        v.addWidget(self.big); v.addWidget(self.sub)
        self.graph = Graph(); v.addWidget(self.graph)
        self.rows = {}
        self.tw_box = QVBoxLayout()
        area = QScrollArea(); area.setWidgetResizable(True)
        inner = QWidget(); inner.setLayout(self.tw_box); area.setWidget(inner)
        v.addWidget(area, 1)
        btns = QHBoxLayout()
        rb = QPushButton(tr(S["revert"])); rb.clicked.connect(self.revert_all)
        btns.addStretch(); btns.addWidget(rb)
        v.addLayout(btns)
        tabs.addTab(ov, tr(S["tab_over"]))

        ap = QWidget(); av = QVBoxLayout(ap)
        h = QHBoxLayout(); h.addWidget(QLabel(tr(S["window"])))
        self.win_sel = QComboBox(); self.win_sel.addItems([tr(x) for x in S["wins"]])
        self.win_sel.activated.connect(lambda _: self.load_apps())
        h.addWidget(self.win_sel); h.addStretch(); av.addLayout(h)
        self.app_note = QLabel(); self.app_note.setWordWrap(True); av.addWidget(self.app_note)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr(c) for c in S["cols"]])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)   # read-only
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        av.addWidget(self.table)
        ph = QHBoxLayout()
        self.probe_btn = QPushButton(tr(S["probe"])); self.probe_btn.clicked.connect(self.start_probe)
        ph.addWidget(self.probe_btn); ph.addStretch(); av.addLayout(ph)
        self.probe_lab = QLabel(tr(S["probe_help"])); self.probe_lab.setWordWrap(True)
        av.addWidget(self.probe_lab)
        self.probe_timer = QTimer(self); self.probe_timer.timeout.connect(self.poll_probe)
        tabs.addTab(ap, tr(S["tab_apps"]))
        tabs.currentChanged.connect(lambda i: i == 1 and self.load_apps())

        sm = QWidget(); sv = QVBoxLayout(sm)
        lab = QLabel(tr(S["smooth"])); lab.setWordWrap(True); sv.addWidget(lab)
        self.bench_btn = QPushButton(tr(S["bench"])); self.bench_btn.clicked.connect(self.run_bench)
        sv.addWidget(self.bench_btn)
        self.bench_out = QTextEdit(); self.bench_out.setReadOnly(True); sv.addWidget(self.bench_out)
        tabs.addTab(sm, tr(S["tab_smooth"]))
        tabs.addTab(self.build_settings(), tr(S["tab_set"]))

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(5000)
        self.refresh()

    def build_settings(self):
        page = QWidget(); v = QVBoxLayout(page)
        self.pending = {}
        self.debounce = QTimer(self); self.debounce.setSingleShot(True); self.debounce.setInterval(700)
        self.debounce.timeout.connect(self.flush_opts)

        g = QGroupBox(tr(S["g_budget"])); f = QFormLayout(g)
        row = QHBoxLayout()
        self.budget = QDoubleSpinBox(); self.budget.setRange(0, 200); self.budget.setDecimals(2)
        self.budget.setSingleStep(0.5); self.budget.setSuffix(" W"); self.budget.setSpecialValueText(tr(S["auto"]))
        self.budget.setKeyboardTracking(False)
        self.hours = QDoubleSpinBox(); self.hours.setRange(1, 99); self.hours.setDecimals(1)
        self.hours.setSuffix(" h"); self.hours.setKeyboardTracking(False)
        self.budget.valueChanged.connect(self.budget_changed)
        self.hours.valueChanged.connect(self.hours_changed)
        self.budget.setMinimumWidth(200); self.budget.setMaximumWidth(240)
        row.addWidget(self.budget); row.addWidget(QLabel(tr(S["target_h"]))); row.addWidget(self.hours); row.addStretch()
        f.addRow(tr(S["budget_w"]), row)
        self.budget_help = QLabel(); self.budget_help.setStyleSheet("color: gray"); f.addRow(self.budget_help)
        v.addWidget(g)

        g = QGroupBox(tr(S["opts"])); og = QHBoxLayout(g)
        og.addWidget(QLabel("EPP"))
        self.epp = QComboBox(); self.epp.addItems(["power", "balance_power", "balance_performance", "performance"])
        self.epp.activated.connect(lambda _: self.set_opt("battery_epp", self.epp.currentText()))
        og.addWidget(self.epp)
        self.boost = QCheckBox(tr(S["boost"])); self.boost.clicked.connect(lambda on: self.set_opt("battery_boost", on))
        og.addWidget(self.boost)
        og.addWidget(QLabel(tr(S["abm"])))
        self.abm = QSpinBox(); self.abm.setRange(1, 4); self.abm.setKeyboardTracking(False)
        self.abm.valueChanged.connect(lambda val: self.queue_opt("abm_level", val))
        og.addWidget(self.abm); og.addStretch()
        v.addWidget(g)

        g = QGroupBox(tr(S["g_alert"])); f = QFormLayout(g)
        self.al_w = QDoubleSpinBox(); self.al_w.setRange(0, 100); self.al_w.setDecimals(1); self.al_w.setSingleStep(0.5)
        self.al_w.setSuffix(" W"); self.al_w.setSpecialValueText(tr(S["off"])); self.al_w.setKeyboardTracking(False)
        self.al_w.valueChanged.connect(lambda val: self.queue_opt("alert_app_w", val))
        f.addRow(tr(S["alert_w"]), self.al_w)
        self.al_k = QSpinBox(); self.al_k.setRange(0, 100000); self.al_k.setSingleStep(250)
        self.al_k.setSpecialValueText(tr(S["off"])); self.al_k.setKeyboardTracking(False)
        self.al_k.valueChanged.connect(lambda val: self.queue_opt("alert_wakeups", val))
        f.addRow(tr(S["alert_k"]), self.al_k)
        self.al_m = QSpinBox(); self.al_m.setRange(1, 120); self.al_m.setSuffix(tr(S["minutes"]))
        self.al_m.setKeyboardTracking(False)
        self.al_m.valueChanged.connect(lambda val: self.queue_opt("alert_minutes", val))
        f.addRow(tr(S["alert_m"]), self.al_m)
        for w in (self.al_w, self.al_k, self.al_m):
            w.setMinimumWidth(140); w.setMaximumWidth(180)
        self.al_ac = QCheckBox(tr(S["alert_ac"])); self.al_ac.clicked.connect(lambda on: self.set_opt("alert_on_ac", on))
        f.addRow(self.al_ac)
        self.ign = QListWidget(); self.ign.setMaximumHeight(110)
        f.addRow(tr(S["ignore"]), self.ign)
        ir = QHBoxLayout()
        self.ign_edit = QLineEdit(); self.ign_edit.setPlaceholderText(tr(S["ign_ph"]))
        self.ign_edit.returnPressed.connect(self.ignore_add)
        b1 = QPushButton(tr(S["ign_add"])); b1.clicked.connect(self.ignore_add)
        b2 = QPushButton(tr(S["ign_del"])); b2.clicked.connect(self.ignore_del)
        ir.addWidget(self.ign_edit, 1); ir.addWidget(b1); ir.addWidget(b2)
        f.addRow(ir)
        h = QLabel(tr(S["alert_help"])); h.setWordWrap(True); h.setStyleSheet("color: gray"); f.addRow(h)
        v.addWidget(g)

        g = QGroupBox(tr(S["g_ui"])); f = QFormLayout(g)
        self.lang = QComboBox()
        for code, name in S["langs"]:
            self.lang.addItem(tr(name), code)
        self.lang.activated.connect(self.lang_changed)
        self.lang.setMaximumWidth(240)
        f.addRow(tr(S["lang"]), self.lang)
        v.addWidget(g)
        v.addStretch()
        area = QScrollArea(); area.setWidgetResizable(True); area.setWidget(page)
        return area

    def full_wh(self):
        return ((self.last_now or {}).get("battery") or {}).get("full_wh") or 0

    def budget_w(self):
        """Effective budget: the configured W, or full charge / 10 h."""
        b = float((self.opts or {}).get("budget_w") or 0)
        full = self.full_wh()
        return b if b > 0 else (full / 10 if full else None)

    def budget_changed(self, w):
        full = self.full_wh()
        if full:
            self.hours.blockSignals(True)
            self.hours.setValue(full / (w if w > 0 else full / 10))
            self.hours.blockSignals(False)
        self.queue_opt("budget_w", w)

    def hours_changed(self, h):
        full = self.full_wh()
        if full and h > 0:
            self.budget.setValue(round(full / h, 2))     # -> budget_changed

    def queue_opt(self, k, val):
        self.pending[k] = val
        self.debounce.start()

    def flush_opts(self):
        pending, self.pending = self.pending, {}
        for k, val in pending.items():
            self.set_opt(k, val)

    def ignore_add(self):
        name = self.ign_edit.text().strip()
        if not name:
            row = self.table.currentRow()
            name = self.table.item(row, 0).text() if row >= 0 and self.table.item(row, 0) else ""
        if name:
            self.ign_edit.clear()
            self.set_opt("alert_ignore", sorted(set(self.opts.get("alert_ignore") or []) | {name}))

    def ignore_del(self):
        gone = {i.text() for i in self.ign.selectedItems()}
        if gone:
            self.set_opt("alert_ignore", [x for x in self.opts.get("alert_ignore") or [] if x not in gone])

    def lang_changed(self, _):
        code = self.lang.currentData()
        self.set_opt("language", code)
        set_lang(code)
        global WINDOW            # rebuild every widget in the new language
        w = Main(); w.setGeometry(self.geometry()); w.show()
        WINDOW = w
        self.timer.stop(); self.close()

    def set_opt(self, k, val):
        try:
            self.apply_status(call(op="option", key=k, value=val))
        except Exception as e:
            QMessageBox.warning(self, "Endura", str(e))

    def apply_status(self, st):
        o = self.opts = st["options"]
        self.last_now = st.get("now") or self.last_now

        def put(w, val):              # never fight the user while they edit a field
            if w.hasFocus() or self.pending:
                return
            w.blockSignals(True); w.setValue(val); w.blockSignals(False)
        self.epp.setCurrentText(o["battery_epp"])
        self.boost.setChecked(bool(o["battery_boost"]))
        put(self.abm, int(o["abm_level"]))
        put(self.budget, float(o.get("budget_w") or 0))
        full, bw = self.full_wh(), self.budget_w()
        if full and bw:
            put(self.hours, full / bw)
            self.budget_help.setText(tr(S["budget_help"], wh=full))
        put(self.al_w, float(o.get("alert_app_w", 3.0)))
        put(self.al_k, int(o.get("alert_wakeups", 1500)))
        put(self.al_m, int(o.get("alert_minutes", 5)))
        self.al_ac.setChecked(bool(o.get("alert_on_ac")))
        ign = list(o.get("alert_ignore") or [])
        if [self.ign.item(i).text() for i in range(self.ign.count())] != ign:
            self.ign.clear(); self.ign.addItems(ign)
        i = self.lang.findData(o.get("language") or "auto")
        if i >= 0:
            self.lang.setCurrentIndex(i)
        last_group = None
        for t in sorted(st["tweaks"], key=lambda t: list(GROUPS).index(t["group"])):
            if t["id"] in self.rows:
                self.rows[t["id"]].update_from(t)
                continue
            if t["group"] != last_group:
                self.tw_box.addWidget(QLabel(f"<h3>{tr(GROUPS.get(t['group'], t['group']))}</h3>"))
            last_group = t["group"]
            self.rows[t["id"]] = TweakRow(self, t)
            self.tw_box.addWidget(self.rows[t["id"]])
        if not self.tw_box.itemAt(self.tw_box.count() - 1).spacerItem():
            self.tw_box.addStretch()

    def refresh(self):
        try:
            st = call(op="status")
            hist = call(op="history", seconds=3600)["history"]
        except Exception as e:
            self.big.setText(tr(S["no_daemon"]))
            self.sub.setText(f"{e}\nsudo systemctl enable --now endurad")
            return
        self.apply_status(st)
        now = st.get("now") or {}
        b = now.get("battery", {})
        full = b.get("full_wh") or 0
        budget = self.budget_w()
        tgt = full / budget if full and budget else 10
        bat = [h["bat_w"] for h in hist[-60:] if h["on_bat"] and h["bat_w"]]
        avg = sum(bat) / len(bat) if bat else None
        sock = f"{now['sock_w']:.1f} W" if now.get("sock_w") is not None else "n/a"
        if now.get("on_battery") and avg:
            self.big.setText(tr(S["on_bat"], w=now.get("bat_w") or 0, h=b.get("energy_wh", 0) / avg))
            self.sub.setText(tr(S["on_bat_sub"], avg=avg, full=full / avg, tgt=tgt, budget=budget or 0,
                                sock=sock, cpu=now.get("cpu_pct", 0), pct=b.get("pct")))
        else:
            self.big.setText(tr(S["on_ac"], pct=b.get("pct", "—")))
            self.sub.setText(f"SoC {sock} · CPU {now.get('cpu_pct', 0):.0f}%")
        self.graph.set_data(hist, budget, tgt)

    def load_apps(self):
        secs = [3600, 8 * 3600, 86400, 7 * 86400][self.win_sel.currentIndex()]
        try:
            r = call(op="apps", seconds=secs, battery_only=True)
            if not r.get("samples"):
                r = call(op="apps", seconds=secs, battery_only=False)
                self.app_note.setText(tr(S["apps_ac"]))
            else:
                sock = f"{r['avg_sock_w']:.1f} W" if r.get("avg_sock_w") else "n/a"
                self.app_note.setText(tr(S["apps_note"], n=r["samples"], bat=r.get("avg_bat_w") or 0, sock=sock,
                                         floor=r.get("floor_w") or 2.0))
        except Exception as e:
            self.app_note.setText(str(e)); return
        apps = r.get("apps", [])
        self.table.setRowCount(len(apps))
        for i, a in enumerate(apps):
            vals = (a["name"], f"{a['cpu_pct']:.1f}", f"{a.get('wakeups', 0):.0f}", f"{a['est_w']:.2f}")
            for j, val in enumerate(vals):
                self.table.setItem(i, j, ro_item(val, right=j > 0))

    def start_probe(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Endura", tr(S["select"])); return
        name = self.table.item(row, 0).text()
        try:
            call(op="probe_app", name=name, rounds=3, win=20)
        except Exception as e:
            QMessageBox.warning(self, "Endura", str(e)); return
        self.probe_btn.setEnabled(False)
        self.probe_lab.setText(tr(S["probing"], n=name, k=0, t=3))
        self.probe_timer.start(3000)

    def poll_probe(self):
        try:
            r = call(op="probe_result")
        except Exception:
            return
        rounds = r.get("rounds", [])
        txt = "<br>".join(tr(S["round"], i=i + 1, a=x["running_w"], b=x["frozen_w"]) for i, x in enumerate(rounds))
        if r.get("state") == "running":
            self.probe_lab.setText(tr(S["probing"], n=r["name"], k=len(rounds), t=r["total"]) + "<br>" + txt)
            return
        self.probe_timer.stop()
        self.probe_btn.setEnabled(True)
        if r.get("state") == "done":
            self.probe_lab.setText(f"<b>{tr(S['probe_res'], n=r['name'], w=r['saving_w'])}</b><br>{txt}")
        else:
            self.probe_lab.setText(tr(S["probe_fail"], e=r.get("error")))

    def run_bench(self):
        self.bench_btn.setEnabled(False)
        self.bench_out.setText(tr(S["running"]))
        QApplication.processEvents()
        try:
            out = subprocess.run(["/usr/libexec/endura/endura-bench"], capture_output=True, text=True, timeout=300).stdout
        except Exception as e:
            out = str(e)
        self.bench_out.setText(out)
        self.bench_btn.setEnabled(True)

    def revert_all(self):
        if QMessageBox.question(self, "Endura", tr(S["revert_q"])) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.apply_status(call(op="revert_all"))
        except Exception as e:
            QMessageBox.warning(self, "Endura", str(e))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Endura")
    app.setDesktopFileName("io.github.endura")
    try:
        set_lang(call(op="status")["options"].get("language"))
    except Exception:
        pass
    global WINDOW
    WINDOW = Main(); WINDOW.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
