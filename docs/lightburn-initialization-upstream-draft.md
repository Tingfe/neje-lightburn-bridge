# LightBurn 初始化兼容问题：上游询问草稿（未发送）

以下草稿仅保存在本地，没有向论坛、邮件或第三方提交。

Subject: Per-device query-only connection initialization in LightBurn 2.0.05

We are testing LightBurn Pro 2.0.05 on macOS with a NEJE 5 Plus through a local TCP-to-BLE bridge. The device reports GRBL 1.1f / NEJE v2.3.2. This phase permits only `$I` and `?`, with real device responses and no synthetic acknowledgements.

Both the GRBL profile and a Custom GCode profile using the GRBL flavor send `G0`, then `$I`, `$#`, and periodic `?` on connection. `HomeOnStartup=false`, DTR disabled, and `AllowSettingsFetch=false` prevent homing/config-fetch attempts but do not remove `G0` or `$#` from this sequence. The bridge rejects forbidden commands with an explicitly identified local policy error; real `$I` and status responses are delivered normally.

Is there a supported per-device setting or extension point to use `$I` as the connection probe and omit `$#`? The Custom GCode templates we inspected expose job/motion/home/position commands, but we did not find an independent connection-probe template. We need to keep `G0` out of the device and cannot acknowledge it as executed or consume another query's `ok` on its behalf.

If this is currently hard-coded, please consider a query-only initialization profile or configurable probe/optional startup queries. We can provide a minimal timestamped TCP command trace and an exported test profile.

本地证据：[调查报告](lightburn-pending.md)。提交前应只挑选必要的命令轨迹和测试配置，避免把完整偏好文件及无关设备配置作为附件。
