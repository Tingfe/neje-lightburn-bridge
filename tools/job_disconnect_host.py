"""Real LightBurn fixed-job BLE disconnect experiment; offline by default.

The subclass delegates every GATT write to Bleak unchanged, then requests a real
disconnect after the exact reviewed job block returns, before NEJE verification.
No simulated radio, automatic inspection/reconnect, reset or source replay.
"""
import asyncio
import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pipeline_tcp_bridge as bridge
import reviewed_half_line as half_line
from grbl_transport import encode_write
from reviewed_ten_mm import review, wire_lines
from neje_protocol import GRBL_COMM_STATUS
from motion_parity_probe import parse_status


def arm_verified_run(event,fields,instances,log):
    if event!='pipeline_response_lines' or not instances or instances[0].verified_run:
        return
    for line in bytes.fromhex(fields['hex']).decode().splitlines():
        if line.startswith('<Run|'):
            state,position=parse_status(line)
            if 2<=position[0]<=8 and instances[0].job_packet is not None:
                instances[0].verified_run=line
                log('fault_run_verified',report=line,position=position,
                    source='verified pipeline_response_lines')
                return


def observed_client(base, log, instances,phase='after-write',job=None):
    if job is None:
        job=b''.join(wire_lines())
    class ObservedClient(base):
        def __init__(self,*args,**kwargs):
            if instances:raise RuntimeError('Fault experiment prohibits a second BLE connection')
            super().__init__(*args,**kwargs)
            self.job_disconnect_requested=False
            self.job_packet=None
            self.partial_payload=None
            self.verified_run=None
            instances.append(self)

        async def write_gatt_char(self,char_specifier,data,**kwargs):
            packet=bytes(data)
            if self.job_disconnect_requested:
                log('experiment_gatt_after_disconnect_request',hex=packet.hex())
            body=packet[8:-1] if len(packet)>8 else b''
            is_job=(packet[3:5]==b'\x40\x03' and body==job)
            # A partial-line write is a proper prefix of the reviewed job that ends
            # inside a line. The bridge streams ordinary GCode character-wise, so
            # the controller keeps the remainder as a half line.
            is_partial=(phase=='partial-line' and packet[3:5]==b'\x40\x03' and body
                        and len(body)<len(job) and job.startswith(body)
                        and not body.endswith(b'\n'))
            if is_job:
                assert packet==encode_write(job,int.from_bytes(packet[6:8],'little'))
                assert kwargs.get('response') is False
            await super().write_gatt_char(char_specifier,data,**kwargs)
            if is_job:
                self.job_packet=packet
                log('job_gatt_write_returned',hex=packet.hex(),payload_hex=job.hex(),
                    device_acceptance_verified=False)
            if is_partial and self.partial_payload is None:
                self.job_packet=packet
                self.partial_payload=body
                log('job_gatt_partial_write_returned',hex=packet.hex(),payload_hex=body.hex(),
                    ends_with_lf=False,payload_bytes=len(body),
                    attempted_counter_before=int.from_bytes(packet[6:8],'little'),
                    device_acceptance_verified=False)
            if phase=='after-write':
                trigger=is_job
            elif phase=='partial-line':
                trigger=is_partial
            else:
                trigger=packet==GRBL_COMM_STATUS and self.verified_run is not None
            if trigger and not self.job_disconnect_requested:
                self.job_disconnect_requested=True
                job_packet=self.job_packet
                payload=job if phase!='partial-line' else self.partial_payload
                log('job_link_disconnect_requested',is_connected=self.is_connected,
                    method='real BleakClient.disconnect',payload_bytes=len(payload),
                    attempted_counter_before=int.from_bytes(job_packet[6:8],'little'),
                    attempted_counter_after=(int.from_bytes(job_packet[6:8],'little')+len(payload))&65535,
                    phase=phase,verified_run=self.verified_run,trigger_packet_hex=packet.hex(),
                    payload_ends_with_lf=payload.endswith(b'\n'),
                    automatic_reconnect=False,remote_stop_inferred=False)
                await self.disconnect()
                log('job_link_disconnect_returned',is_connected=self.is_connected)
    return ObservedClient


