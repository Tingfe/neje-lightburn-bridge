"""Managed experimental LightBurn BLE sessions; offline preview by default.

Reuses all bridge permissions and independent capture auditors. A failed or
interrupted connected session blocks later starts in the same session root.
No automatic retry, reconnect, recovery, or permission to run a different job.
"""
import argparse
import asyncio
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

import pipeline_tcp_bridge as bridge
from check_controlled_capture import audit as job_audit
from check_pipeline_tcp_capture import audit as init_audit

ROOT=Path(__file__).resolve().parents[1]
EXPIRY_FAULTS=('pipeline_session_error','pipeline_task_error','receive_fault',
               'stream_write_uncertain','error')


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path,value):
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as output:
        json.dump(value,output,ensure_ascii=False,indent=2)
        output.write('\n');output.flush();os.fsync(output.fileno())
        temporary=Path(output.name)
    os.replace(temporary,path)


def preview(args):
    return dict(offline_only=not args.execute,bind='127.0.0.1:4002',
        profile='reviewed_file' if args.file_review else 'initialization_only',
        job=args.file_review.summary() if args.file_review else None,
        seconds=args.seconds,controls_requested=args.allow_reviewed_job_controls,
        initialization_includes_bare_G0=True,automatic_reconnect=False,
        hardware_executed=False,final_usb_equivalence_proven=False)


def assess_expired(rows):
    """Wall-clock expiry is neither a success nor a fault.

    Kept non-blocking only when the capture proves a bounded shutdown: no fault
    events, verified cleanup, and either no job or a job that already reported
    completion. Anything else stays on the strict requires_recovery path. No
    independent audit is claimed, so the state is deliberately not 'completed'.
    """
    if any(r['event'] in EXPIRY_FAULTS for r in rows):
        raise RuntimeError('Wall limit reached with a recorded fault; explicit recovery required')
    last = rows[-1]
    if (last['event'] != 'pipeline_server_stopped' or last.get('cleanup_unknown')
            or last.get('failure_reason')):
        raise RuntimeError('Wall limit reached without verified cleanup; explicit recovery required')
    stops = [r for r in rows if r['event'] == 'pipeline_transmission_stopped']
    submitted = stops[0].get('job_submitted', 0) if stops else 0
    finished = bool(stops and stops[0].get('job_completed'))
    if submitted and not finished:
        raise RuntimeError('Wall limit reached with an unfinished job; explicit recovery required')
    return dict(state='expired', hardware_connected=True, job_completed=finished,
                audit=dict(passed=False, independent_audit_run=False,
                           reason='wall_limit_expiry_not_independently_audited',
                           job_submitted=submitted, job_completed=finished),
                requires_explicit_recovery=False)


def assess(path,args,returncode):
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    if not rows or any(r.get('simulated') for r in rows):
        raise RuntimeError('Missing real capture; cannot claim managed-session success')
    connected=any(r['event']=='pipeline_tcp_connected' for r in rows)
    if not connected:
        # This bridge logs TCP entry before attempting any BLE connection.
        if returncode and any(r['event']=='error' for r in rows):
            return dict(state='failed_before_connection',hardware_connected=False)
        if (not returncode and rows[-1]['event']=='pipeline_server_stopped'
                and not rows[-1]['cleanup_unknown'] and not rows[-1].get('failure_reason')):
            return dict(state='unused',hardware_connected=False,job_completed=False)
        raise RuntimeError('Unfinished startup capture')
    if returncode:
        raise RuntimeError('Connected bridge failed; explicit recovery required')
    if not any(r['event']=='pipeline_tcp_ready' for r in rows):
        raise RuntimeError('Connected session never became ready')
    if any(r['event']=='pipeline_wall_limit_reached' for r in rows):
        return assess_expired(rows)
    if args.file_review:
        result=job_audit(path,backend='ble',reviewed_file=args.file_review,expected_controls=())
    else:
        result=init_audit(path)
        if rows[-1].get('failure_reason'):
            raise RuntimeError('Initialization ended with a recorded failure')
    return dict(state='completed',audit=result,hardware_connected=True,
                job_completed=bool(args.file_review))


