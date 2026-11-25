# Heat Score 与状态机 / Heat Score & State Machine

本文对应 `thermal_core.cpp`（设备端）和 `flexrest_engine.py`（逐行对应的 Python 版本）。
所有数值在 `params.h`，Python 侧 `flexrest_params.py` 必须一致（`test_params_parity.py` 检查）。

## 1. 每秒一次的流水线

```text
NTC(GPIO1) ─┐
DHT11 ──────┼─> 安全检查 ─> 60 s 滑动窗口 + 个人基线 ─> Heat Score / Trend / Confidence
            │                                              │
            │                                   分级状态机（迟滞 + 最短停留）
            │                                              │
            └──────────────── 串口 CSV 日志 <── 执行器保护（最大占空比 / 最长连续开启 / 冷却）
                                                           │
                                                    干预响应判定（有没有变好）
```

## 2. 特征（60 s 窗口）

| 特征 | 含义 |
|---|---|
| `skin_mean`, `skin_slope` | NTC 原始 ADC 的均值和最小二乘斜率（每秒）。`skin_raw` 只看趋势，不换算摄氏度 |
| `skin_delta` | `skin_mean - 个人基线`。基线 = 开机后前 180 s 的 `skin_raw` 均值；也可以用 `BASELINE:<raw>` 直接给定 |
| `env_mean`, `env_slope` | 床内温度（DHT11）均值和斜率 |
| `hum_relative`, `hum_slope` | 床内湿度相对基线的变化比例，以及斜率 |

窗口内样本少于 45 个时不出评分。某个传感器缺失时，它的分量**从分母中移除**，不是当成 0。

## 3. Heat Score

当前分压接法下 ADC 下降表示皮肤变暖，所以 `SKIN_WARMING_DIRECTION = -1`。每个证据先归一化成“1 = 明显在变热”：

| 分量 | 归一化 | 权重 | 证据组 |
|---|---|---|---|
| skin_delta | `-Δraw / 35` | 0.24 | 皮温 |
| skin_slope | `-slope / 0.30 raw/s` | 0.22 | 皮温 |
| env_level | `(T - 28 °C) / 4 °C` | 0.12 | 床内温度 |
| env_slope | `slope / 0.025 °C/s` | 0.14 | 床内温度 |
| humidity_relative | `ΔRH比例 / 0.10` | 0.10 | 床内湿度 |
| humidity_slope | `slope / 0.08 %/s` | 0.13 | 床内湿度 |

- **Heat Score** = 可用分量（截断到 0..1）的加权平均，范围 0..1。
- **Trend** = 三个斜率分量（截断到 -1..1）的加权平均；正 = 正在变热，负 = 正在回落。
- **Evidence** = 三个证据组中，组内最大分量 ≥ 0.25 的组数（0..3）。单一皮温跳变最多只算 1 组。
- **Confidence** = `覆盖率 × (0.45 + 0.30 × 持续性 + 0.25 × 一致性) × 基线系数`
  - 覆盖率：可用权重 / 全部权重；
  - 持续性：Heat Score ≥ 0.35 连续出现的次数 / 3；
  - 一致性：0 组 0.35，否则 `min(1, 0.45 + 0.275 × 组数)`；
  - 基线未就绪时 ×0.8。

Heat Score 回答“有多热”，Confidence 回答“有多确定”。两者分开，低把握不会通过抬高 Heat Score 来表达。

## 4. 状态机

