"""Standalone Windows GUI for phone-harness observability and human teaching."""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageTk

from phone_harness.operator_broker import (
    DEFAULT_PENDING_FILE,
    OperatorQuestionBroker,
    load_pending_question,
)
from phone_harness.runtime import STATUS_FILE
from phone_harness.trace import DEFAULT_TRACE_FILE


BG = "#101318"
SURFACE = "#181d24"
SURFACE_2 = "#202630"
TEXT = "#e9edf3"
MUTED = "#8f9aa8"
ACCENT = "#74b7ff"
GOOD = "#67d391"
WARN = "#f1c56b"
BAD = "#f47c7c"
BORDER = "#303845"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _event_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%H:%M:%S.%f")[:-3]
    except ValueError:
        return "--:--:--"


def _event_duration(event):
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    value = data.get("duration_ms")
    if isinstance(value, (int, float)):
        return f"{value:.0f} ms"
    return ""


class TraceTail:
    """Incrementally read appended JSONL events and tolerate file rotation."""

    def __init__(self, path=DEFAULT_TRACE_FILE):
        self.path = Path(path)
        self.offset = 0
        self.identity = None

    def poll(self):
        if not self.path.exists():
            return []
        try:
            stat = self.path.stat()
        except OSError:
            return []
        identity = (stat.st_dev, stat.st_ino)
        if self.identity != identity or stat.st_size < self.offset:
            self.identity = identity
            self.offset = 0
        events = []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(self.offset)
                for line in stream:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(value, dict):
                        events.append(value)
                self.offset = stream.tell()
        except OSError:
            return []
        return events


