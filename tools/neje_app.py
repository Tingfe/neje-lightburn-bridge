"""NEJE Bridge — a small interactive control panel for LightBurn over Bluetooth.

One window, no terminal, no flags to remember:

    [ 启动（完整功能） ]  [ 隔离试跑 ]  [ 停止 ]  [ 急停 ]
    有效时间 [ 7 ] 小时     状态: 桥接运行中 · 等待 LightBurn 连接

While it is running it *supervises* the bridge: when a session ends (LightBurn
disconnected, job finished) it starts a fresh bridge process for the next client, for
as long as the chosen validity window lasts. Each restart is a brand new process with a
brand new BLE transport and a clean preflight, so nothing about a previous session is
carried over -- and because the transport is created per process, this sidesteps the
"Existing transport active" problem that a single long-lived bridge cannot avoid.

Every session is audited independently as soon as it ends, and the verdict is shown in
the window. Nothing is ever fabricated or replayed.
"""
import datetime
import json
import queue
import socket
import subprocess
import sys
import threading
import time
import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import neje


def _ensure_tkinter():
    """Homebrew keeps python-tk's _tkinter in libexec, where a venv will not look.

    Rather than asking the operator to set PYTHONPATH, find the module and make it
    importable here. Applies only when tkinter is not already available.
    """
    try:
        import tkinter  # noqa: F401
        return
    except ImportError:
        pass
    import glob
    candidates = []
    for root in ('/opt/homebrew/Cellar/python-tk@*/*/libexec',
                 '/opt/homebrew/opt/python-tk@*/libexec',
                 '/usr/local/Cellar/python-tk@*/*/libexec'):
        candidates += glob.glob(root)
    for path in candidates:
        if glob.glob(os.path.join(path, '_tkinter*.so')):
            os.environ['PYTHONPATH'] = path + os.pathsep + os.environ.get('PYTHONPATH', '')
            if path not in sys.path:
                sys.path.insert(0, path)
            return


_ensure_tkinter()

ROOT = neje.ROOT
LOG_ROOT = Path.home() / 'Library' / 'Logs' / 'NEJE'
# Measured: the FIRST session after the app starts is healthy (17 s, 32 lines, audit
# passed), while sessions restarted 2 s later lose their client after about a second.
# The BLE link and the device need time to settle between sessions, so the restart is
# deliberately unhurried, and a session that dies almost immediately backs off further.
RESTART_DELAY_SECONDS = 1.0
FAST_DEATH_SECONDS = 5.0
BACKOFF_SECONDS = 20.0
SAFETY = ('护目镜（455nm）', '通风', '灭火设备在手边', '可随时切断设备主电源',
          '行程净空', '手离开机器')


def port_in_use(port=4002):
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(('127.0.0.1', port)) == 0


