# 系统版本评测记录（提升曲线数据源）

## v0 基线（重构前，M1 架构）
- 2026-08-15 第一次：misc 36s / crypto 64s / web 112s / pwn 120s（4/4）
- 2026-08-15 第二次：misc 58s / crypto 68s / pwn 98s / web 168s（4/4）
- 已知问题：print 模式 v4-pro 空输出、孤儿 worker、提交纪律粗放、无僵局检测

## v0.1（P0 重构后：状态机 + json 模式 + 进程组杀 + 提交纪律）
- 2026-08-15：crypto 68s / pwn 110s / web 200s / misc 927s（4/4）
- 备注：misc 927s 为模型随机绕圈（无僵局检测，P1 待修）；json 模式验证 v4-pro 修复路径
- 新能力：状态机 6 态、递增提交冷却 [0,15,60,180]、去重、孤儿清理、事件级输出解析

## 待记录
- v0.2（P1：BasePlatform + 僵局双层 + 解析回退 + planning）
- v0.3（P2：triage + 看板 + 复核开关 + 训练闭环）
- 之后：CTFTiny/DASCTF benchmark 成绩

## v0.2（P1 后：BasePlatform + 规划器 + 签名式僵局检测 + 解析回退）
- 2026-08-15：misc 66s / crypto 81s / pwn 111s / web 128s（4/4，web 单独复测确认无僵局误杀）
- 新能力：平台抽象层（mock/dasctf 双适配器）、planning 阶段（计划注入 worker）、
  僵局检测（工具名+参数签名 D2 / 输出 D3 / 错误率 D6 / idle，卡住即杀+带警告重派一次）、
  --only 定向调试参数
- 修复：状态机非法迁移、planner 模板转义、同名工具误杀（签名化）