```text
                    early×3 (score≥0.35, trend≥0.12, ≥2组, conf≥0.45)
        ┌──────────── STABLE ───────────────────────────┐
        │  high×2 (score≥0.68, ≥3组, conf≥0.60)         ▼
        │                                         EARLY_HEAT  PWM 48，每 30 s +24（≤112）
        ▼                       high×2                  │
  HIGH_HEAT_LOAD  <─────────────────────────────────────┤
  PWM 112，仍在变热(trend>0.25)每 30 s +24（≤176）       │ improving×3 且停留≥60 s
        │ improving×3 且停留≥90 s                        ▼
        └────────────────────────────────────────>  RECOVERY  每 30 s −24（≥24）
                                                        │ PWM≤24 且 score≤0.22
                    early/high 复发 → 回到 EARLY/HIGH    ▼
                                                    MAINTAIN  PWM 24
                                                        │ score≤0.15 且 |trend|≤0.10，停留≥60 s
                                                        ▼
                                                     STABLE  PWM 0

  任意状态 ── 传感器异常 / STOP ──> FAULT（PWM 0，锁存，需 RESET）
```

`improving` = `trend ≤ -0.12` 或 `score ≤ 0.40`。

### 迟滞（防止来回抖动）

1. **进入门槛高于退出门槛**：进入 EARLY 需要 0.35 + 正趋势 + 2 组证据；回到 STABLE 需要 ≤0.15 且趋势接近 0。
2. **连续窗口**：EARLY 需要连续 3 次、HIGH 连续 2 次、RECOVERY 连续 3 次满足条件，任何一次不满足计数清零。
3. **最短停留**：EARLY 60 s、HIGH 90 s、RECOVERY 30 s、MAINTAIN 30 s（且 MAINTAIN 至少 60 s 才关）。
4. **功率步进间隔**：每 30 s 最多调整一档（±24/255）。

`test_hysteresis_no_chatter_around_early_threshold` 用在 0.34/0.36 之间来回跳的分数验证：输出不会开关抖动。

### 为什么逐级退出

夜里“热完又冷”是常见的抱怨。RECOVERY → MAINTAIN → STABLE 是逐级降功率，而不是热一消失就满功率直接切断或继续猛吹冷，目的是不让“降温”本身变成下一次冷醒的原因。

## 5. 执行器保护（ActuatorGuard）

与状态机独立的最后一道软件限制：

| 限制 | 数值 | 行为 |
|---|---|---|
| 最大占空比 | 176/255（约 69%） | 超出部分截断，日志 `guard=clamped_max_duty` |
| 最长连续开启 | 1800 s | 到时强制关断，`guard=max_on_time_cooldown` |
| 冷却 | 600 s | 冷却期内即使状态机请求也输出 0，`guard=cooldown` |
| FAULT | — | 输出 0，`guard=fault_off` |

默认固件是**模拟执行器**：`FLEXREST_SIMULATE_ACTUATOR=1` 时 MOSFET 栅极始终为低，日志里的 `pwm_out` 是“如果接上会输出多少”。软件限制不能代替硬件保护，见 [hardware_safety.md](hardware_safety.md)。

## 6. 干预响应（有没有变好）

输出开启或档位改变后，观察至少 30 s，再与开始时的 Heat Score 比较：

| 响应 | 条件 |
|---|---|
| `WAITING_FOR_RESPONSE` | 刚开始/刚换档，或观察不足 30 s |
| `IMPROVING` | 分数下降 ≥0.06 或趋势 ≤ -0.10，连续 2 次 |
| `WORSENING` | 分数上升 ≥0.06 且趋势 ≥ 0.10 |
| `RECOVERED` | 分数 ≤0.15 且趋势平稳连续 2 次，或输出关闭时已回到 STABLE |
| `NO_CLEAR_RESPONSE` | 其他 |

这只是对“分数怎么变”的描述，不是对人体效果的结论。

## 7. 安全检查（fail-closed）

| 检查 | 触发 FAULT |
|---|---|
| NTC 原始值 < 50 或 > 4045 | 开路 / 短路 |
| 床内温度不在 -10..60 °C | `env_temp_abnormal` |
| 湿度不在 0..100 % | `humidity_abnormal` |
| DHT11 超过 10 s 没有有效读数 | `env_sensor_missing`（`REQUIRE_ENVIRONMENT=1`） |
| 串口 `STOP` | `manual_stop` |

FAULT 锁存：排除问题后发送 `RESET` 才回到 STABLE。