class Supervisor:
    """Starts the bridge, and starts it again after each session ends."""

    def __init__(self, log):
        self.log = log
        self.trace = LOG_ROOT / 'app.log'
        self.trace.parent.mkdir(parents=True, exist_ok=True)
        self.process = None
        self.test = False
        self.deadline = 0.0
        self.sessions = 0
        self.restart_at = None
        self.last_log = None
        self.device = None
        self.last_verdict = None
        self.busy = threading.Lock()

    def _trace(self, message):
        stamp = datetime.datetime.now().isoformat(timespec='seconds')
        with self.trace.open('a') as handle:
            handle.write(f'{stamp} {message}\n')

    # -- state -----------------------------------------------------------------
    def running(self):
        return self.process is not None and self.process.poll() is None

    def remaining(self):
        # Based on the deadline only: the process may not exist yet when _spawn asks.
        return max(0.0, self.deadline - time.time()) if self.deadline else 0.0

    def awaiting_client(self):
        return self.running() and not port_in_use()

    def client_connected(self):
        # The bridge binds 4002 for its whole life; a *client* is present when the
        # port is accepting and our own probe connects AND the log shows a connection.
        return self.running() and self._log_has('pipeline_tcp_connected', since=self._session_start)

    # -- lifecycle -------------------------------------------------------------
    def start(self, test, hours):
        with self.busy:
            if self.running():
                self.log('已经在运行')
                return
            self.test = test
            self.deadline = time.time() + hours * 3600
            self.sessions = 0
            self.log(f'启动桥接（{"隔离试跑" if test else "完整功能"}），有效时间 {hours:g} 小时')
            self._spawn()

    def _spawn(self):
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S')
        path = LOG_ROOT / f'neje-{stamp}.jsonl'
        # The bridge's wall limit is in SECONDS and must cover what is left of the
        # window (a unit slip here silently produced --seconds 1).
        command, _ = neje.build_command(seconds=int(max(60, self.remaining())),
                                       log=path, test=self.test, device=self.device)
        self.last_log = path
        self._session_start = time.time()
        self.sessions += 1
        self.log(f'第 {self.sessions} 个会话 → {path.name}')
        self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)

    def stop(self):
        with self.busy:
            self.deadline = 0.0
            self.restart_at = None
            if self.process and self.process.poll() is None:
                self.process.terminate()
                self.log('已停止桥接')
            self.process = None

    def tick(self):
        """Called from the UI loop: restart between sessions inside the window."""
        with self.busy:
            if self.restart_at is not None:
                if time.time() >= self.restart_at:
                    self.restart_at = None
                    if self.deadline > time.time():
                        self._spawn()
                    else:
                        self.deadline = 0.0
                        self.log('有效时间已用完，桥接不再重启')
                return
            if self.process is None:
                return
            if self.process.poll() is None:
                return
            self.process = None
            self.device = self.device or self._remember_address(self.last_log)
            self.last_verdict = self._audit(self.last_log)
            if self.deadline <= time.time():
                self.log('有效时间已用完，桥接不再重启')
                self.deadline = 0.0
                return
            lived = time.time() - self._session_start
            delay = RESTART_DELAY_SECONDS
            if lived < FAST_DEATH_SECONDS:
                delay = BACKOFF_SECONDS
                self.log(f'上一个会话只活了 {lived:.0f} 秒（异常），退避 {delay:g} 秒再试')
            self.log(f'会话结束，{delay:g} 秒后自动准备下一个连接')
            self.restart_at = time.time() + delay

    # -- helpers ---------------------------------------------------------------
    def _log_has(self, event, since=None):
        if not self.last_log or not self.last_log.exists():
            return False
        if since is None:
            since = 0
        try:
            for line in self.last_log.read_text(errors='replace').splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get('event') == event:
                    stamp = row.get('utc')
                    if stamp and datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() >= since:
                        return True
        except Exception:                                    # noqa: BLE001
            return False
        return False

    def _remember_address(self, path):
        """Learn the machine's address from the session log so the next start is direct."""
        if not path or not path.exists():
            return None
        for line in path.read_text(errors='replace').splitlines():
            if 'pipeline_ble_target' not in line:
                continue
            try:
                return json.loads(line).get('address')
            except Exception:                                # noqa: BLE001
                return None
        return None

    def _audit(self, path):
        if not path or not path.exists():
            return None
        sys.path.insert(0, str(ROOT / 'tools'))
        from check_transparent_capture import audit
        try:
            result = audit(path)
        except AssertionError as error:
            return ('fail', f'审计未通过：{error}', str(path))
        except Exception as error:                           # noqa: BLE001
            return ('pending', f'审计无法进行：{error}', str(path))
        return ('pass',
                f"通过 · 客户端 {result['client_bytes_in']} 字节、设备 "
                f"{result['device_bytes_staged']} 字节、无伪造、无重放、干净收尾"
                f"={result['clean_shutdown']}", str(path))


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.supervisor = Supervisor(self.enqueue)
        root.title('NEJE Bridge — LightBurn 蓝牙控制台')
        root.geometry('760x610')
        root.minsize(680, 560)
        self._build()
        root.after(500, self._drain)
        root.after(1000, self._refresh)
        self.log('准备就绪。确认安全条件后点「启动（完整功能）」。')

    # -- ui --------------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill='x')

        self.state = tk.StringVar(value='已停止')
        ttk.Label(top, text='状态', font=('', 12, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(top, textvariable=self.state, foreground='#444').grid(row=0, column=1, sticky='w')
        self.detail = tk.StringVar(value='')
        ttk.Label(top, textvariable=self.detail, foreground='#666').grid(row=1, column=1, sticky='w')

        ttk.Label(top, text='有效时间').grid(row=2, column=0, sticky='w', pady=(8, 0))
        self.hours = tk.DoubleVar(value=7.0)
        ttk.Spinbox(top, from_=0.5, to=7, increment=0.5, width=6,
                    textvariable=self.hours).grid(row=2, column=1, sticky='w', pady=(8, 0))
        ttk.Label(top, text='小时（LightBurn 断开重连不需重启）').grid(row=2, column=2, sticky='w',
                                                              pady=(8, 0), padx=(6, 0))

        buttons = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        buttons.pack(fill='x')
        self.start_btn = ttk.Button(buttons, text='启动（完整功能）', command=self.on_start)
        self.start_btn.pack(side='left')
        self.test_btn = ttk.Button(buttons, text='隔离试跑', command=self.on_test)
        self.test_btn.pack(side='left', padx=6)
        self.stop_btn = ttk.Button(buttons, text='停止', command=self.on_stop)
        self.stop_btn.pack(side='left')
        self.estop_btn = ttk.Button(buttons, text='急停', command=self.on_estop)
        self.estop_btn.pack(side='right')
        self.arm_btn = ttk.Button(buttons, text='重新准备连接', command=self.on_rearm)
        self.arm_btn.pack(side='right', padx=6)

        safety = ttk.LabelFrame(self.root, text='出光前确认（完整功能模式）', padding=8)
        safety.pack(fill='x', padx=10, pady=(0, 8))
        ttk.Label(safety, text=' · '.join(SAFETY), foreground='#8a1f11',
                  wraplength=700, justify='left').pack(anchor='w')
        ttk.Label(safety, text='只有设备自身的电源开关能真正停下它。',
                  foreground='#8a1f11').pack(anchor='w')

        verdict = ttk.LabelFrame(self.root, text='上一个会话的独立审计', padding=8)
        verdict.pack(fill='x', padx=10, pady=(0, 8))
        self.verdict = tk.StringVar(value='（还没有会话）')
        ttk.Label(verdict, textvariable=self.verdict, wraplength=700,
                  justify='left').pack(anchor='w')

        logbox = ttk.LabelFrame(self.root, text='运行记录', padding=6)
        logbox.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self.text = scrolledtext.ScrolledText(logbox, height=12, wrap='word',
                                              font=('Menlo', 11))
        self.text.pack(fill='both', expand=True)
        self.text.configure(state='disabled')

    # -- actions ---------------------------------------------------------------
    def on_start(self):
        if not messagebox.askokcancel(
                '确认安全条件',
                '完整功能模式会让激光按 LightBurn 的指令出光。\n\n请确认：\n  · '
                + '\n  · '.join(SAFETY) + '\n\n只有设备自身的电源开关能真正停下它。\n\n继续？'):
            return
        self.supervisor.start(test=False, hours=float(self.hours.get()))

    def on_test(self):
        self.supervisor.start(test=True, hours=float(self.hours.get()))

    def on_rearm(self):
        """Fallback: start a fresh session now, without touching the validity window."""
        if self.supervisor.running():
            self.log('桥接已在运行')
            return
        if self.supervisor.deadline <= time.time():
            self.supervisor.deadline = time.time() + float(self.hours.get()) * 3600
        self.supervisor._spawn()
        self.log('已手动准备一个新的会话')

    def on_stop(self):
        self.supervisor.stop()

    def on_estop(self):
        if not messagebox.askokcancel('急停', '立即向设备发送停止字节（! + 0x85 + 0x18）？'):
            return
        log = LOG_ROOT / f'estop-{datetime.datetime.now().strftime("%Y%m%dT%H%M%S")}.jsonl'
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(ROOT / 'tools' / 'emergency_stop.py'),
                   '--execute', '--log', str(log)]
        subprocess.Popen(command)
        self.log(f'已发送急停（{log.name}）。复位后坐标不可信，需要重新回零。')

    # -- plumbing --------------------------------------------------------------
    def enqueue(self, message):
        self.supervisor._trace(message)
        self.queue.put(message)

    def log(self, message):
        stamp = datetime.datetime.now().strftime('%H:%M:%S')
        self.text.configure(state='normal')
        self.text.insert('end', f'[{stamp}] {message}\n')
        self.text.see('end')
        self.text.configure(state='disabled')

    def _drain(self):
        while not self.queue.empty():
            self.log(self.queue.get())
        self.root.after(400, self._drain)

    def _refresh(self):
        # Never let an exception break the chain: if this method stops rescheduling
        # itself the supervisor silently stops restarting sessions, which is exactly
        # how the auto-reconnect failed the first time it was tried on hardware.
        try:
            self._refresh_once()
        except Exception as error:                           # noqa: BLE001
            self.log(f'刷新出错（已继续）: {error!r}')
        finally:
            self.root.after(1000, self._refresh)

    def _refresh_once(self):
        self.supervisor.tick()
        if self.supervisor.running():
            left = self.supervisor.remaining()
            self.state.set('桥接运行中')
            self.detail.set(f'第 {self.supervisor.sessions} 个会话 · 会话剩余 '
                            f'{int(left // 3600)} 小时 {int(left % 3600 // 60)} 分 · '
                            f'模式：{"隔离试跑" if self.supervisor.test else "完整功能"}')
        elif self.supervisor.deadline:
            self.state.set('已停止')
            self.detail.set('有效时间已用完' if self.supervisor.deadline == 0 else '')
        else:
            self.state.set('已停止')
            self.detail.set('')
        verdict = self.supervisor.last_verdict
        if verdict:
            kind, text, path = verdict
            self.verdict.set(f'{"✅" if kind == "pass" else "⚠️"} {text}\n日志：{path}')
        for widget, enabled in ((self.start_btn, not self.supervisor.running()),
                                (self.test_btn, not self.supervisor.running()),
                                (self.stop_btn, self.supervisor.running())):
            widget.state(['!disabled'] if enabled else ['disabled'])
        # ttk states are 'disabled' / '!disabled'; a bare 'normal' raises TclError.
        self.estop_btn.state(['!disabled'] if self.supervisor.running() else ['disabled'])
        self.arm_btn.state(['!disabled'] if not self.supervisor.running() else ['disabled'])


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
