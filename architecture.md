# 系统架构 / Architecture

## 定位

一个夜间热舒适闭环原型：估计“身体是否正在变热、变热的把握有多大”，在热不适扩大之前做最小必要的局部降温，并继续观察分数是否回落。

它估计的是**热状态趋势**，不是体温测量，也不把皮温等同于主观冷热感。皮温、床内温度、床内湿度都只是推断热状态的证据。

## 硬件（黑客松版）

| 部件 | 作用 | 接线 |
|---|---|---|
| ESP32-S3 DevKitC-1 | 采集、评分、状态机、安全限制、串口日志 | USB 供电 / 串口 115200 |
| NTC 热敏电阻 + 固定电阻分压 | 皮温**趋势**（12 位 ADC 原始值 `skin_raw`） | 分压中点 → GPIO1（ADC1_CH0，11 dB 衰减） |
| DHT11 | 床内微环境温度 / 湿度 | DATA → GPIO17（上拉到 3V3） |
| 逻辑电平 N 沟道 MOSFET + TEC | 局部降温执行器（演示时为模拟） | GPIO10 → 约 100 Ω → 栅极，栅极 10 kΩ 下拉到 GND；5 V 电源 + → TEC+，TEC− → 漏极，源极 → GND；与 ESP32 共地 |
| PCM 相变材料（概念） | 被动削峰储热，不受软件控制 | — |

```text
          3V3                              5 V 电源(限流 + 保险丝)
           │                                   │
        R_fixed                               TEC
           ├──── GPIO1 (skin_raw)              │
          NTC                          Drain ──┘
           │                 GPIO10 ─100Ω─ Gate   (10k 下拉到 GND)
          GND                          Source ── GND ── ESP32 GND
```

分压方向决定了“变热时 ADC 变大还是变小”。原型接法下 ADC **下降**表示变热，因此 `SKIN_WARMING_DIRECTION = -1`；换接法时必须重新确认这个符号。

## 软件分层

| 层 | 设备端（C++） | 主机端（Python 镜像） |
|---|---|---|
| 参数 | `params.h` | `flexrest_params.py` |
| 安全检查 | `checkSample()` | `check_sample()` |
| 窗口 + 个人基线 | `FeatureWindow` | `FeatureWindow` |
| Heat Score / Confidence | `scoreFeatures()` | `score()` |
| 分级状态机 | `StateMachine` | `StateMachine` |
| 执行器保护 | `ActuatorGuard` | `ActuatorGuard` |
| 干预响应 | `ResponseTracker` | `ResponseTracker` |
| 硬件 I/O、串口命令 | `main.cpp` | `capture_serial.py` |
| 日志解析 / 回放 / 画图 | — | `parse_log.py`, `simulate.py`, `plot_night.py` |

`thermal_core.cpp` 不依赖 Arduino，可以在电脑上编译（`host_replay.cpp`）。`test_firmware_parity.py` 把同一份 CSV 分别喂给 C++ 和 Python，要求每一秒的状态、动作、PWM、证据数完全一致。

## 串口日志格式

每秒一行 CSV，`#` 开头是注释，重启后会重新打印表头：

```text
t_s,skin_raw,env_temp,humidity,baseline_ready,skin_baseline,heat_score,trend_score,confidence,evidence,state,action,reason,pwm_req,pwm_out,guard,response
```

## 原型边界

- `skin_raw` 是 ADC 原始趋势，没有换算成 °C，也没有标定。
- DHT11 精度有限（±2 °C / ±5 %RH 量级），只用来看床内湿热的变化方向。
- 没有占床检测、身份识别；默认床上就是这一位使用者。
- 阈值和权重是工程初值，需要更多真实夜晚数据来调整；它们不是生理学常数。
- 回放是开环的：记录的数据没有被模拟输出影响，回放只展示“控制器会怎么决定”。
