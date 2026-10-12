"""endura-gui: toggle power tweaks, watch power draw and per-app cost."""
import json
import socket
import subprocess
import sys

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from .i18n import T, tr

SOCK = "/run/endura/endura.sock"
GROUPS = {"cpu": T("CPU", "CPU"), "firmware": T("Firmware / clock", "固件 / 时钟"),
          "display": T("Display", "显示"), "audio": T("Audio", "音频"), "devices": T("Devices", "设备"),
          "system": T("System", "系统"), "apps": T("Applications", "应用")}
S = {
    "title": T("Endura · battery tuning", "Endura · 续航调优"),
    "no_daemon": T("Daemon not running", "守护进程未运行"),
    "budget": T("{h} h budget {w:.1f} W", "{h}h 预算 {w:.1f}W"),
    "legend": T("blue: system (battery)   grey: SoC", "蓝: 整机(电池)  灰蓝: SoC"),
    "waiting": T("waiting for data…", "等待数据…"),
    "on_bat": T("{w:.1f} W   ·   ~{h:.1f} h left", "{w:.1f} W   ·   剩余约 {h:.1f} h"),
    "on_bat_sub": T("10-min average {avg:.1f} W → {full:.1f} h on a full charge ({tgt} h needs ≤ {budget:.1f} W). "
                    "SoC {sock} · CPU {cpu:.0f}% · battery {pct}%",
                    "最近 10 分钟平均 {avg:.1f} W → 满电可用 {full:.1f} h（{tgt}h 需 ≤ {budget:.1f} W）。"
                    "SoC {sock} · CPU {cpu:.0f}% · 电量 {pct}%"),
    "on_ac": T("On AC · battery {pct}%", "接通电源 · 电量 {pct}%"),
    "opts": T("Battery options", "电池模式选项"), "boost": T("Turbo", "睿频"), "abm": T("ABM level", "ABM 级别"),
    "revert": T("Revert everything", "全部还原"),
    "revert_q": T("Turn every tweak off and restore the original system settings?", "关闭全部调优并还原系统原始设置？"),
    "tab_over": T("Overview", "总览"), "tab_apps": T("Apps", "应用耗电"), "tab_smooth": T("Smoothness", "流畅度"),
    "window": T("Window", "时间窗口"), "wins": [T("1 hour", "1 小时"), T("8 hours", "8 小时"), T("24 hours", "24 小时"), T("7 days", "7 天")],
    "cols": [T("App / service", "应用 / 服务"), T("Avg CPU (% of a core)", "平均 CPU（单核%）"),
             T("Wakeups/s", "唤醒 次/秒"), T("Est. W", "估算功耗 W")],
    "apps_note": T("{n} battery samples, average system {bat:.1f} W, SoC {sock}. Est. W = (SoC − 2 W idle floor) "
                   "× the app's share of CPU time: for comparison only.",
                   "电池样本 {n} 个，平均整机 {bat:.1f} W、SoC {sock}。估算功耗 = (SoC − 2W 空闲底) × 该应用 CPU 时间占比，仅作相对比较。"),
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
    TARGET_H = 10

    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr(S["title"]))
        self.resize(860, 920)
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        ov = QWidget(); v = QVBoxLayout(ov)
        self.big = QLabel("—"); self.big.setStyleSheet("font-size: 22pt; font-weight: 600")
        self.sub = QLabel(""); self.sub.setWordWrap(True)
        v.addWidget(self.big); v.addWidget(self.sub)
        self.graph = Graph(); v.addWidget(self.graph)
        opt = QGroupBox(tr(S["opts"])); og = QHBoxLayout(opt)
        og.addWidget(QLabel("EPP"))
        self.epp = QComboBox(); self.epp.addItems(["power", "balance_power", "balance_performance", "performance"])
        self.epp.activated.connect(lambda _: self.set_opt("battery_epp", self.epp.currentText()))
        og.addWidget(self.epp)
        self.boost = QCheckBox(tr(S["boost"])); self.boost.clicked.connect(lambda on: self.set_opt("battery_boost", on))
        og.addWidget(self.boost)
        og.addWidget(QLabel(tr(S["abm"])))
        self.abm = QSpinBox(); self.abm.setRange(1, 4)
        self.abm.valueChanged.connect(lambda val: self.set_opt("abm_level", val))
        og.addWidget(self.abm); og.addStretch()
        v.addWidget(opt)
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

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(5000)
        self.refresh()

    def set_opt(self, k, val):
        try:
            self.apply_status(call(op="option", key=k, value=val))
        except Exception as e:
            QMessageBox.warning(self, "Endura", str(e))

    def apply_status(self, st):
        o = st["options"]
        self.epp.setCurrentText(o["battery_epp"])
        self.boost.setChecked(bool(o["battery_boost"]))
        self.abm.blockSignals(True); self.abm.setValue(int(o["abm_level"])); self.abm.blockSignals(False)
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
        budget = full / self.TARGET_H if full else None
        bat = [h["bat_w"] for h in hist[-60:] if h["on_bat"] and h["bat_w"]]
        avg = sum(bat) / len(bat) if bat else None
        sock = f"{now['sock_w']:.1f} W" if now.get("sock_w") is not None else "n/a"
        if now.get("on_battery") and avg:
            self.big.setText(tr(S["on_bat"], w=now.get("bat_w") or 0, h=b.get("energy_wh", 0) / avg))
            self.sub.setText(tr(S["on_bat_sub"], avg=avg, full=full / avg, tgt=self.TARGET_H, budget=budget,
                                sock=sock, cpu=now.get("cpu_pct", 0), pct=b.get("pct")))
        else:
            self.big.setText(tr(S["on_ac"], pct=b.get("pct", "—")))
            self.sub.setText(f"SoC {sock} · CPU {now.get('cpu_pct', 0):.0f}%")
        self.graph.set_data(hist, budget, self.TARGET_H)

    def load_apps(self):
        secs = [3600, 8 * 3600, 86400, 7 * 86400][self.win_sel.currentIndex()]
        try:
            r = call(op="apps", seconds=secs, battery_only=True)
            if not r.get("samples"):
                r = call(op="apps", seconds=secs, battery_only=False)
                self.app_note.setText(tr(S["apps_ac"]))
            else:
                sock = f"{r['avg_sock_w']:.1f} W" if r.get("avg_sock_w") else "n/a"
                self.app_note.setText(tr(S["apps_note"], n=r["samples"], bat=r.get("avg_bat_w") or 0, sock=sock))
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
    w = Main(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
