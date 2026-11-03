# GA tuning, target `signals`: defaults vs tuned on held-out seeds

Seeds: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]. Fitness is lower-is-better (same formula as during tuning).
p-value: paired Wilcoxon signed-rank test over seeds (needs at least 5 seeds).

| Condition | Default fitness | Tuned fitness | Improvement | Seeds tuned better | p-value | Gain holds |
|---|---|---|---|---|---|---|
| pen 0.5, loss 0.1, range 300.0 (tuning) | 34.144 ± 2.731 | 32.84 ± 2.241 | 3.82% | 6/10 | 0.193 | yes |
| pen 0.8, loss 0.3, range 300.0 | 33.022 ± 1.923 | 32.533 ± 2.811 | 1.48% | 5/10 | 0.557 | yes |
