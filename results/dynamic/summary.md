# Fixed-time vs dynamic signal control

Network grid2x2, seeds [1, 2, 3, 4, 5], 1000 s per run, penetration 0.5 (VANET sensing). Mean ± standard deviation over the seeds; the change is against fixed-time on the same traffic.

## Scenario `normal`

| Metric | Fixed-time (30 s greens) | Adaptive (queue threshold, existing) | Dynamic congestion-aware | Dynamic congestion-aware, VANET sensing |
|---|---|---|---|---|
| Average waiting time per vehicle (s) | 19.56 ± 1.15 | 12.16 ± 0.90 (-37.8 %) | 6.67 ± 0.37 (-65.9 %) | 12.92 ± 0.86 (-34.0 %) |
| Average queue per junction (vehicles) | 1.65 ± 0.22 | 1.02 ± 0.13 (-38.1 %) | 0.55 ± 0.08 (-66.5 %) | 1.08 ± 0.11 (-34.5 %) |
| Vehicles that completed their trip | 313.80 ± 27.96 | 315.20 ± 27.24 (+0.4 %) | 319.80 ± 29.12 (+1.9 %) | 316.80 ± 29.24 (+1.0 %) |
| Average travel time (s) | 100.31 ± 1.18 | 93.00 ± 1.79 (-7.3 %) | 86.11 ± 1.10 (-14.2 %) | 92.87 ± 1.13 (-7.4 %) |
| Total congestion: vehicle-hours queued at signals | 1.84 ± 0.24 | 1.14 ± 0.15 (-38.3 %) | 0.62 ± 0.09 (-66.5 %) | 1.20 ± 0.12 (-34.6 %) |
| Stops per vehicle | 1.45 ± 0.05 | 1.55 ± 0.10 (+7.4 %) | 1.25 ± 0.08 (-14.0 %) | 1.41 ± 0.11 (-2.6 %) |
| Throughput (vehicles per hour) | 1129.68 ± 100.65 | 1134.72 ± 98.08 (+0.4 %) | 1151.28 ± 104.85 (+1.9 %) | 1140.48 ± 105.28 (+1.0 %) |
| Average time loss (s) | 34.57 ± 1.38 | 27.29 ± 1.55 (-21.1 %) | 20.30 ± 0.92 (-41.3 %) | 27.15 ± 1.17 (-21.5 %) |
| Longest queue on one approach (vehicles) | 8.40 ± 0.89 | 6.80 ± 1.30 (-19.0 %) | 4.80 ± 1.30 (-42.9 %) | 7.40 ± 1.67 (-11.9 %) |
| Collisions | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 |

## Scenario `congested`

| Metric | Fixed-time (30 s greens) | Adaptive (queue threshold, existing) | Dynamic congestion-aware | Dynamic congestion-aware, VANET sensing |
|---|---|---|---|---|
| Average waiting time per vehicle (s) | 38.31 ± 7.79 | 26.39 ± 3.65 (-31.1 %) | 11.12 ± 0.97 (-71.0 %) | 17.35 ± 1.86 (-54.7 %) |
| Average queue per junction (vehicles) | 9.04 ± 3.83 | 4.63 ± 0.87 (-48.8 %) | 1.92 ± 0.20 (-78.7 %) | 3.03 ± 0.44 (-66.5 %) |
| Vehicles that completed their trip | 586.00 ± 36.57 | 634.60 ± 22.94 (+8.3 %) | 662.60 ± 34.83 (+13.1 %) | 654.00 ± 30.78 (+11.6 %) |
| Average travel time (s) | 131.49 ± 11.45 | 123.44 ± 8.47 (-6.1 %) | 94.75 ± 1.71 (-27.9 %) | 105.15 ± 3.85 (-20.0 %) |
| Total congestion: vehicle-hours queued at signals | 10.04 ± 4.25 | 5.14 ± 0.96 (-48.8 %) | 2.14 ± 0.22 (-78.7 %) | 3.36 ± 0.49 (-66.5 %) |
| Stops per vehicle | 2.38 ± 0.48 | 2.89 ± 0.36 (+21.7 %) | 1.43 ± 0.04 (-39.6 %) | 2.03 ± 0.12 (-14.4 %) |
| Throughput (vehicles per hour) | 2109.60 ± 131.66 | 2284.56 ± 82.59 (+8.3 %) | 2385.36 ± 125.40 (+13.1 %) | 2354.40 ± 110.81 (+11.6 %) |
| Average time loss (s) | 66.47 ± 11.70 | 57.78 ± 8.34 (-13.1 %) | 28.81 ± 1.55 (-56.7 %) | 39.29 ± 3.67 (-40.9 %) |
| Longest queue on one approach (vehicles) | 38.40 ± 11.80 | 17.80 ± 1.64 (-53.6 %) | 10.40 ± 1.52 (-72.9 %) | 13.20 ± 2.17 (-65.6 %) |
| Collisions | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 |
