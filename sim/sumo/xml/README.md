# SUMO XML files

All XML used by the simulation is kept here.

| Folder | Contents | In git |
|---|---|---|
| `networks/` | Road networks: `net.xml` (single junction), `grid2x2.net.xml`, `grid2x2_zone.net.xml`, `grid2x2_city.net.xml` | Yes |
| `networks/source/` | Node, edge, connection and signal definitions of the single junction | Yes |
| `routes/` | Default traffic demand (`routes.rou.xml`) | Yes |
| `gui/` | SUMO-GUI view settings and map decorations | Yes |
| `generated/` | Route files written for each run and removed when it ends normally | No |

Trip outputs (`tripinfo_*.xml`) are written to `results/tripinfo/` and are not in git. Files that remain in
`generated/` or `results/tripinfo/` come from runs that were stopped early and are safe to delete.

The networks are built by `sim/sumo/generate_grid.py`, `generate_zone_net.py` and `generate_city_net.py`.
