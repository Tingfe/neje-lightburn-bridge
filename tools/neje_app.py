"""Control panel for the native LightBurn TCP/BLE bridge.

Automatic rearm requires a successful exit, strict raw audit, verified route
cleanup and recent Idle with no later commands. Fault/reset recovery is explicit.
Software stop targets only the owned connected process and reports confirmation.
"""
import datetime
import json
import queue
import signal
import subprocess
import sys
import threading
import time
import os
import math
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import neje
import device_setup
import desktop_settings


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
# A clean, post-EOF Idle proof permits the next session while the GUI stays open.
# The child process still owns only one LightBurn/BLE session at a time.
RESTART_DELAY_SECONDS = 0.2
TICK_SECONDS = 0.1                    # supervisor loop interval, independent of the UI
SAFETY = ('护目镜（455nm）', '通风', '灭火设备在手边', '可随时切断设备主电源',
          '行程净空', '手离开机器')


def port_in_use(port=4002):
    return neje.port_in_use(port)


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
        self.audit_details = None
        self.recovery_required = False
        self.stop_confirmation_pending = False
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
    def start(self, test, hours=None):
        with self.busy:
            if self.running():
                self.log('已经在运行')
                return
            self.test = test
            self.recovery_required = False
            self.restart_at = None
            self.deadline = time.time() + hours * 3600 if hours is not None else math.inf
            self.sessions = 0
            self.log(f'启动桥接（{"隔离试跑" if test else "完整功能"}）')
            self._spawn()

    def _spawn(self):
        if port_in_use():
            self.deadline = 0.0
            self.log('127.0.0.1:4002 已被占用；请结束旧桥接或使用现有服务')
            return
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
        path = LOG_ROOT / f'neje-{stamp}.jsonl'
        # The bridge's wall limit is in SECONDS and must cover what is left of the
        # window (a unit slip here silently produced --seconds 1).
        lifetime = math.isinf(self.deadline)
        command, _ = neje.build_command(seconds=0 if lifetime else int(max(60, self.remaining())),
                                       log=path, test=self.test, device=self.device)
        if lifetime:
            command += ['--owner-pid', str(os.getpid())]
        self.last_log = path
        self._session_start = time.time()
        self.sessions += 1
        self.log(f'第 {self.sessions} 个会话 → {path.name}')
        diagnostic = path.with_suffix('.process.log')
        with diagnostic.open('x') as handle:
            self.process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)

    def stop(self):
        with self.busy:
            self.deadline = 0.0
            self.restart_at = None
            if self.process and self.process.poll() is None:
                self.process.terminate()
                self.log('已请求关闭桥接；这不表示设备已经停止')
            self.recovery_required = True

    def request_stop(self):
        """Request reset on the currently owned bridge, never a default USB port."""
        with self.busy:
            self.deadline = 0.0
            self.restart_at = None
            self.recovery_required = True
            if not self.running() or not hasattr(signal, 'SIGUSR1'):
                self.log('无法经当前链路请求停止：设备停止未确认，请使用独立断电')
                return False
            if not self._log_has('pipeline_tcp_ready'):
                self.log('当前桥接尚未连接设备：停止未确认，请使用独立断电')
                return False
            try:
                self.process.send_signal(signal.SIGUSR1)
            except OSError as error:
                self.log(f'停止请求失败：{error}；设备停止未确认')
                return False
            self.stop_confirmation_pending = True
            self.log('停止请求已提交到当前桥接，等待真实设备确认；复位后坐标不可信')
            return True

    def rearm(self, hours=None):
        with self.busy:
            if self.running():
                self.log('桥接已在运行')
                return
            self.restart_at = None
            self.recovery_required = False
            if self.deadline <= time.time():
                self.deadline = time.time() + hours * 3600 if hours is not None else math.inf
            self._spawn()
            self.log('已显式准备新会话；设备仍须通过真实预检')

    def tick(self):
        """Called from the UI loop: restart between sessions inside the window."""
        with self.busy:
            if self.restart_at is not None:
                # Wait for the settle time AND for the old listener to be gone, then
                # start at once. Polling the port is what makes the gap imperceptible
                # instead of a fixed delay the operator has to sit through.
                ready = time.time() >= self.restart_at and not port_in_use()
                if ready:
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
                if self.stop_confirmation_pending and (self._log_has('controlled_session_cancelled') or
                        self._log_has('pipeline_native_reset_completed')):
                    self.stop_confirmation_pending = False
                    self.log('已收到复位后的真实 Idle/Alarm 状态；坐标仍不可信，需显式恢复')
                return
            exit_code = self.process.poll()
            self.process = None
            self.device = self.device or self._remember_address(self.last_log)
            self.last_verdict = self._audit(self.last_log)
            if exit_code != 0:
                self.log(f'桥接退出码 {exit_code}；诊断日志：{self.last_log.with_suffix(".process.log")}')
            if self.stop_confirmation_pending:
                confirmed = self._log_has('controlled_session_cancelled') or self._log_has('pipeline_native_reset_completed')
                self.log('已收到复位后的真实 Idle/Alarm 状态；坐标不可信' if confirmed else
                         '本次停止未获设备确认，请使用独立断电')
                self.stop_confirmation_pending = False
            cancelled = self._log_has('controlled_session_cancelled')
            if (exit_code != 0 or not self.last_verdict or self.last_verdict[0] != 'pass'
                    or cancelled or self.recovery_required
                    or not self.audit_details or not self.audit_details.get('restart_safe')):
                self.deadline = 0.0
                self.restart_at = None
                self.recovery_required = True
                self.log('自动重连已禁止：需检查故障、清理和机器状态后显式恢复')
                return
            if self.deadline <= time.time():
                self.log('有效时间已用完，桥接不再重启')
                self.deadline = 0.0
                return
            if self.audit_details.get('position_recovery_required'):
                self.log('上次会话发生过复位；连接可重新建立，但坐标需由操作者确认或回零')
            self.log('LightBurn 已断开；桥接继续待命，准备下一个连接')
            self.restart_at = time.time() + RESTART_DELAY_SECONDS

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
        self.audit_details = None
        if not path or not path.exists():
            return None
        sys.path.insert(0, str(ROOT / 'tools'))
        from check_transparent_capture import audit
        try:
            result = audit(path)
            self.audit_details = result
        except (AssertionError, ValueError, KeyError) as error:
            return ('fail', f'审计未通过：{error}', str(path))
        except Exception as error:                           # noqa: BLE001
            return ('pending', f'审计无法进行：{error}', str(path))
        if not result['passed'] or not result['clean_shutdown']:
            return ('pending', '会话或清理尚未完整核验', str(path))
        return ('pass',
                f"通过 · 客户端 {result['client_bytes_in']} 字节、设备 "
                f"{result['device_bytes_staged']} 字节、无伪造、无重放、干净收尾"
                f"={result['clean_shutdown']}", str(path))


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.closed = threading.Event()
        self.closing = False
        self.supervisor = Supervisor(self.enqueue)
        self.scanning = False
        self.pending_start = None
        self.confirmed_mode = None
        self.saved_device = desktop_settings.load()
        if self.saved_device:
            self.supervisor.device = self.saved_device['address']
        root.title('NEJE Bridge')
        root.geometry('720x300')
        root.minsize(680, 300)
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        if sys.platform == 'darwin': root.createcommand('::tk::mac::Quit', self.on_close)
        self._build()
        threading.Thread(target=self._supervise, daemon=True).start()
        root.after(500, self._drain)
        root.after(1000, self._refresh)
        self.log('点「启动桥接」，再在 LightBurn 选择蓝牙桥接设备。')
        if not self.saved_device: root.after(250, self.on_scan)

    def _supervise(self):
        """Fast, independent of the UI: keeps the reconnect gap to a fraction of a second."""
        while not self.closed.is_set():
            try:
                self.supervisor.tick()
            except Exception as error:                       # noqa: BLE001
                self.enqueue(f'监督器出错（已继续）: {error!r}')
            self.closed.wait(TICK_SECONDS)

    # -- ui --------------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self.root, padding=(16, 12, 16, 6))
        top.pack(fill='x')
        ttk.Label(top, text='NEJE → LightBurn', font=('', 19, 'bold')).pack(anchor='w')
        ttk.Label(top, text='蓝牙透明桥接 · 127.0.0.1:4002').pack(anchor='w', pady=(2, 8))
        self.state = tk.StringVar(value='桥接未启动')
        ttk.Label(top, textvariable=self.state, font=('', 14, 'bold')).pack(anchor='w')
        self.detail = tk.StringVar(value='')
        ttk.Label(top, textvariable=self.detail, wraplength=650).pack(anchor='w', pady=(2, 3))
        name = self.saved_device['name'] if self.saved_device else '正在寻找 NEJE…'
        self.selected = tk.StringVar(value='设备：' + name)
        ttk.Label(top, textvariable=self.selected).pack(anchor='w')
        buttons = ttk.Frame(self.root, padding=(16, 0, 16, 6))
        buttons.pack(fill='x')
        self.start_btn = ttk.Button(buttons, text='启动桥接', command=self.on_primary)
        self.start_btn.pack(side='left', ipadx=12, ipady=3)
        names = device_setup.lightburn_tcp_device()
        self.create_btn = ttk.Button(buttons, text='打开 LightBurn' if names else '创建 LightBurn 设备',
                                     command=self.on_create_device)
        self.create_btn.pack(side='left', padx=(8, 0), ipady=3)
        ttk.Style().configure('Stop.TButton', foreground='#A92D2D')
        self.estop_btn = ttk.Button(buttons, text='软件停止设备', command=self.on_estop,
                                   style='Stop.TButton')
        self.estop_btn.pack(side='left', padx=(8, 0), ipady=3)
        setup = ttk.Frame(self.root, padding=(16, 0, 16, 5))
        setup.pack(fill='x')
        self.setup = tk.StringVar(value=self.setup_text())
        ttk.Label(setup, textvariable=self.setup, wraplength=650, justify='left').pack(anchor='w')
        ttk.Label(setup, text='断开连接不会停止设备已缓冲的动作；软件停止未确认时请独立断电。',
                  wraplength=650).pack(anchor='w', pady=(3, 0))
        toggles = ttk.Frame(self.root, padding=(16, 0, 16, 2))
        toggles.pack(fill='x')
        self.show_advanced = tk.BooleanVar(value=False)
        self.show_logs = tk.BooleanVar(value=False)
        ttk.Checkbutton(toggles, text='设备与测试选项', variable=self.show_advanced,
                        command=self.toggle_panels).pack(side='left')
        ttk.Checkbutton(toggles, text='运行记录', variable=self.show_logs,
                        command=self.toggle_panels).pack(side='left', padx=10)
        self.advanced = ttk.Frame(self.root, padding=(16, 4, 16, 4))
        pick = ttk.Frame(self.advanced)
        pick.pack(fill='x')
        self.pick = tk.StringVar(value='（扫描后选择设备）')
        self.picker = ttk.Combobox(pick, textvariable=self.pick, width=48, state='readonly')
        self.picker.pack(side='left', padx=6)
        self.picker.bind('<<ComboboxSelected>>', lambda event: self.on_pick())
        ttk.Button(pick, text='重新扫描', command=self.on_scan).pack(side='left')
        extra = ttk.Frame(self.advanced)
        extra.pack(fill='x', pady=4)
        self.test_btn = ttk.Button(extra, text='隔离试跑', command=self.on_test)
        self.test_btn.pack(side='left')
        ttk.Button(extra, text='连接说明', command=self.on_steps).pack(side='left', padx=6)
        ttk.Button(extra, text='打开日志文件夹', command=self.on_logs).pack(side='left')
        self.found = []
        self.logbox = ttk.LabelFrame(self.root, text='运行记录与会话核验', padding=6)
        self.verdict = tk.StringVar(value='（还没有会话）')
        ttk.Label(self.logbox, textvariable=self.verdict, wraplength=650,
                  justify='left').pack(anchor='w')
        self.text = scrolledtext.ScrolledText(self.logbox, height=8, wrap='word',
                                              font=('Menlo', 11))
        self.text.pack(fill='both', expand=True)
        self.text.configure(state='disabled')
        self.notice = tk.StringVar(value='')
        self.footer = ttk.Label(self.root, textvariable=self.notice, wraplength=650,
                                padding=(16, 4, 16, 6))
        self.footer.pack(fill='x')

    def toggle_panels(self):
        self.advanced.pack_forget()
        self.logbox.pack_forget()
        if self.show_advanced.get(): self.advanced.pack(fill='x', before=self.footer)
        if self.show_logs.get(): self.logbox.pack(fill='both', expand=True, padx=16, before=self.footer)
        height = 300 + (88 if self.show_advanced.get() else 0) + (250 if self.show_logs.get() else 0)
        self.root.geometry(f'720x{height}')

    def setup_text(self):
        names = device_setup.lightburn_tcp_device()
        if names: return 'LightBurn：请选择「' + names[0] + '」，启动桥接后连接。'
        if names is None: return '首次使用：请先安装并打开一次 LightBurn，再点创建设备。'
        return '首次使用：保存并退出 LightBurn，再点「创建 LightBurn 设备」。'

    # -- actions ---------------------------------------------------------------
    def on_scan(self):
        """List what is actually advertising, so a new or renamed device is findable."""
        if self.scanning or self.closing or self.supervisor.running(): return
        self.scanning = True
        self.log('正在扫描蓝牙设备（约 6 秒）…')

        def work():
            try:
                rows = device_setup.scan(6.0)
            except Exception as error:                       # noqa: BLE001
                message = '蓝牙初始化或扫描超时（14 秒）' if isinstance(error, TimeoutError) else str(error) or repr(error)
                self.queue.put(lambda error=message: self._scan_failed(error))
                return
            self.queue.put(lambda: self._scan_finished(rows))
        threading.Thread(target=work, daemon=True).start()

    def _scan_failed(self, error):
        self.scanning = False
        self.pending_start = None
        if not self.supervisor.device: self.selected.set('设备：扫描未完成')
        self.show_advanced.set(True)
        self.toggle_panels()
        self.log(f'扫描失败：{error}。请检查系统蓝牙及本程序的蓝牙权限。')

    def _scan_finished(self, rows):
        if self.closing: return
        self.scanning = False
        self.found = device_setup.candidates(rows)
        labels = [f'{name}  {address}' for name, address in self.found]
        self.picker.configure(values=labels or ['（没发现 NEJE）'])
        preferred = [row for row in self.found if row[1] == self.supervisor.device]
        chosen = preferred[0] if preferred else (self.found[0] if len(self.found) == 1 else None)
        if chosen:
            self.pick.set(f'{chosen[0]}  {chosen[1]}')
            self.on_pick()
            pending, self.pending_start = self.pending_start, None
            if pending is not None: self._start_mode(pending)
        else:
            self.pending_start = None
            if not self.supervisor.device:
                self.selected.set('设备：未发现 NEJE' if not self.found else '设备：请在选项中选择一台 NEJE')
            self.log('未发现 NEJE，请确认上电后重新扫描。' if not self.found else
                     '发现多台 NEJE，请展开「设备与测试选项」选择一台后启动。')
            self.show_advanced.set(True)
            self.toggle_panels()

    def on_pick(self):
        if self.supervisor.running():
            self.log('请先关闭当前桥接，再切换设备。')
            return
        label = self.pick.get()
        for name, address in getattr(self, 'found', []):
            if label.startswith(name) and address in label:
                self.supervisor.device = address
                self.selected.set(f'设备：{name}')
                try:
                    desktop_settings.save(name, address)
                except OSError as error:
                    self.log(f'本次已选用 {name}，但未能保存设备：{error}')
                    return
                self.log(f'已选用 {name}；下次启动直接连接。')
                return
        self.log('请在「扫描设备」后，从下拉框里选一台')

    def on_create_device(self):
        """Add a selectable profile once; never close an unsaved LightBurn document."""
        changed, message = device_setup.add_tcp_device(apply=False)
        if not changed:
            self.log(message)
            if device_setup.lightburn_tcp_device():
                subprocess.Popen(['open', '-a', 'LightBurn'])
                self.log('在 LightBurn 设备列表选择「LaserLink NEJE5 (蓝牙桥接)」。Ready 需真实连接确认。')
            return
        try:
            running = device_setup.lightburn_running()
        except RuntimeError as error:
            self.log(str(error))
            return
        if running:
            messagebox.showinfo('先保存并退出 LightBurn',
                                '首次添加设备需要 LightBurn 关闭，避免它覆盖配置。\n'
                                '请保存图形并退出 LightBurn，再点此按钮。仅需设置一次。')
            self.log('等待保存并退出 LightBurn 后创建设备。')
            return
        changed, message = device_setup.add_tcp_device(apply=True)
        self.log(('✅ ' if changed else '⚠️ ') + message)
        self.setup.set(self.setup_text())
        if changed:
            self.create_btn.configure(text='打开 LightBurn')
            subprocess.Popen(['open', '-a', 'LightBurn'])
            self.log('已打开 LightBurn，请在设备列表选择新增的蓝牙桥接设备。')

    def on_steps(self):
        names = device_setup.lightburn_tcp_device()
        if names:
            self.log('配置文件已有：'+', '.join(names)+'。请在LightBurn选择该设备，'
                     '连接127.0.0.1:4002；Ready及真实设备回应才证明连接成功。')
        else:
            self.log(device_setup.add_instructions())

    def on_logs(self):
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(['open', str(LOG_ROOT)])

    def on_primary(self):
        if self.supervisor.running() or self.supervisor.restart_at is not None:
            self.on_stop()
        else:
            self.on_start()

    def on_start(self):
        self._start_mode(False)

    def _start_mode(self, test):
        if self.closing or self.supervisor.running(): return
        if not self.supervisor.device:
            self.pending_start = test
            self.on_scan()
            return
        if self.supervisor.recovery_required and not messagebox.askokcancel('重新连接',
                '请确认故障已处理、设备状态及坐标已检查。\n重新连接不会重放上次作业，也不会回零或恢复坐标。'):
            return
        if self.confirmed_mode != test and not messagebox.askokcancel(
                '确认激光供电已隔离' if test else '确认安全条件',
                '隔离试跑仍原样转发 LightBurn 指令，不过滤功率或运动。\n'
                '请确认激光模组供电已物理隔离、行程净空且可独立断电。' if test else
                '完整功能模式会让激光按 LightBurn 的指令出光。\n\n请确认：\n  · '
                + '\n  · '.join(SAFETY) + '\n\n断连不会停止已缓冲动作；软件停止未确认时请独立断电。\n\n继续？'):
            return
        self.confirmed_mode = test
        self.supervisor.start(test=test)

    def on_test(self):
        self._start_mode(True)

    def on_rearm(self):
        self.on_start()

    def on_stop(self):
        self.pending_start = None
        self.supervisor.stop()

    def on_estop(self):
        self.pending_start = None
        self.supervisor.request_stop()

    def on_close(self):
        if self.closing: return
        self.closing = True
        self.supervisor.stop()
        self.log('正在关闭桥接并等待清理；这不表示设备已停止')
        self._finish_close()

    def _finish_close(self):
        self.supervisor.tick()
        if self.supervisor.running():
            self.root.after(100, self._finish_close)
            return
        self.closed.set()
        self.root.destroy()

    # -- plumbing --------------------------------------------------------------
    def enqueue(self, message):
        self.supervisor._trace(message)
        self.queue.put(message)

    def log(self, message, already_traced=False):
        if not already_traced: self.supervisor._trace(message)
        stamp = datetime.datetime.now().strftime('%H:%M:%S')
        self.text.configure(state='normal')
        self.text.insert('end', f'[{stamp}] {message}\n')
        self.text.see('end')
        self.text.configure(state='disabled')
        self.notice.set(message.splitlines()[0][:160])

    def _drain(self):
        while not self.queue.empty():
            item = self.queue.get()
            if callable(item):
                if not self.closing: item()
            else: self.log(item, already_traced=True)
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
            self.state.set('桥接运行中')
            self.detail.set(f'第 {self.supervisor.sessions} 个会话 · '
                            f'模式：{"隔离试跑" if self.supervisor.test else "完整功能"}')
        elif self.supervisor.restart_at is not None:
            self.state.set('正在准备下次连接')
            self.detail.set('正常会话结束后重新连接')
        elif self.supervisor.recovery_required:
            self.state.set('桥接已关闭 · 请检查设备状态')
            self.detail.set('重新连接需显式点击；不会自动重放作业或恢复坐标')
        else:
            self.state.set('桥接未启动')
            self.detail.set('')
        verdict = self.supervisor.last_verdict
        if verdict:
            kind, text, path = verdict
            self.verdict.set(f'{"✅" if kind == "pass" else "⚠️"} {text}\n日志：{path}')
        active = self.supervisor.running() or self.supervisor.restart_at is not None
        self.start_btn.configure(text='关闭桥接' if active else
                                 ('重新连接' if self.supervisor.recovery_required else '启动桥接'))
        self.test_btn.state(['disabled'] if active else ['!disabled'])
        self.picker.state(['disabled'] if active else ['!disabled', 'readonly'])
        # ttk states are 'disabled' / '!disabled'; a bare 'normal' raises TclError.
        self.estop_btn.state(['!disabled'])


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
