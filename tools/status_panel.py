"""Graphical daily-use status entry: a local read-only panel over session records.

It reads the newest managed session record on disk and renders it. It never opens
BLE or a serial port, never sends anything and never starts a session, so it cannot
bypass any execution gate: everything it shows was already recorded by an audited
run. Live device reads stay with the existing tools, which keep their own
attestations.

Offline by default (prints the summary). --serve binds 127.0.0.1 only. --write
emits a static page.
"""
import argparse
import datetime
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

RECENT_EVENTS = 12
INTERESTING = ('pipeline_job_submitted', 'controlled_job_completed',
               'controlled_job_endpoint_verified', 'pipeline_ble_disconnected',
               'pipeline_stopped', 'pipeline_session_error', 'cleanup_verified',
               'pipeline_tcp_closed', 'pipeline_server_stopped', 'error',
               'fault_experiment_capture_complete', 'pipeline_wall_limit_reached')

STATE_ZH = {'running': '运行中', 'completed': '已完成', 'requires_recovery': '需要显式恢复',
            'recovered': '已恢复', 'expired': '已到期', 'failed_before_connection': '连接前失败'}


def newest_session(root):
    root = Path(root)
    if not root.is_dir():
        return None
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / 'session.json').exists()]
    if not dirs:
        return None
    return max(dirs, key=lambda d: d.stat().st_mtime)


def summarize(root='sessions'):
    directory = newest_session(root)
    if directory is None:
        return dict(found=False, session_root=str(root),
                    note='尚未建立管理会话；本面板不连接设备')
    record = json.loads((directory / 'session.json').read_text())
    job = (record.get('plan') or {}).get('job') or {}
    audit = record.get('audit') or {}
    events = []
    log = directory / 'bridge.jsonl'
    if log.exists():
        for line in log.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('event') in INTERESTING:
                events.append(dict(utc=row.get('utc', ''), event=row['event'],
                                   detail={k: v for k, v in row.items()
                                           if k not in ('utc', 'event', 'hex')}))
    return dict(found=True, session_root=str(root), session_id=record.get('session_id'),
                directory=str(directory), state=record.get('state'),
                state_zh=STATE_ZH.get(record.get('state'), record.get('state')),
                started_utc=record.get('started_utc'), finished_utc=record.get('finished_utc'),
                error=record.get('error'),
                requires_explicit_recovery=record.get('requires_explicit_recovery'),
                automatic_reconnect=record.get('automatic_reconnect'),
                job=dict(profile=job.get('geometry_profile'), sha256=job.get('sha256'),
                         commands=job.get('command_count'), power=job.get('power'),
                         power_ceiling=job.get('power_ceiling'),
                         path_mm=job.get('path_distance_mm'),
                         nominal_seconds=job.get('nominal_motion_seconds'),
                         x_range=job.get('x_range'), y_range=job.get('y_range')),
                audit=dict(passed=audit.get('passed'), verified_bytes=audit.get('verified_bytes'),
                           frames=audit.get('decoded_frames'),
                           cleanup=audit.get('cleanup'),
                           no_synthetic_ack=audit.get('no_synthetic_ack'),
                           no_replay=audit.get('no_replay')),
                recent_events=events[-RECENT_EVENTS:],
                device_accessed_by_this_panel=False, execution_gates_bypassed=False)


PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8">
<title>LaserLink 状态面板</title>
<style>
 body{{font-family:-apple-system,Helvetica,sans-serif;margin:24px;background:#0f1115;color:#e6e6e6}}
 h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#8a94a6;font-size:12px;margin-bottom:18px}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}
 .card{{background:#171a21;border:1px solid #262b36;border-radius:10px;padding:12px 14px}}
 .k{{color:#8a94a6;font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
 .v{{font-size:15px;margin-top:4px;word-break:break-all}}
 .ok{{color:#4ade80}} .warn{{color:#fbbf24}} .bad{{color:#f87171}}
 table{{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px}}
 td{{padding:3px 6px;border-bottom:1px solid #222732;vertical-align:top}}
 td.t{{color:#8a94a6;white-space:nowrap;width:70px}}
</style>
<h1>LaserLink NEJE5 状态面板</h1>
<div class="sub">只读本地会话记录 · 不连接设备 · 不绕过任何执行门槛 · {stamp}</div>
<div class="grid">{cards}</div>
<h3 style="font-size:14px;margin:20px 0 0">最近事件</h3>
<table>{events}</table>
<script>setTimeout(function(){{location.reload()}},3000)</script>
</html>"""


def _card(key, value, cls=''):
    return f'<div class="card"><div class="k">{key}</div><div class="v {cls}">{value}</div></div>'


def render(summary):
    if not summary.get('found'):
        cards = _card('会话', summary['note'], 'warn')
        return PAGE.format(stamp=datetime.datetime.now().strftime('%H:%M:%S'),
                           cards=cards, events='')
    state = summary.get('state') or ''
    cls = 'ok' if state == 'completed' else 'warn' if state == 'running' else 'bad'
    audit = summary['audit']
    cards = ''.join([
        _card('会话状态', summary.get('state_zh') or '', cls),
        _card('作业摘要', (summary['job'].get('sha256') or '')[:16] + '…'),
        _card('命令数 / 功率', f"{summary['job'].get('commands')} 条 / {summary['job'].get('power')}"),
        _card('路径 / 名义时长', f"{summary['job'].get('path_mm')} mm / {summary['job'].get('nominal_seconds')} s"),
        _card('独立审计', 'passed' if audit.get('passed') else '未通过 / 无记录',
              'ok' if audit.get('passed') else 'bad'),
        _card('核验字节 / 帧', f"{audit.get('verified_bytes')} / {audit.get('frames')}"),
        _card('通道清理', str((audit.get('cleanup') or {}).get('way', '—')) +
              ' / app' + str((audit.get('cleanup') or {}).get('final_app', '—'))),
        _card('需显式恢复', str(summary.get('requires_explicit_recovery')), 
              'bad' if summary.get('requires_explicit_recovery') else 'ok'),
    ])
    rows = ''.join(f'<tr><td class="t">{(e["utc"] or "")[11:19]}</td><td>{e["event"]}</td>'
                   f'<td>{json.dumps(e["detail"], ensure_ascii=False)[:120]}</td></tr>'
                   for e in reversed(summary['recent_events']))
    return PAGE.format(stamp=datetime.datetime.now().strftime('%H:%M:%S'), cards=cards, events=rows)


def make_handler(root):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/state'):
                body = json.dumps(summarize(root), ensure_ascii=False, indent=2).encode()
                kind = 'application/json'
            else:
                body = render(summarize(root)).encode()
                kind = 'text/html; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-root', default='sessions')
    parser.add_argument('--serve', action='store_true', help='Bind 127.0.0.1 and serve the panel')
    parser.add_argument('--port', type=int, default=4010)
    parser.add_argument('--write', type=Path, help='Write a static HTML page instead of printing')
    args = parser.parse_args()
    if args.serve:
        server = HTTPServer(('127.0.0.1', args.port),
                            make_handler(args.session_root))
        print(json.dumps(dict(serving=f'http://127.0.0.1:{args.port}/',
                              session_root=args.session_root, read_only=True,
                              device_accessed=False, execution_gates_bypassed=False),
                         ensure_ascii=False))
        server.serve_forever()
        return 0
    summary = summarize(args.session_root)
    if args.write:
        args.write.write_text(render(summary))
        print(json.dumps(dict(written=str(args.write), **{k: summary[k] for k in
                     ('found', 'state') if k in summary}), ensure_ascii=False))
        return 0
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
