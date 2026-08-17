# CTF Agent 文档索引

> 2026-08-17 精简版：docs/ 只保留核心 7 份，调研/历史材料归档到 `_archive/`（不进 GitHub）。

| 文档 | 内容 | 谁读 |
|---|---|---|
| [USER-MANUAL.md](USER-MANUAL.md) | 完整操作手册：UI 全流程、目录结构、FAQ | 所有人 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构全景、一次解题 10 步、状态机、定版决策记录、瓶颈与分工 | 全员/答辩 |
| [RACEDAY.md](RACEDAY.md) | 比赛日操作卡（8/18 测试赛 + 8/21 初赛）、比赛模式提示词纪律 | 比赛日值守 |
| [BENCHMARK.md](BENCHMARK.md) | 题库与评测体系、完整性铁律、服务题容器、网络封锁与 worker 隔离 | 架构/评测 |
| [INSTALL.md](INSTALL.md) | 依赖安装与各服务启动 | 环境搭建 |
| [SECRETS-CHECKLIST.md](SECRETS-CHECKLIST.md) | 开源发布前敏感项核对清单 | 发布前必读 |

## 快速开始

1. 安装：按 [INSTALL.md](INSTALL.md) 走一遍（Windows 依赖 + Kali SSH + pi 构建）；
2. 理解：读 [ARCHITECTURE.md](ARCHITECTURE.md) 第 1-3 节（10 分钟）；
3. 使用：按 [USER-MANUAL.md](USER-MANUAL.md) 起看板跑 benchmark；
4. 比赛：赛前按 [RACEDAY.md](RACEDAY.md) 核对清单，赛前跑 `preflight.py` 全绿。

## 目录约定

- `docs/_archive/`：第三方源码调研（Cairn/verialabs/Koshary/pi/BreachWeave）、历史计划/验收/复盘——**本地留档，不上传 GitHub**；
- 代码即文档：核心流程注释都在 `src/` 源码里，文档只讲"是什么、怎么用、为什么"。
