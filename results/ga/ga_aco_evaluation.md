# GA tuning, target `aco`: defaults vs tuned on held-out seeds

Seeds: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]. Fitness is lower-is-better (same formula as during tuning).
p-value: paired Wilcoxon signed-rank test over seeds (needs at least 5 seeds).

| Condition | Default fitness | Tuned fitness | Improvement | Seeds tuned better | p-value | Gain holds |
|---|---|---|---|---|---|---|
| pen 0.5, loss 0.1, range 300.0 (tuning) | 163.429 ± 14.286 | 118.842 ± 3.205 | 27.28% | 10/10 | 0.002 | yes |
| pen 0.8, loss 0.3, range 300.0 | 198.852 ± 18.183 | 119.536 ± 6.694 | 39.89% | 10/10 | 0.002 | yes |