async def run(args,log):
    from bleak import BleakClient
    instances=[]
    def observe(event,**fields):
        log(event,**fields)
        if args.disconnect_phase=='verified-run':arm_verified_run(event,fields,instances,log)
    observed=observed_client(BleakClient,observe,instances,args.disconnect_phase,
                             half_line.job(args.job_file)
                             if args.disconnect_phase=='partial-line' else None)
    failure=None
    with patch('bleak.BleakClient',observed):
        try:await bridge.run(args,observe)
        except Exception as error:
            failure=repr(error)
            log('fault_experiment_bridge_error',message=failure)
    if len(instances)!=1 or not instances[0].job_disconnect_requested:
        raise RuntimeError('Exact job write did not trigger the intended fault; no retry')
    if args.disconnect_phase=='partial-line':
        payload=instances[0].partial_payload
        if not payload or payload.endswith(b'\n'):
            raise RuntimeError('Partial-line phase did not capture a mid-line write; no retry')
        log('fault_experiment_half_line',payload_hex=payload.hex(),payload_bytes=len(payload),
            ends_with_lf=False,line_boundary_not_reached=True,
            device_input_state='unverified_half_line_possible')
    if instances[0].is_connected or failure is None:
        raise RuntimeError('Bridge did not report disconnected failure')
    log('fault_experiment_capture_complete',ble_connections=len(instances),
        bridge_failed=True,automatic_reconnect=False,job_success_claimed=False,
        independent_audit_required=True,explicit_inspection_required=True)


def parse_args(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-job-disconnect',action='store_true')
    parser.add_argument('--disconnect-phase',
                        choices=('after-write','verified-run','partial-line'),default='after-write')
    own,rest=parser.parse_known_args(argv)
    args=bridge.parse_args(rest)
    args.disconnect_phase=own.disconnect_phase
    file_review=getattr(args,'file_review',None)
    if args.disconnect_phase=='partial-line':
        # The half-line scenario needs a line longer than one write payload, which
        # the fixed 10 mm job does not contain.
        if args.job_file is None or file_review is None:
            parser.error('partial-line phase requires --job-file with its reviewed digest and profile')
        if not (args.allow_reviewed_file and args.allow_reviewed_xy_file):
            parser.error('partial-line phase requires --allow-reviewed-file and --allow-reviewed-xy-file')
        if args.capture_controls_without_forwarding:
            parser.error('partial-line phase cannot capture controls without forwarding')
    elif args.job_file or args.capture_controls_without_forwarding:
        parser.error('Fault experiment only supports the fixed reviewed 10 mm job')
    if args.execute and not (own.allow_job_disconnect and args.allow_reviewed_job_controls):
        parser.error('Requires --allow-job-disconnect and --allow-reviewed-job-controls')
    if args.execute and args.disconnect_phase!='partial-line' and not args.allow_reviewed_ten_mm_job:
        parser.error('Fixed-job phase requires --allow-reviewed-ten-mm-job')
    return args


def main(argv=None):
    args=parse_args(argv)
    if not args.execute:
        if args.disconnect_phase=='partial-line':
            plan=dict(offline_only=True,review=half_line.review(args.job_file).summary(),
                split=half_line.split_plan(),phase=args.disconnect_phase,
                fault='real host BLE disconnect immediately after a GATT write that ends mid-line',
                automatic_reconnect=False,automatic_inspection=False,
                hardware_executed=False,motion_authorized=False)
        else:
            plan=dict(offline_only=True,review=review(),
                phase=args.disconnect_phase,
                fault='real host BLE disconnect after exact fixed-job GATT write returns before device verification',
                automatic_reconnect=False,automatic_inspection=False,hardware_executed=False)
        print(json.dumps(plan))
        return 0
    with args.log.open('x') as output:
        def log(event,**fields):
            output.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        event=event,**fields))+'\n');output.flush()
        log('fault_experiment_start',review=review(),real_ble=True,source='real LightBurn TCP',
            laser_power_isolated=True,phase=args.disconnect_phase,fault='host_requested_link_disconnect')
        try:asyncio.run(run(args,log))
        except Exception as error:
            log('fault_experiment_error',message=repr(error))
            return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
