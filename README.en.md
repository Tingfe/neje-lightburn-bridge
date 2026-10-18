<img src="assets/NEJE Bridge.png" width="96" alt="NEJE Bridge">

# NEJE Bridge

[简体中文](README.md) · English

Use a NEJE 5 Plus in LightBurn over Bluetooth. The Mac app exposes the machine as a local GRBL/TCP device at `127.0.0.1:4002`; LightBurn sends its normal jobs and controls through the bridge.

## Start

1. On an Apple Silicon Mac, download `NEJE-Bridge-macOS-arm64` from the [macOS build workflow](https://github.com/Tingfe/neje-lightburn-bridge/actions/workflows/macos-build.yml). Version 1.3.0 is available as a workflow artifact; the formal Release was still 1.2.4 at the last check on 2026-10-18. The app is not Apple-notarized, and an Intel GUI build has not been verified.
2. Open **NEJE Bridge.app**, choose **English** if desired, and click **Start bridge**.
3. For first-time setup, save your LightBurn work and quit LightBurn, then click **Create LightBurn device**. Later, choose the created device and connect normally.
4. Confirm the selected LightBurn device is **GRBL / TCP**, address `127.0.0.1`, port `4002`. Start jobs from LightBurn as usual.

The bridge stays available after a clean LightBurn disconnect. It keeps real controller responses and session logs; it does not fabricate `ok` or replay an uncertain job.

## Verified scope

The project has completed real LightBurn image and vector jobs through BLE, including a roughly 58-minute house cut. The user reports that everyday operation now feels comparable to USB. The current engineering estimate is **96%**, because the long job ended with unconfirmed BLE cleanup, and a controlled same-design USB/BLE finished-part comparison remains open. See [current status](docs/overall-progress.md) for the evidence and limits.

## Safety and help

LightBurn Stop and the app's software stop request a controller stop, but disconnecting or quitting the app does not stop motion already buffered by the machine. Keep an independent machine power cutoff available. A controller reset also invalidates the position reference; home or verify coordinates before restarting.

- [Daily operation and troubleshooting](docs/daily-use-guide.md)
- [How the bridge works](docs/architecture.md)
- [Documentation and evidence index](docs/README.md)

Source development: `python3 tools/neje.py` (requires the Python dependencies); local app build: `sh packaging/build-macos.sh python dist/macos-app`. [MIT license](LICENSE) · [Safety notice](DISCLAIMER.md).
