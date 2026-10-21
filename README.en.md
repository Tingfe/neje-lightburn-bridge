<img src="assets/NEJE Bridge.png" width="96" alt="NEJE Bridge">

# NEJE Bridge

[简体中文](README.md) · English

Use a NEJE 5 Plus in LightBurn over Bluetooth. The Mac app exposes the machine as a local GRBL/TCP device at `127.0.0.1:4002`; LightBurn sends its normal jobs and controls through the bridge.

**Firmware compatibility:** On the tested NEJE 5 Plus, the bridge works with System / NEJE / GRBL components all at **2.3.2**. After all three were upgraded to **2.4.0**, its BLE GRBL channel stopped responding; connectivity returned after an official-client rollback and full power cycle. Other versions are untested, and one device does not establish a blanket 2.4.0 incompatibility. Check the [version and compatibility notes](docs/firmware-compatibility.en.md) before upgrading or connecting.

## Start

1. On an Apple Silicon Mac, download `NEJE-Bridge-1.3.0-macOS-arm64.zip` from the [v1.3.0 Release](https://github.com/Tingfe/neje-lightburn-bridge/releases/tag/v1.3.0). The app supports Chinese and English; it is not Apple-notarized, and an Intel GUI build has not been verified. Check the ZIP against the release's `SHA256SUMS.txt` before installing.
2. Open **NEJE Bridge.app**, choose **English** if desired, and click **Start bridge**.
3. For first-time setup, save your LightBurn work and quit LightBurn, then click **Create LightBurn device**. Later, choose the created device and connect normally.
4. Confirm the selected LightBurn device is **GRBL / TCP**, address `127.0.0.1`, port `4002`. Start jobs from LightBurn as usual.

The bridge stays available after a clean LightBurn disconnect. It keeps real controller responses and session logs; it does not fabricate `ok` or replay an uncertain job.

## Verified scope

The project completed real LightBurn image and vector jobs through BLE, including a roughly 58-minute house cut. The user reported that operation felt comparable to USB at that time. After all three firmware components were upgraded to 2.4.0, the old BLE GRBL status query stopped responding. **On 2026-10-19 the device was rolled back through the official older Windows client to three 2.3.2 components; after a full power cycle, USB, the BLE reliable channel, and real LightBurn initialization worked again. The current acceptance coverage estimate is 96%.** No machining job has been repeated since rollback, and 2.4.0 compatibility remains unresolved. See [current status](docs/overall-progress.md) and [rollback verification](docs/firmware-rollback-decision-20261019.md).

## Safety and help

LightBurn Stop and the app's software stop request a controller stop, but disconnecting or quitting the app does not stop motion already buffered by the machine. Keep an independent machine power cutoff available. A controller reset also invalidates the position reference; home or verify coordinates before restarting.

- [Daily operation and troubleshooting](docs/daily-use-guide.md)
- [How the bridge works](docs/architecture.md)
- [Firmware compatibility](docs/firmware-compatibility.en.md)
- [Documentation and evidence index](docs/README.md)

Source development: `python3 tools/neje.py` (requires the Python dependencies); local app build: `sh packaging/build-macos.sh python dist/macos-app`. [MIT license](LICENSE) · [Safety notice](DISCLAIMER.md).
