# 原生 GRBL 驱动的连接状态对照

2026-10-13，E88起。上一目标轮E86–E87为progress，真实管理恢复和再次通信已审计通过，但Custom GCode配置的Ready/Disconnected标签不一致。

只读检查prefs.ini确认，当前LaserLink NEJE5 Offline Review的Name/ProfilePath为Custom GCode，GCodeFlavor为grbl；现有LaserLink ReadOnly TEST则是原生GRBL、10×10测试尺寸。新增独立LaserLink NEJE5 Native GRBL配置，由现有原生测试配置生成并经LightBurn UI导入：260×400、TCP127.0.0.1:4002、HomeOnStartup=false、Fire/Frame/其他辅助关闭。保留原配置逐项不变，不编辑设备固件或设置。新驱动是否解决标签问题尚属假设。

先用现有管理入口仅初始化/查询，实际TCP/GATT记录和界面同时观察，正常退出后独立审计。若正常连接成立，再以现有独立故障工具仅初始化断连，观察标签及真实TCP关闭；管理保持故障，按已验证recover显式清理/读取，再另行正常初始化并关闭。均不载入作业、不运动、不出光、不复位或设置写入。白名单不扩大、不伪造启动行/ok/Idle/Alarm；若新驱动请求超出范围，拦截并保留证据。

本轮GUI动作在持续项目配置/验证授权内进行，默认执行开关与隔离条件仍保留。已有完整300项回归版本未改动，不重复全量；新的原始捕获分别独立审计。证据保存在evidence/native-grbl-ui-20261013/，包括配置/偏好前后差异、实际界面观察、真实会话、恢复及退出。最终目标仍包括真实加工及完整日常工作流，不以驱动初始化替代。

首次正常初始化已显示Ready，随后真实断连显示Disconnected，所属路由及只读恢复通过。基于上述新证据，最后一次恢复后连接扩展为既有E63原生矩形的精确同文件零功率运行，验证Ready/运行/完成的实际界面。源为evidence/lightburn-xy-native-20261013/xy-square-native.gc，SHA-256 ee68b21918bfee405ab9b140670847543b01c443fd741852abeb42b013b14b18，16条/160作业字节，X0–10/Y390–400、F60/M4 S0、约40秒，结束X0/Y400；与既有USB基线相同。沿用用户有效行程内持续零功率授权，不再询问次数，不追加复位、回零或出光；异常不重放或自动返程。新增现场观察仅作为观察证据询问，不作为重新授权。
