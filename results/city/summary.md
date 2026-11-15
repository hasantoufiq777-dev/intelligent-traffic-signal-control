# City network results

5 seeds × 1200 s, penetration 50 %, mixed vehicles.

| Configuration | Waiting (s) | Travel time (s) | Throughput (veh/h) | Max queue | Collisions | CO₂ (g/km) | Reroutes |
|---|---|---|---|---|---|---|---|
| fixed | 27.3 | 118.6 | 1216 | 10.8 | 0 | 292 | 0 |
| adaptive | 19.8 | 110.8 | 1231 | 10.0 | 0 | 290 | 0 |
| adaptive_vanet | 22.4 | 113.0 | 1232 | 9.6 | 0 | 289 | 0 |
| adaptive_vanet + A* | 21.5 | 111.7 | 1225 | 9.6 | 0 | 287 | 18 |

Trips per vehicle category using each road class (seed 1):

| Category | Trips | Main | Secondary | Collector | Local |
|---|---|---|---|---|---|
| bus | 10 | 8 | 2 | 2 | 0 |
| car | 276 | 233 | 176 | 60 | 0 |
| eco_car | 59 | 51 | 34 | 13 | 0 |
| motorcycle | 36 | 30 | 21 | 9 | 0 |
| truck | 28 | 21 | 7 | 7 | 0 |
