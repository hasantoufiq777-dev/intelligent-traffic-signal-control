# GA tuning, target `aco`: defaults vs tuned on held-out seeds

Seeds: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]. Fitness is lower-is-better (same formula as during tuning).
p-value: paired Wilcoxon signed-rank test over seeds (needs at least 5 seeds).

| Condition | Default fitness | Tuned fitness | Improvement | Seeds tuned better | p-value | Gain holds |
|---|---|---|---|---|---|---|
| pen 0.5, loss 0.1, range 300.0 (tuning) | 182.356 ± 18.675 | 119.187 ± 5.997 | 34.64% | 10/10 | 0.002 | yes |
| pen 0.8, loss 0.3, range 300.0 | 230.485 ± 26.246 | 128.111 ± 8.204 | 44.42% | 10/10 | 0.002 | yes |