class PhoneHarnessMonitor(tk.Tk):
    def __init__(self, *, trace_path=DEFAULT_TRACE_FILE, status_path=STATUS_FILE):
        super().__init__()
        self.title("Phone Harness Monitor")
        self.geometry("1540x930")
        self.minsize(1180, 720)
        self.configure(bg=BG)
        self.trace_tail = TraceTail(trace_path)
        self.status_path = Path(status_path)
        self.events = {}
        self.last_preview_path = None
        self.preview_photo = None
        self.current_question_id = None
        self.choice_buttons = []
        self._configure_styles()
        self._build_ui()
        self.after(100, self._poll)

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Monitor.TFrame", background=BG)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("Surface2.TFrame", background=SURFACE_2)
        style.configure("Monitor.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 16))
        style.configure("PanelTitle.TLabel", background=SURFACE, foreground=TEXT, font=("Segoe UI Semibold", 11))
        style.configure("Metric.TLabel", background=SURFACE_2, foreground=ACCENT, font=("Consolas", 11, "bold"))
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                        bordercolor=BORDER, rowheight=29, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background=SURFACE_2, foreground=MUTED,
                        bordercolor=BORDER, font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#294765")], foreground=[("selected", "#ffffff")])
        style.configure("Monitor.TButton", background=SURFACE_2, foreground=TEXT, padding=(10, 6))
        style.map("Monitor.TButton", background=[("active", "#34404d")])
        style.configure("Accent.TButton", background="#286aa6", foreground="#ffffff", padding=(12, 7))
        style.map("Accent.TButton", background=[("active", "#347dbb")])

    def _build_ui(self):
        header = ttk.Frame(self, style="Monitor.TFrame", padding=(18, 14, 18, 10))
        header.pack(fill="x")
        ttk.Label(header, text="PHONE HARNESS", style="Title.TLabel").pack(side="left")
        self.connection_label = ttk.Label(header, text="● UNKNOWN", style="Monitor.TLabel")
        self.connection_label.pack(side="left", padx=(24, 0))
        self.operation_label = ttk.Label(header, text="idle", style="Muted.TLabel")
        self.operation_label.pack(side="left", padx=(18, 0))
        self.runtime_label = ttk.Label(header, text="", style="Muted.TLabel")
        self.runtime_label.pack(side="right")

        body = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=5, bd=0)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        left = ttk.Frame(body, style="Surface.TFrame", padding=10)
        center = ttk.Frame(body, style="Monitor.TFrame")
        right = ttk.Frame(body, style="Surface.TFrame", padding=10)
        body.add(left, minsize=390, width=430)
        body.add(center, minsize=440, width=620)
        body.add(right, minsize=360, width=440)

        ttk.Label(left, text="Timeline", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 8))
        columns = ("time", "type", "summary", "duration")
        self.timeline = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        for key, title, width in (
            ("time", "Time", 78), ("type", "Stage", 118), ("summary", "Summary", 230), ("duration", "", 65)
        ):
            self.timeline.heading(key, text=title)
            self.timeline.column(key, width=width, minwidth=55, stretch=key == "summary")
        timeline_scroll = ttk.Scrollbar(left, orient="vertical", command=self.timeline.yview)
        self.timeline.configure(yscrollcommand=timeline_scroll.set)
        self.timeline.pack(side="left", fill="both", expand=True)
        timeline_scroll.pack(side="right", fill="y")
        self.timeline.bind("<<TreeviewSelect>>", self._on_timeline_select)

        preview_panel = ttk.Frame(center, style="Surface.TFrame", padding=10)
        preview_panel.pack(fill="both", expand=True)
        preview_header = ttk.Frame(preview_panel, style="Surface.TFrame")
        preview_header.pack(fill="x")
        ttk.Label(preview_header, text="Current Frame / Capture", style="PanelTitle.TLabel").pack(side="left")
        self.preview_meta = ttk.Label(preview_header, text="no frame", style="Muted.TLabel")
        self.preview_meta.pack(side="right")
        self.preview_canvas = tk.Canvas(preview_panel, bg="#080a0d", highlightthickness=1,
                                        highlightbackground=BORDER)
        self.preview_canvas.pack(fill="both", expand=True, pady=(8, 0))
        self.preview_canvas.bind("<Configure>", lambda _event: self._refresh_preview())

        detail_panel = ttk.Frame(center, style="Surface.TFrame", padding=10)
        detail_panel.pack(fill="both", expand=False, pady=(10, 0))
        ttk.Label(detail_panel, text="Analysis / Event Detail", style="PanelTitle.TLabel").pack(anchor="w")
        self.detail_text = tk.Text(detail_panel, height=13, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                                   relief="flat", font=("Consolas", 9), wrap="word")
        self.detail_text.pack(fill="both", expand=True, pady=(6, 0))
        self.detail_text.configure(state="disabled")

        decision = ttk.Frame(right, style="Surface.TFrame")
        decision.pack(fill="both", expand=True)
        ttk.Label(decision, text="Decision", style="PanelTitle.TLabel").pack(anchor="w")
        self.decision_text = tk.Text(decision, height=18, bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                                     relief="flat", font=("Segoe UI", 10), wrap="word")
        self.decision_text.pack(fill="both", expand=True, pady=(6, 12))
        self.decision_text.configure(state="disabled")

        question = ttk.Frame(right, style="Surface2.TFrame", padding=12)
        question.pack(fill="x", side="bottom")
        ttk.Label(question, text="Human Teaching", style="Metric.TLabel").pack(anchor="w")
        self.question_label = tk.Label(question, text="No pending question", bg=SURFACE_2, fg=MUTED,
                                       justify="left", anchor="w", wraplength=390,
                                       font=("Segoe UI", 10))
        self.question_label.pack(fill="x", pady=(8, 5))
        self.question_context = tk.Label(question, text="", bg=SURFACE_2, fg=MUTED,
                                         justify="left", anchor="w", wraplength=390,
                                         font=("Segoe UI", 9))
        self.question_context.pack(fill="x")
        self.choice_frame = ttk.Frame(question, style="Surface2.TFrame")
        self.choice_frame.pack(fill="x", pady=(8, 4))
        self.answer_entry = tk.Text(question, height=4, bg="#11161c", fg=TEXT, insertbackground=TEXT,
                                    relief="flat", font=("Segoe UI", 10), wrap="word")
        self.answer_entry.pack(fill="x", pady=(5, 8))
        self.send_button = ttk.Button(question, text="Send answer", style="Accent.TButton",
                                      command=self._submit_answer, state="disabled")
        self.send_button.pack(anchor="e")

    def _poll(self):
        self._poll_status()
        for event in self.trace_tail.poll():
            self._append_event(event)
        self._poll_question()
        self.after(250, self._poll)

    def _poll_status(self):
        state = _read_json(self.status_path)
        if not isinstance(state, dict):
            self.connection_label.configure(text="● OFFLINE", foreground=BAD)
            self.operation_label.configure(text="runtime status unavailable")
            return
        health = state.get("last_health") or {}
        connection = health.get("connection_state") or state.get("connection_state") or "unknown"
        color = GOOD if connection == "ready" else WARN if connection not in {"unknown", None} else MUTED
        self.connection_label.configure(text=f"● {str(connection).upper()}", foreground=color)
        operation = state.get("current_operation")
        if isinstance(operation, dict):
            kind = operation.get("kind", "operation")
            phase = operation.get("phase")
            self.operation_label.configure(text=f"{kind} · {phase or 'running'}")
        else:
            self.operation_label.configure(text="idle")
        runtime_id = state.get("runtime_id") or ""
        pid = state.get("pid")
        self.runtime_label.configure(text=f"runtime {str(runtime_id)[:8]}  pid {pid or '-'}")

    def _append_event(self, event):
        event_id = str(event.get("event_id") or len(self.events))
        self.events[event_id] = event
        values = (
            _event_time(event.get("at")),
            event.get("type", "event"),
            event.get("summary", ""),
            _event_duration(event),
        )
        self.timeline.insert("", "end", iid=event_id, values=values)
        children = self.timeline.get_children()
        if len(children) > 700:
            for old in children[:100]:
                self.events.pop(old, None)
                self.timeline.delete(old)
        self.timeline.see(event_id)
        preview = event.get("preview_path")
        if isinstance(preview, str) and Path(preview).exists():
            self.last_preview_path = Path(preview)
            self.preview_meta.configure(text=_event_duration(event) or Path(preview).name)
            self._refresh_preview()
        if event.get("type") in {"host.decision", "workflow.decision"}:
            self._set_text(self.decision_text, self._format_decision(event))
        if event.get("type") in {"operation.error", "operator.question"}:
            self.timeline.selection_set(event_id)
            self.timeline.see(event_id)
            self._show_event(event)

    def _format_decision(self, event):
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        lines = [str(event.get("summary") or "Decision")]
        if "confidence" in data:
            lines.append(f"\nConfidence: {data['confidence']}")
        for key in (
            "goal", "reason", "uncertainty", "expected_effect", "decision_kind",
            "producer_id", "producer_cell", "emissions", "merge_count", "free_cells",
        ):
            if data.get(key) is not None:
                lines.append(f"{key.replace('_', ' ').title()}: {data[key]}")
        if data.get("evidence"):
            lines.append("\nEvidence:")
            lines.extend(f"  • {item}" for item in data["evidence"])
        if data.get("alternatives"):
            lines.append("\nAlternatives:")
            lines.extend(f"  • {item}" for item in data["alternatives"])
        return "\n".join(lines)

    def _on_timeline_select(self, _event=None):
        selected = self.timeline.selection()
        if not selected:
            return
        event = self.events.get(selected[0])
        if event is not None:
            self._show_event(event)

    def _show_event(self, event):
        self._set_text(self.detail_text, json.dumps(event, ensure_ascii=False, indent=2))
        preview = event.get("preview_path")
        if isinstance(preview, str) and Path(preview).exists():
            self.last_preview_path = Path(preview)
            self._refresh_preview()

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _refresh_preview(self):
        path = self.last_preview_path
        if path is None or not Path(path).exists():
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(
                max(10, self.preview_canvas.winfo_width() // 2),
                max(10, self.preview_canvas.winfo_height() // 2),
                text="No captured frame yet",
                fill=MUTED,
                font=("Segoe UI", 11),
            )
            return
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                width = max(40, self.preview_canvas.winfo_width() - 16)
                height = max(40, self.preview_canvas.winfo_height() - 16)
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
                self.preview_photo = ImageTk.PhotoImage(image)
        except (OSError, ValueError):
            return
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(
            self.preview_canvas.winfo_width() // 2,
            self.preview_canvas.winfo_height() // 2,
            image=self.preview_photo,
            anchor="center",
        )

    def _poll_question(self):
        pending = load_pending_question(DEFAULT_PENDING_FILE)
        question_id = None if pending is None else pending.get("question_id")
        if question_id == self.current_question_id:
            return
        self.current_question_id = question_id
        for button in self.choice_buttons:
            button.destroy()
        self.choice_buttons = []
        self.answer_entry.delete("1.0", "end")
        if pending is None:
            self.question_label.configure(text="No pending question", fg=MUTED)
            self.question_context.configure(text="")
            self.send_button.configure(state="disabled")
            return
        self.question_label.configure(text=pending.get("question") or "Question", fg=TEXT)
        context_parts = []
        if pending.get("context"):
            context_parts.append(str(pending["context"]))
        if pending.get("confidence") is not None:
            context_parts.append(f"AI confidence: {pending['confidence']}")
        if pending.get("impact"):
            context_parts.append(f"Impact: {pending['impact']}")
        self.question_context.configure(text="  ·  ".join(context_parts))
        preview = pending.get("preview_path")
        if isinstance(preview, str) and Path(preview).exists():
            self.last_preview_path = Path(preview)
            self._refresh_preview()
        for choice in pending.get("choices") or []:
            button = ttk.Button(
                self.choice_frame,
                text=str(choice),
                style="Monitor.TButton",
                command=lambda value=str(choice): self._choose_answer(value),
            )
            button.pack(side="left", padx=(0, 6), pady=2)
            self.choice_buttons.append(button)
        self.send_button.configure(state="normal")
        self.bell()

    def _choose_answer(self, value):
        self.answer_entry.delete("1.0", "end")
        self.answer_entry.insert("1.0", value)
        self._submit_answer(choice=value)

    def _submit_answer(self, choice=None):
        question_id = self.current_question_id
        if not question_id:
            return
        answer = self.answer_entry.get("1.0", "end").strip()
        if not answer and choice is None:
            return
        OperatorQuestionBroker.submit_response(
            question_id,
            answer or str(choice),
            choice=choice,
        )
        self.send_button.configure(state="disabled")
        self.question_label.configure(text="Answer sent. Waiting for workflow to resume…", fg=GOOD)


def main():
    if sys.platform != "win32":
        raise SystemExit("phone-harness-monitor is currently a Windows GUI")
    app = PhoneHarnessMonitor()
    app.mainloop()


if __name__ == "__main__":
    main()

