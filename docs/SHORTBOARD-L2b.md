# CTF Agent 复盘报告

- 代码版本（src+skills）: `b100a20`
- 题目数: 13
- 已解: 9/13

## 题目明细

| cid | 题型 | 状态 | 尝试 | 错误提交 | 耗时(s) | flag |
|---|---|---|---|---|---|---|
| cry-collision-course | crypto | solved | 2 | 0 | 678 | flag{d0nt_g3t_2_s4lty} |
| cry-describeme | crypto | dead | 3 | 3 | 659 | flag{use_good_params} |
| cry-polly-crack-this | crypto | dead | 2 | 0 | 64 | - |
| pwn-bigboy | pwn | solved | 2 | 1 | 638 | flag{Y0u_Arrre_th3_Bi66Est_of_boiiii |
| pwn-get-it | pwn | solved | 2 | 1 | 252 | flag{y0u_deF_get_itls} |
| pwn-password-checker | pwn | dead | 2 | 0 | 1098 | - |
| rev-baby-mult | rev | solved | 1 | 0 | 105 | flag{sup3r_v4l1d_pr0gr4m} |
| rev-dockreleakage | rev | solved | 1 | 0 | 138 | flag{n3v3r_l34v3_53n5171v3_1nf0rm471 |
| rev-ezbreezy | rev | solved | 1 | 0 | 195 | flag{u_h4v3_r3c0v3r3d_m3} |
| rev-rebug-2 | rev | dead | 2 | 1 | 136 | csawctf{01011100010001110000} |
| rev-sourcery | rev | solved | 1 | 0 | 163 | flag{ctf_pl4y3rz_g1t_1t_d0n3} |
| rev-tablez | rev | solved | 1 | 0 | 43 | flag{t4ble_l00kups_ar3_b3tter_f0r_m3 |
| rev-beleaf | rev | solved | 3 | 0 | 385 | flag{we_beleaf_in_your_re_future} |

## 题型矩阵

| 题型 | 已解/总数 | 解题率 |
|---|---|---|
| crypto | 1/3 | 33% |
| pwn | 2/3 | 67% |
| rev | 6/7 | 86% |

## 僵局原因统计

- error rate 3/5: 7 次

## 工具使用统计（全部 worker 日志）

| 工具 | 调用 | 错误 | 错误率 |
|---|---|---|---|
| bash | 496 | 120 | 24% |
| write | 8 | 0 | 0% |
| read | 8 | 8 | 100% |

## 自动建议（规则生成，供人工判断）

- 错误率最高的工具是 `read`（8/8）——检查该工具的用法提示或技能包说明
- 未解题集中在: {'crypto': 2, 'pwn': 1, 'rev': 1} —— 优先补充对应题型技能包/模型路由
- 最常见僵局: error rate 3/5（7 次）——调僵局阈值或对应提示词