def status(root):
    """Read current capture and owner lock; never repair or advance state."""
    path=root/'state.json'
    if not path.exists():return dict(state='no_managed_session')
    try:
        state=json.loads(path.read_text())
        capture=Path(state['directory'])/'bridge.jsonl'
        if not isinstance(state['state'],str):raise ValueError('Invalid session state')
    except (OSError,ValueError,KeyError,TypeError) as error:
        return dict(state='state_unreadable',read_only=True,
                    requires_explicit_recovery=True,error=repr(error))
    held=None
    if (root/'bridge.lock').exists():
        with (root/'bridge.lock').open('r') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                held=False
            except BlockingIOError:held=True
    state.update(owner_lock_held=held,read_only=True,status_is_completion_audit=False)
    stage=state['state']
    if stage=='running':stage='starting'
    stages={'pipeline_listening':'waiting_for_lightburn',
            'pipeline_tcp_connected':'initializing_ble',
            'session_ready':'checking_controller',
            'pipeline_tcp_ready':'ready',
            'pipeline_job_submitted':'job_in_progress',
            'controlled_job_endpoint_verified':'finishing_job',
            'controlled_job_completed':'job_completed_waiting_for_disconnect',
            'pipeline_transmission_stopped':'closing',
            'cleanup_verified':'route_closed',
            'pipeline_server_stopped':'bridge_stopped'}
    faults={'error','pipeline_session_error','pipeline_task_error','receive_fault',
            'stream_write_uncertain','pipeline_job_incomplete_at_eof',
            'pipeline_session_timeout','pipeline_eof_drain_timeout'}
    state['incomplete_capture_tail']=False
    if capture.exists():
        try:
            with capture.open() as source:
                for line in source:
                    if not line.endswith('\n'):
                        state['incomplete_capture_tail']=True;break
                    row=json.loads(line)
                    if row['event'] in stages:stage=stages[row['event']]
                    if row['event'] in faults and 'observed_fault' not in state:
                        state['observed_fault']=row
                    state['last_capture_utc']=row.get('utc')
        except (OSError,ValueError,KeyError,TypeError) as error:
            stage='capture_unreadable';state['capture_error']=repr(error)
    if state['state']=='running' and held is not True:
        stage='owner_missing_or_unverified'
        state['requires_explicit_recovery']=True
    elif state['state']!='running':stage=state['state']
    if state.get('capture_error'):stage='capture_unreadable'
    if state.get('observed_fault') and state['state']!='recovered':stage='fault_observed'
    state['observed_stage']=stage
    return state


def status_message(state):
    """Explain journal evidence without turning a UI/exit event into success."""
    stage=state.get('observed_stage',state.get('state'))
    if stage=='recovered' and state.get('recovery',{}).get('audit',{}).get('passed'):
        return '所属通道已关闭，当前控制器只读核查通过；原作业结果仍未知。可显式开始新会话，不会自动重放。'
    if state.get('observed_fault') or state.get('requires_explicit_recovery') or stage in (
            'requires_recovery','state_unreadable','capture_unreadable','owner_missing_or_unverified'):
        return '会话异常，尚未确认设备停止。已缓冲动作可能继续；勿直接重发作业，先核查设备并处理恢复。'
    if state.get('incomplete_capture_tail'):
        return '日志末行尚未写完，不能据此确认作业完成。'
    if stage=='expired':
        return '上次会话到达墙钟上限后已核验关闭（way=0/app3）。该次未做完整独立审计，不声明作业成功；可直接开始新会话。'
    if stage=='completed':
        if not state.get('audit',{}).get('passed'):
            return '完成记录缺少独立审计，不能确认作业成功。'
        return '上次作业和关闭已通过完整捕获审计。' if state.get('job_completed') else \
               '上次初始化和关闭已通过完整捕获审计；没有执行作业。'
    return {
        'no_managed_session':'尚未建立管理会话。',
        'waiting_for_lightburn':'正在等待LightBurn连接。',
        'initializing_ble':'正在连接蓝牙并检查设备。',
        'checking_controller':'正在核查控制器状态。',
        'ready':'连接与预检完成，等待已审阅的作业。',
        'job_in_progress':'正在传输和运行作业。',
        'finishing_job':'正在核验终点和模态。',
        'job_completed_waiting_for_disconnect':'已收到作业结束记录，等待会话关闭和独立审计。',
        'closing':'正在关闭会话。',
        'route_closed':'已收到通道关闭记录，等待完整审计。',
        'bridge_stopped':'桥接已退出，等待独立审计结果。',
        'unused':'本次没有客户端连接，未执行设备作业。',
        'failed_before_connection':'连接设备前失败，未执行设备作业。',
    }.get(stage,'状态尚未确认，请查看会话记录。')


