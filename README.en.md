<img src="assets/NEJE Bridge.png" width="110" alt="NEJE Bridge">

# NEJE 5 Plus in LightBurn over Bluetooth

**Language / 语言:** [English](README.en.md) · [简体中文](README.md)

NEJE Bridge presents the NEJE 5 Plus Bluetooth connection to LightBurn as a local GRBL/TCP device at `127.0.0.1:4002`. The desktop app offers Chinese and English. Choose **English** in the top-right corner; the preference is remembered.

## Getting started

1. Download the `NEJE-Bridge-macOS-arm64` artifact from the [macOS app workflow](https://github.com/Tingfe/neje-lightburn-bridge/actions/workflows/macos-build.yml), then install **NEJE Bridge.app** on an Apple Silicon Mac. The [latest formal release](https://github.com/Tingfe/neje-lightburn-bridge/releases/latest) is still 1.2.4; bilingual 1.3.0 has not been published as a Release. No Intel GUI package has been verified.
2. Power on the NEJE 5 Plus in GRBL mode. Open the app and choose **Start bridge**. Confirm the on-screen safety conditions before full operation.
3. If this is your first use, click **Create LightBurn device**. Save your LightBurn work and quit LightBurn before creating its profile; the app backs up the configuration. Otherwise click **Open LightBurn**.
4. In LightBurn, choose the GRBL/TCP device for `127.0.0.1:4002`, then connect. A real controller response is needed to confirm the connection; a profile in the device list alone does not prove it.

The app keeps the bridge available after a clean LightBurn disconnect. It performs an independent session audit before preparing a new connection. A fault, uncertain cleanup or controller reset requires an explicit check and reconnect; it never replays a job or restores coordinates automatically.

## Safety

Disconnecting, quitting the app or losing Bluetooth does **not** stop motion already buffered by the controller. The **Stop machine (software)** button requests a stop through the current owned connection and reports whether the machine confirmed it. If stop is unconfirmed, cut machine power independently. Wear 455 nm eye protection, provide ventilation, keep a fire extinguisher and power cutoff within reach, clear the travel area, and keep hands away from the machine.

The bridge does not fabricate GRBL success responses. Its protocol logs and machine commands are unchanged by the UI language choice. Existing LightBurn device names and earlier log entries remain as originally saved.

For setup details and troubleshooting, see the [Chinese guide](docs/gui-mac-test-guide.md). Source builds and hardware acceptance are separate from the offline package checks; see the [project status](docs/overall-progress.md).
