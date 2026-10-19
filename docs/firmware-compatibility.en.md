# Firmware compatibility

Scope: this project's **NEJE Bridge → NEJE 5 Plus → LightBurn** Bluetooth bridge. The observations below come from one Verify model 55 device with controller hardware 2.0.1; they do not establish compatibility for every NEJE machine or firmware release.

| System / NEJE / GRBL component versions | Evidence on this device | Bridge status |
| --- | --- | --- |
| **2.3.2 / 2.3.2 / 2.3.2** | Image and vector jobs, including a roughly 58-minute house cut, completed before the later upgrade. After the 2026-10-19 rollback and full power cycle, USB, the BLE reliable channel, and real LightBurn initialization worked again. No machining job has been repeated since rollback. | **Tested combination on this device.** Still confirm GRBL mode and genuine controller replies before use. |
| **2.4.0 / 2.4.0 / 2.4.0** (shown in Windows NEJE Neo as `v3.0.0 debug 3`) | USB GRBL and NEJE's native Bluetooth client worked, but repeated BLE GRBL `0x40` status queries received no reply; this bridge could not initialize. | **Current bridge protocol failed on this device.** USB or native-client connectivity does not establish LightBurn BLE bridge compatibility. |
| Other or mixed component versions | Not tested. | Verify all three actual component versions and the channel response before assuming compatibility. |

Neo's `v3.0.0 debug 3` is a firmware **bundle label**; Windows Neo `v7.0.0` is a **desktop-client version**. Neither is the version of all three firmware components. On this device, the bundle contained System, NEJE, and GRBL 2.4.0. The Mac bridge source and the 1.3.0 GUI package have no verified 2.4.0 hardware compatibility.

Do not rely on USB `$I` alone to check compatibility: it reports only the GRBL application version. Source users can close the bridge and other NEJE clients, then run `python3 tools/p0_probe.py --query`; the `app_info.apps` log entry lists the actual versions for IDs 1 (System), 2 (NEJE), and 3 (GRBL). This sends only Verify and SystemRead and does not enable the GRBL channel. Other users can save the full version information shown by the official client. If all three components cannot be confirmed, treat the combination as untested.

**Before use:** If you depend on this project for LightBurn, check the device's three firmware components and back up GRBL settings before an upgrade. If connection fails, preserve the bridge log and version readout; do not repeatedly press Start or bypass an unanswered status check. Any rollback should use the matching official NEJE update interface after reviewing the [device-specific rollback record](firmware-rollback-decision-20261019.md); that record is not a general flashing guide.

See the [2.4.0 diagnostic record](connection-init-race-20261019.md) for the raw observations. The cause of the 2.4.0 failure remains unknown, and one device cannot establish that every 2.4.0 device is incompatible.