def start(root,args,runtime):
    root.mkdir(parents=True,exist_ok=True)
    with (root/'bridge.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another managed session owns this root') from None
        state_path=root/'state.json'
        if state_path.exists():
            previous=json.loads(state_path.read_text())
            if previous.get('state') not in ('completed','unused','failed_before_connection','recovered','expired'):
                raise RuntimeError('Previous session needs explicit recovery; inspect status and evidence')
            if previous.get('state')=='recovered' and not previous.get('recovery',{}).get('audit',{}).get('passed'):
                raise RuntimeError('Recovery record lacks independent audit; no new start')
        session_id=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        folder=root/session_id;folder.mkdir()
        state=dict(schema=1,state='running',session_id=session_id,directory=str(folder),
                   pid=os.getpid(),started_utc=utc(),plan=preview(args),automatic_reconnect=False)
        save(folder/'session.json',state);save(state_path,state)
        code=None
        try:
            options=list(runtime)
            if args.file_review:
                raw=args.job_file.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=args.file_review.sha256:
                    raise RuntimeError('Source changed after review; no hardware start')
                snapshot=folder/'job.gc';snapshot.write_bytes(raw)
                options+=['--job-file',str(snapshot)]
                state['job_snapshot']=str(snapshot)
            options+=['--log',str(folder/'bridge.jsonl')]
            save(folder/'session.json',state);save(state_path,state)
            print(json.dumps(state,ensure_ascii=False),flush=True)
            with (folder/'stdout.txt').open('x') as out,(folder/'stderr.txt').open('x') as err:
                with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
                    code=bridge.main(options)
            state.update(assess(folder/'bridge.jsonl',args,code))
        except BaseException as error:
            # Also journal Ctrl-C/SystemExit; a crashed process leaves running,
            # which the next explicit start refuses without guessing remotely.
            state.update(state='requires_recovery',error=repr(error))
        state.update(finished_utc=utc(),bridge_returncode=code,
                     requires_explicit_recovery=state['state']=='requires_recovery')
        save(folder/'session.json',state);save(state_path,state)
        print(json.dumps(state,ensure_ascii=False,indent=2))
        return 1 if state['state'] in ('requires_recovery','failed_before_connection') else 0


