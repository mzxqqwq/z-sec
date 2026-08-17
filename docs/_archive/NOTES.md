# 工程备忘与已知问题（供团队传阅）

> 维护：DSH 开发代理 | 2026-08-15 | 代码根 D:\ctf-agent

## 已知问题

| # | 问题 | 影响 | 状态/对策 |
|---|---|---|---|
| 1 | **deepseek-v4-pro 在 pi print 模式下输出为空**（18 分钟零输出，解不出 mock 题；flash 正常） | 强推理模型暂时不可用于 worker | 待排查（怀疑 reasoning 模型的最终消息无 text 部分时 print 模式不输出）；竞速配置里 flash 腿可兜底 |
| 2 | Kali API 偶发无响应（命令实际执行但 HTTP 超时） | 长命令被误判失败 | 重试即可；写文件类命令用 base64 原子写入更稳；避免 heredoc |
| 3 | Kali API 后台进程启动不稳（nohup+& 有时不存活） | web 服务等长驻进程可能没起来 | 启动后立刻单独验证；`< /dev/null` 重定向 stdin |
| 4 | Windows 端口 7777 被系统保留（WSAEACCES） | mock 平台不能绑 7777 | 用 7788 |
| 5 | 并发 worker 数 ≥8 时本机明显卡顿（含 DSH 命令通道超时） | 初赛调度上限 | max_parallel_challenges ≤4、race ≤2 保守运行 |
| 6 | orchestrator 打印 emoji 在 GBK 控制台崩溃 | 全盘崩溃 | 已修：stdout 强制 UTF-8 + 去掉 emoji |
| 7 | 附件在 Windows、worker 工具在 Kali 的文件错位 | 读不到附件 | 已修：编排器把附件 base64 上传 Kali；每题独立远程目录 /root/ctf/<cid> |
| 8 | 崩溃后遗留孤儿 pi worker 进程烧 API 额度 | 成本浪费 | 启动前清理；后续加 atexit 杀子进程 |

## 关键决策记录

- worker 运行时选 pi（earendil-works）而非自写循环：MIT、Cairn 冠军官方支持的 worker 后端、40+ provider
- 黑板编排抄 Cairn（Fact/Intent/Hint）；人工提示 = workspace/hints/<cid>.md
- Kali 桥 = pi 扩展（kali.ts）覆写 read/write/edit/bash 四个工具，REST 转发到 Kali API
- 默认模型 deepseek-v4-flash + thinking low（速度优先）；v4-pro 待问题 #1 修复后再启用
- 交卷纪律：每题最多 3 次错交、全局提交锁、429 退避

## 下一步（按优先级）

1. 排查 v4-pro 空输出（或换 deepseek-reasoner 试）
2. M2 演练结果复盘 → 调技能包与提示词
3. 8/18 测试赛：抓真实 API、适配 dasctf_client.py 端点
4. 成本统计与熔断
5. SSH pty 通道（gdb/nc 交互式工具，EnIGMA 结论的 pwn/web 胜负手）
