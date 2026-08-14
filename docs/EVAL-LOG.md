# 系统版本评测记录（提升曲线数据源）

## v0 基线（重构前，M1 架构）
- 2026-08-15 第一次：misc 36s / crypto 64s / web 112s / pwn 120s（4/4）
- 2026-08-15 第二次：misc 58s / crypto 68s / pwn 98s / web 168s（4/4）
- 已知问题：print 模式 v4-pro 空输出、孤儿 worker、提交纪律粗放、无僵局检测

## v0.1（P0 重构后：状态机 + json 模式 + 进程组杀 + 提交纪律）
- 2026-08-15：crypto 68s / pwn 110s / web 200s / misc 927s（4/4）
- 备注：misc 927s 为模型随机绕圈（无僵局检测，P1 待修）；json 模式验证 v4-pro 修复路径
- 新能力：状态机 6 态、递增提交冷却 [0,15,60,180]、去重、孤儿清理、事件级输出解析

## v0.3 首次真题评测（CTFTiny L1：8 道 easy，crypto/misc/rev）
- 成绩 4/8：crypto 2/2 ✅、rev 2/4、misc 0/2
- 发现并修复三大 bug：
  1. csawctf{...} 前缀被 ctf{ 模式截断 → 加词边界+csawctf 模式（whataxor 因此失分）
  2. 带空格的长 flag 被正则漏掉 → 字符类放宽（showdown 因此失分）
  3. 描述占位符（flag{path}/flag{md5hash}）被当 flag 提交 → 占位符黑名单（ezmaze 错交）
  4. 提交冷却吞掉后续候选 → try_submit_wait 等待重试
- 结论：评测体系按设计工作——第一次跑真题就暴露了 4 个真问题

## v0.4（L1 修复后）
- git 版本：待记录
- L1 重跑成绩：待记录

## v0.2（P1 后：BasePlatform + 规划器 + 签名式僵局检测 + 解析回退）
- 2026-08-15：misc 66s / crypto 81s / pwn 111s / web 128s（4/4，web 单独复测确认无僵局误杀）
- 新能力：平台抽象层（mock/dasctf 双适配器）、planning 阶段（计划注入 worker）、
  僵局检测（工具名+参数签名 D2 / 输出 D3 / 错误率 D6 / idle，卡住即杀+带警告重派一次）、
  --only 定向调试参数
- 修复：状态机非法迁移、planner 模板转义、同名工具误杀（签名化）

## v0.3（P2 后：triage 排序 + 看板 + 人工复核 + 训练闭环，架构 v2.1 重构完成）
- 2026-08-15，git commit 7e50d89（34 文件入库，src+docs 版本化）
- 新能力：
  - triage 先易后难排序（3h 抢分）
  - Flask 看板 dashboard.py（8088）：题目状态/待复核候选/hints 写入/复核开关/日志尾部
  - 人机回路文件协议：hints/<cid>.md、requests/confirm/<cid>.json、requests/verify/<cid>.toggle
    （看板写 → 编排器每轮消费，端到端验证通过）
  - 训练闭环 postmortem.py：失败模式统计 + 题型矩阵 + 工具错误排行 + 自动建议 + git 版本对应
- 架构 v2.1 三大阶段（P0/P1/P2）全部完成，进入 benchmark 评测