def recover(root,*,execute=False,isolated=False,output_authorized=False):
    from managed_recovery import ownership_proof,inspect_route,audit_route,inspect_controller,audit_controller
    if not execute:
        print(json.dumps(dict(offline_only=True,action='recover',new_motion=False,
            reset=False,automatic_replay=False,scope='owned route close, then exact read-only checks',
            original_job_remains_unknown=True),ensure_ascii=False,indent=2));return 0
    if not (isolated or output_authorized):
        raise ValueError('Recovery requires a laser state attestation: --laser-power-isolated '
                         'or --laser-output-authorized')
    if not root.exists():raise ValueError('No managed session to recover')
    with (root/'bridge.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Managed owner is still live; no recovery') from None
        state=json.loads((root/'state.json').read_text())
        if state.get('state') not in ('requires_recovery','running'):
            raise ValueError('No failed/orphaned managed session to recover')
        folder=Path(state['directory']).resolve()
        if folder.parent!=root or folder.name!=state['session_id']:
            raise ValueError('Session folder is outside its recorded root/identity')
        capture=folder/'bridge.jsonl';proof=ownership_proof(capture)
        attempt=folder/('recovery-'+uuid.uuid4().hex[:8]);attempt.mkdir()
        save(attempt/'failed-state.json',state);save(attempt/'ownership.json',proof)
        result=dict(directory=str(attempt),started_utc=utc(),job_completed=False,
                    no_replay=True,no_reset=True,no_motion=True)
        save(attempt/'result.json',result)
        def record(path,runner):
            with path.open('x') as output:
                def log(event,**fields):
                    output.write(json.dumps(dict(utc=utc(),event=event,**fields))+'\n');output.flush()
                asyncio.run(runner(proof,log))
        try:
            record(attempt/'route.jsonl',inspect_route)
            route=audit_route(attempt/'route.jsonl',proof)
            result['route_audit']=route
            if not route['input_boundary_complete']:
                raise RuntimeError('Controller may retain a partial input line; no query/reset or start. Separate reset/position recovery required')
            record(attempt/'controller.jsonl',inspect_controller)
            controller=audit_controller(attempt/'controller.jsonl')
            if hashlib.sha256(capture.read_bytes()).hexdigest()!=proof['capture_sha256']:
                raise RuntimeError('Original fault capture changed during recovery')
            result.update(audit=dict(passed=True,route=route,controller=controller),finished_utc=utc())
            save(attempt/'result.json',result)
            # Never rewrite the original failed session.json or bridge capture.
            state.update(state='recovered',recovery=result,requires_explicit_recovery=False,
                         original_job_result='unknown',job_completed=False)
            save(root/'state.json',state)
            print(json.dumps(dict(state='recovered',recovery=result,
                message=status_message(state)),ensure_ascii=False,indent=2));return 0
        except BaseException as error:
            result.update(error=repr(error),finished_utc=utc(),passed=False)
            save(attempt/'result.json',result)
            print(json.dumps(dict(state='requires_recovery',recovery=result),ensure_ascii=False,indent=2))
            return 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('review','start','status','recover'))
    parser.add_argument('--session-root',type=Path,default=ROOT/'sessions')
    options,runtime=parser.parse_known_args(argv)
    root=options.session_root.resolve()
    if options.action=='recover':
        recovery_parser=argparse.ArgumentParser(description='Explicit cleanup and read-only inspection; no reset/motion/replay')
        recovery_parser.add_argument('--execute',action='store_true')
        recovery_parser.add_argument('--laser-power-isolated',action='store_true')
        recovery_parser.add_argument('--laser-output-authorized',action='store_true')
        scope=recovery_parser.parse_args(runtime)
        try:return recover(root,execute=scope.execute,isolated=scope.laser_power_isolated,
                           output_authorized=scope.laser_output_authorized)
        except (OSError,ValueError,RuntimeError) as error:
            print(str(error),file=sys.stderr);return 1
    if options.action=='status':
        if runtime:parser.error('status does not accept bridge arguments')
        view=status(root)
        view['message']=status_message(view)
        print(json.dumps(view,ensure_ascii=False,indent=2))
        return 0
    if any(arg=='--log' or arg.startswith('--log=') for arg in runtime):
        parser.error('Managed sessions assign new capture paths; --log is not accepted')
    args=bridge.parse_args(runtime+['--log',str(root/'managed-preview.jsonl')])
    if args.allow_reviewed_ten_mm_job:
        parser.error('Fixed experimental controls use the original tool; managed jobs require --job-file')
    if options.action=='review' and args.execute:
        parser.error('review is offline; remove --execute')
    if not args.execute:
        print(json.dumps(preview(args),ensure_ascii=False,indent=2))
        return 0
    try:return start(root,args,runtime)
    except (OSError,ValueError,RuntimeError) as error:
        print(str(error),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
