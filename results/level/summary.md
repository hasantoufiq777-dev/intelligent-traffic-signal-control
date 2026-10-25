# Fixed-time vs dynamic signal control

Network grid2x2, seeds [1, 2, 3, 4, 5], 1000 s per run, penetration 0.5 (VANET sensing). Mean ± standard deviation over the seeds; the change is against fixed-time on the same traffic.

## Scenario `normal`

| Metric | Fixed-time (30 s greens) | Dynamic congestion-aware, VANET sensing | Queue levelling, VANET sensing | Queue levelling |
|---|---|---|---|---|
| Average waiting time per vehicle (s) | 19.56 ± 1.15 | 12.92 ± 0.86 (-34.0 %) | 14.22 ± 1.84 (-27.3 %) | 6.94 ± 1.43 (-64.5 %) |
| Average queue per junction (vehicles) | 1.65 ± 0.22 | 1.08 ± 0.11 (-34.5 %) | 1.21 ± 0.27 (-26.7 %) | 0.60 ± 0.18 (-63.6 %) |
| Vehicles that completed their trip | 313.80 ± 27.96 | 316.80 ± 29.24 (+1.0 %) | 317.00 ± 27.68 (+1.0 %) | 317.40 ± 28.16 (+1.1 %) |
| Average travel time (s) | 100.31 ± 1.18 | 92.87 ± 1.13 (-7.4 %) | 95.12 ± 2.60 (-5.2 %) | 85.13 ± 1.97 (-15.1 %) |
| Total congestion: vehicle-hours queued at signals | 1.84 ± 0.24 | 1.20 ± 0.12 (-34.6 %) | 1.35 ± 0.30 (-26.6 %) | 0.66 ± 0.20 (-63.9 %) |
| Stops per vehicle | 1.45 ± 0.05 | 1.41 ± 0.11 (-2.6 %) | 1.60 ± 0.10 (+10.5 %) | 1.05 ± 0.13 (-27.4 %) |
| Throughput (vehicles per hour) | 1129.68 ± 100.65 | 1140.48 ± 105.28 (+1.0 %) | 1141.20 ± 99.64 (+1.0 %) | 1142.64 ± 101.36 (+1.1 %) |
| Average time loss (s) | 34.57 ± 1.38 | 27.15 ± 1.17 (-21.5 %) | 29.41 ± 2.63 (-14.9 %) | 19.33 ± 2.13 (-44.1 %) |
| Longest queue on one approach (vehicles) | 8.40 ± 0.89 | 7.40 ± 1.67 (-11.9 %) | 6.20 ± 1.64 (-26.2 %) | 6.00 ± 1.23 (-28.6 %) |
| Collisions | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 |

## Scenario `congested`

| Metric | Fixed-time (30 s greens) | Dynamic congestion-aware, VANET sensing | Queue levelling, VANET sensing | Queue levelling |
|---|---|---|---|---|
| Average waiting time per vehicle (s) | 38.31 ± 7.79 | 17.35 ± 1.86 (-54.7 %) | 33.85 ± 4.14 (-11.6 %) | 26.47 ± 4.59 (-30.9 %) |
| Average queue per junction (vehicles) | 9.04 ± 3.83 | 3.03 ± 0.44 (-66.5 %) | 5.95 ± 1.02 (-34.2 %) | 4.66 ± 1.09 (-48.4 %) |
| Vehicles that completed their trip | 586.00 ± 36.57 | 654.00 ± 30.78 (+11.6 %) | 632.80 ± 25.19 (+8.0 %) | 647.40 ± 27.16 (+10.5 %) |
| Average travel time (s) | 131.49 ± 11.45 | 105.15 ± 3.85 (-20.0 %) | 128.45 ± 7.61 (-2.3 %) | 116.70 ± 7.60 (-11.2 %) |
| Total congestion: vehicle-hours queued at signals | 10.04 ± 4.25 | 3.36 ± 0.49 (-66.5 %) | 6.61 ± 1.13 (-34.2 %) | 5.18 ± 1.21 (-48.4 %) |
| Stops per vehicle | 2.38 ± 0.48 | 2.03 ± 0.12 (-14.4 %) | 2.95 ± 0.33 (+24.3 %) | 2.34 ± 0.29 (-1.4 %) |
| Throughput (vehicles per hour) | 2109.60 ± 131.66 | 2354.40 ± 110.81 (+11.6 %) | 2278.08 ± 90.70 (+8.0 %) | 2330.64 ± 97.78 (+10.5 %) |
| Average time loss (s) | 66.47 ± 11.70 | 39.29 ± 3.67 (-40.9 %) | 62.67 ± 7.57 (-5.7 %) | 50.90 ± 7.38 (-23.4 %) |
| Longest queue on one approach (vehicles) | 38.40 ± 11.80 | 13.20 ± 2.17 (-65.6 %) | 16.60 ± 1.14 (-56.8 %) | 16.60 ± 1.52 (-56.8 %) |
| Collisions | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 |
