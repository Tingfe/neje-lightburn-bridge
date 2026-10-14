"""Launcher lifecycle tests; historical captures are replayed, no device I/O."""
import contextlib
import fcntl
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import lightburn_session as launcher

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'evidence/lightburn-curve-native-20261013/curve-lead-native.gc'
CAPTURE=ROOT/'evidence/curve-native-hardware-20261013/ble-curve-01.jsonl'
DIGEST='8c6b7397a75825180d278b4c3bf86251a32e67173e21a9a2e3aacd4eac9a437d'


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'sessions'
        self.source=Path(self.temp.name)/'original.gc';self.source.write_bytes(SOURCE.read_bytes())

    def run_cli(self,action,*args):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return launcher.main([action,'--session-root',str(self.root),*args])

    def file_args(self):
        return ['--execute','--allow-initialization','--laser-power-isolated',
                '--job-file',str(self.source),'--expected-job-sha256',DIGEST,
                '--file-profile','xy-linear-zero','--allow-reviewed-file',
                '--allow-reviewed-xy-file','--allow-reviewed-job-controls']

    def replay(self,options):
        args=launcher.bridge.parse_args(options)
        self.assertNotEqual(args.job_file,self.source)
        self.assertEqual(args.job_file.read_bytes(),SOURCE.read_bytes())
        # Changing the original after snapshot cannot alter the bridge input.
        self.source.write_bytes(b'M3 S1000\n')
        args.log.write_bytes(CAPTURE.read_bytes())
        return 0

    def test_preview_and_status_do_not_create_files_or_call_bridge(self):
        with patch.object(launcher.bridge,'main',side_effect=AssertionError('No execution')):
            for action in ('review','start','status'):
                self.assertEqual(self.run_cli(action),0)
        self.assertFalse(self.root.exists())

    def test_all_existing_execution_gates_validate_before_session_creation(self):
        for missing in ('--allow-initialization','--laser-power-isolated',
                        '--allow-reviewed-file','--allow-reviewed-xy-file',
                        '--allow-reviewed-job-controls'):
            with self.subTest(missing=missing):
                flags=self.file_args();flags.remove(missing)
                with patch.object(launcher.bridge,'main',side_effect=AssertionError('No execution')):
                    with self.assertRaises(SystemExit):self.run_cli('start',*flags)
                self.assertFalse(self.root.exists())
        for action,flags in [('review',self.file_args()),('start',['--log','overwrite.jsonl'])]:
            with self.assertRaises(SystemExit):self.run_cli(action,*flags)
            self.assertFalse(self.root.exists())

    def test_historical_real_job_capture_requires_independent_audit_and_snapshot(self):
        with patch.object(launcher.bridge,'main',side_effect=self.replay) as run:
            self.assertEqual(self.run_cli('start',*self.file_args()),0)
            self.assertEqual(run.call_count,1)
        state=json.loads((self.root/'state.json').read_text())
        self.assertEqual(state['state'],'completed')
        self.assertTrue(state['audit']['passed']);self.assertTrue(state['job_completed'])
        self.assertEqual(Path(state['job_snapshot']).read_bytes(),SOURCE.read_bytes())
        self.assertEqual(state['audit']['unconsumed_notification_tail'],11)
        self.assertFalse(state['requires_explicit_recovery'])

    def test_exit_zero_missing_completion_still_latches_recovery_and_blocks_restart(self):
        def missing_completion(options):
            self.replay(options)
            log=launcher.bridge.parse_args(options).log
            rows=[json.loads(line) for line in log.read_text().splitlines()]
            log.write_text(''.join(json.dumps(r)+'\n' for r in rows if r['event']!='controlled_job_completed'))
            return 0
        with patch.object(launcher.bridge,'main',side_effect=missing_completion) as run:
            self.assertEqual(self.run_cli('start',*self.file_args()),1)
            self.source.write_bytes(SOURCE.read_bytes())
            self.assertEqual(self.run_cli('start',*self.file_args()),1)
            self.assertEqual(run.call_count,1)
        state=json.loads((self.root/'state.json').read_text())
        self.assertEqual(state['state'],'requires_recovery')
        self.assertEqual(state['bridge_returncode'],0) # Process exit alone was insufficient.

    def test_interrupt_persists_unknown_and_stale_running_cannot_restart(self):
        with patch.object(launcher.bridge,'main',side_effect=KeyboardInterrupt) as run:
            self.assertEqual(self.run_cli('start',*self.file_args()),1)
            self.assertEqual(self.run_cli('start',*self.file_args()),1)
            self.assertEqual(run.call_count,1)
        state=json.loads((self.root/'state.json').read_text())
        self.assertIn('KeyboardInterrupt',state['error'])
        state['state']='running';(self.root/'state.json').write_text(json.dumps(state))
        with patch.object(launcher.bridge,'main',side_effect=AssertionError('No execution')):
            self.assertEqual(self.run_cli('start',*self.file_args()),1)

    def test_root_lock_and_corrupted_manifest_refuse_start(self):
        self.root.mkdir()
        with (self.root/'bridge.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(launcher.bridge,'main',side_effect=AssertionError('No execution')):
                self.assertEqual(self.run_cli('start',*self.file_args()),1)
        (self.root/'state.json').write_text('broken')
        with patch.object(launcher.bridge,'main',side_effect=AssertionError('No execution')):
            self.assertEqual(self.run_cli('start',*self.file_args()),1)

    def test_unconnected_timeout_is_unused_and_never_automatically_retried(self):
        def unused(options):
            args=launcher.bridge.parse_args(options)
            args.log.write_text(json.dumps(dict(event='pipeline_listening'))+'\n'+
                json.dumps(dict(event='pipeline_server_stopped',cleanup_unknown=False,failure_reason=None))+'\n')
            return 0
        with patch.object(launcher.bridge,'main',side_effect=unused) as run:
            self.assertEqual(self.run_cli('start',*self.file_args()),0)
            self.assertEqual(run.call_count,1)
        state=json.loads((self.root/'state.json').read_text())
        self.assertEqual(state['state'],'unused');self.assertFalse(state['hardware_connected'])
        self.assertFalse(state['job_completed'])

    def test_simulated_capture_never_becomes_current_real_success(self):
        args=launcher.bridge.parse_args(['--log','unused.jsonl'])
        log=Path(self.temp.name)/'simulated.jsonl'
        log.write_text(json.dumps(dict(event='pipeline_server_stopped',simulated=True))+'\n')
        with self.assertRaises(RuntimeError):launcher.assess(log,args,0)

    def test_historical_initialization_replay_passes_without_claiming_job(self):
        args=launcher.bridge.parse_args(['--log','unused.jsonl'])
        result=launcher.assess(ROOT/'evidence/pipeline-lightburn/lightburn-init-02.jsonl',args,0)
        self.assertEqual(result['state'],'completed')
        self.assertTrue(result['audit']['passed']);self.assertFalse(result['job_completed'])

    def test_scan_failure_does_not_claim_connection_or_automatically_retry(self):
        def failed(options):
            args=launcher.bridge.parse_args(options)
            args.log.write_text(json.dumps(dict(event='error',message='No unique NEJE found'))+'\n')
            return 1
        with patch.object(launcher.bridge,'main',side_effect=failed) as run:
            self.assertEqual(self.run_cli('start',*self.file_args()),1)
            self.assertEqual(run.call_count,1)
        state=json.loads((self.root/'state.json').read_text())
        self.assertEqual(state['state'],'failed_before_connection')
        self.assertFalse(state['hardware_connected'])
        self.assertFalse(state['requires_explicit_recovery'])

    def make_status_capture(self,rows):
        self.root.mkdir(exist_ok=True)
        folder=self.root/'current';folder.mkdir(exist_ok=True)
        (self.root/'state.json').write_text(json.dumps(dict(state='running',directory=str(folder),pid=1)))
        (folder/'bridge.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return folder

    def test_live_status_requires_actual_lock_and_reads_complete_events_only(self):
        folder=self.make_status_capture([dict(event='pipeline_listening'),dict(event='pipeline_tcp_ready')])
        original=(self.root/'state.json').read_bytes()
        with (self.root/'bridge.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            view=launcher.status(self.root)
            self.assertTrue(view['owner_lock_held']);self.assertEqual(view['observed_stage'],'ready')
            with (folder/'bridge.jsonl').open('a') as out:out.write('{"event":"controlled_job_completed"')
            view=launcher.status(self.root)
            self.assertEqual(view['observed_stage'],'ready');self.assertTrue(view['incomplete_capture_tail'])
            self.assertFalse(view['status_is_completion_audit'])
        self.assertEqual((self.root/'state.json').read_bytes(),original)

    def test_no_owner_lock_is_unknown_even_when_recorded_pid_exists(self):
        import os
        self.make_status_capture([dict(event='pipeline_tcp_ready')])
        state=json.loads((self.root/'state.json').read_text());state['pid']=os.getpid()
        (self.root/'state.json').write_text(json.dumps(state))
        with (self.root/'bridge.lock').open('a'):pass
        view=launcher.status(self.root)
        self.assertFalse(view['owner_lock_held'])
        self.assertEqual(view['observed_stage'],'owner_missing_or_unverified')
        self.assertTrue(view['requires_explicit_recovery'])

    def test_cleanup_event_does_not_hide_fault_or_perform_recovery(self):
        self.make_status_capture([dict(event='receive_fault',message='counter mismatch'),
                                  dict(event='cleanup_verified')])
        with (self.root/'bridge.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            view=launcher.status(self.root)
        self.assertEqual(view['observed_stage'],'fault_observed')
        self.assertEqual(view['observed_fault']['message'],'counter mismatch')
        self.assertTrue(view['read_only'])

    def test_malformed_complete_capture_is_visible_not_ready(self):
        folder=self.make_status_capture([dict(event='pipeline_tcp_ready')])
        with (folder/'bridge.jsonl').open('a') as out:out.write('invalid-json\n')
        with (self.root/'bridge.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            view=launcher.status(self.root)
        self.assertEqual(view['observed_stage'],'capture_unreadable')
        self.assertIn('capture_error',view)
        state=json.loads((self.root/'state.json').read_text());state['state']='completed'
        (self.root/'state.json').write_text(json.dumps(state))
        self.assertEqual(launcher.status(self.root)['observed_stage'],'capture_unreadable')
        (self.root/'state.json').write_text('broken')
        view=launcher.status(self.root)
        self.assertEqual(view['state'],'state_unreadable')
        self.assertTrue(view['requires_explicit_recovery'])


    def expiry_rows(self, submitted=0, completed=False):
        return [dict(event='pipeline_tcp_connected'),dict(event='pipeline_tcp_ready'),
                dict(event='pipeline_wall_limit_reached',seconds=300,client_connected=True),
                dict(event='pipeline_transmission_stopped',job_submitted=submitted,
                     job_completed=completed),
                dict(event='cleanup_verified',way=0,final_app=3),
                dict(event='pipeline_server_stopped',cleanup_unknown=False,failure_reason=None)]

    def test_wall_limit_expiry_is_recorded_and_not_claimed_as_success(self):
        result=launcher.assess_expired(self.expiry_rows())
        self.assertEqual(result['state'],'expired')
        self.assertFalse(result['requires_explicit_recovery'])
        self.assertFalse(result['job_completed'])
        self.assertFalse(result['audit']['passed'])
        self.assertFalse(result['audit']['independent_audit_run'])
        # A job that already reported completion is reported, still unclaimed.
        result=launcher.assess_expired(self.expiry_rows(submitted=18,completed=True))
        self.assertEqual(result['state'],'expired')
        self.assertTrue(result['job_completed'])
        self.assertFalse(result['audit']['passed'])

    def test_wall_limit_expiry_keeps_strict_recovery_for_real_problems(self):
        with self.assertRaises(RuntimeError):
            launcher.assess_expired(self.expiry_rows(submitted=18,completed=False))
        with self.assertRaises(RuntimeError):
            launcher.assess_expired([dict(event='pipeline_task_error',message='x')]+self.expiry_rows())
        unverified=self.expiry_rows()
        unverified[-1]=dict(event='pipeline_server_stopped',cleanup_unknown=True,failure_reason=None)
        with self.assertRaises(RuntimeError):launcher.assess_expired(unverified)
        failed=self.expiry_rows()
        failed[-1]=dict(event='pipeline_server_stopped',cleanup_unknown=False,failure_reason='boom')
        with self.assertRaises(RuntimeError):launcher.assess_expired(failed)
        truncated=self.expiry_rows()[:-1]
        with self.assertRaises(RuntimeError):launcher.assess_expired(truncated)

    def test_expired_session_allows_a_new_start_without_claiming_success(self):
        self.make_status_capture([dict(event='pipeline_wall_limit_reached')])
        state=json.loads((self.root/'state.json').read_text());state['state']='expired'
        (self.root/'state.json').write_text(json.dumps(state))
        with (self.root/'bridge.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            view=launcher.status(self.root)
        self.assertEqual(view['observed_stage'],'expired')
        message=launcher.status_message(view)
        self.assertIn('墙钟上限',message)
        self.assertNotIn('审计通过',message)
        def replay_expired(options):
            launcher.bridge.parse_args(options).log.write_text(''.join(
                json.dumps(row)+'\n' for row in self.expiry_rows()))
            return 0
        with patch.object(launcher.bridge,'main',side_effect=replay_expired):
            self.assertEqual(self.run_cli('start',*self.file_args()),0)
        final=json.loads((self.root/'state.json').read_text())
        self.assertEqual(final['state'],'expired')
        self.assertFalse(final['requires_explicit_recovery'])
        self.assertFalse(final['job_completed'])


if __name__=='__main__':unittest.main()
