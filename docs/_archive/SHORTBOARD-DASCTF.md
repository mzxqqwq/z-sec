# CTF Agent 复盘报告

- 代码版本（src+skills）: `f2051cc`
- 题目数: 7
- 已解: 3/7

## 题目明细

| cid | 题型 | 状态 | 尝试 | 错误提交 | 耗时(s) | flag |
|---|---|---|---|---|---|---|
| lost LFSR key | crypto | solved | 4 | 0 | 2799 | DASCTF{f1nd_th3_hidden_Linear_R3lat1 |
| DigitalSignature | misc | solved | 2 | 0 | 409 | DASCTF{0x2b2D44D5325F0d3550296686BE2 |
| stegh | misc | dead | 2 | 0 | 1211 | - |
| Steganography | misc | dead | 3 | 0 | 2051 | - |
| ezmac | re | solved | 1 | 0 | 122 | DASCTF{83c720da35436cc0} |
| androidfile | re | dead | 2 | 0 | 986 | - |
| androidfff | re | dead | 3 | 0 | 1952 | - |

## 题型矩阵

| 题型 | 已解/总数 | 解题率 |
|---|---|---|
| crypto | 1/1 | 100% |
| misc | 1/3 | 33% |
| re | 1/3 | 33% |

## 僵局原因统计

- error rate 3/5: 10 次
- idle: 1 次
- repeated identical call: 1 次
- identical output: 1 次

## 工具使用统计（全部 worker 日志）

| 工具 | 调用 | 错误 | 错误率 |
|---|---|---|---|
| bash | 568 | 106 | 19% |
| read | 13 | 13 | 100% |
| write | 4 | 0 | 0% |

## 自动建议（规则生成，供人工判断）

- 错误率最高的工具是 `read`（13/13）——检查该工具的用法提示或技能包说明
- 未解题集中在: {'misc': 2, 're': 2} —— 优先补充对应题型技能包/模型路由
- 最常见僵局: error rate 3/5（10 次）——调僵局阈值或对应提示词
