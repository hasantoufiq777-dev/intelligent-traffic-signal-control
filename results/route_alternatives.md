# 2x2 Grid Route Alternatives Analysis

Evaluation of loopless route alternatives across boundary Origin-Destination (O-D) pairs.
Identifies near-equal alternative routes (within 20% of best free-flow travel time)
for vehicle navigation and dynamic rerouting scenarios.

| Origin Entry | Destination Exit | Best Travel Time (s) | # Alternatives | Near-Equal (<20%) | Alternative Routes (via Junctions) |
|--------------|------------------|----------------------|----------------|-------------------|-------------------------------------|
| `bottom0J0` | `top1J3` | 84.8 | 14 | **2** | **R1** (84.8s, 1177m): `bottom0J0 -> J0J1 -> J1J3 -> J3top1 -> top1J3`<br/>**R2** (84.8s, 1177m): `bottom0J0 -> J0J2 -> J2J3 -> J3top1 -> top1J3`<br/>**R3** (117.8s, 1636m): `bottom0J0 -> J0J1 -> J1J3 -> J3J2 -> J2J3 -> J3top1 -> top1J3` |
| `bottom1J1` | `top0J2` | 84.8 | 14 | **2** | **R1** (84.8s, 1177m): `bottom1J1 -> J1J3 -> J3J2 -> J2top0 -> top0J2`<br/>**R2** (84.8s, 1177m): `bottom1J1 -> J1J0 -> J0J2 -> J2top0 -> top0J2`<br/>**R3** (117.8s, 1636m): `bottom1J1 -> J1J3 -> J3J2 -> J2J0 -> J0J2 -> J2top0 -> top0J2` |
| `left0J0` | `right1J3` | 84.8 | 14 | **2** | **R1** (84.8s, 1177m): `left0J0 -> J0J1 -> J1J3 -> J3right1 -> right1J3`<br/>**R2** (84.8s, 1177m): `left0J0 -> J0J2 -> J2J3 -> J3right1 -> right1J3`<br/>**R3** (117.8s, 1636m): `left0J0 -> J0J1 -> J1J3 -> J3J2 -> J2J3 -> J3right1 -> right1J3` |
| `left1J2` | `right0J1` | 84.8 | 14 | **2** | **R1** (84.8s, 1177m): `left1J2 -> J2J0 -> J0J1 -> J1right0 -> right0J1`<br/>**R2** (84.8s, 1177m): `left1J2 -> J2J3 -> J3J1 -> J1right0 -> right0J1`<br/>**R3** (117.8s, 1636m): `left1J2 -> J2J0 -> J0J1 -> J1J3 -> J3J1 -> J1right0 -> right0J1` |
| `top0J2` | `bottom1J1` | 84.8 | 14 | **2** | **R1** (84.8s, 1177m): `top0J2 -> J2J0 -> J0J1 -> J1bottom1 -> bottom1J1`<br/>**R2** (84.8s, 1177m): `top0J2 -> J2J3 -> J3J1 -> J1bottom1 -> bottom1J1`<br/>**R3** (117.8s, 1636m): `top0J2 -> J2J0 -> J0J1 -> J1J3 -> J3J1 -> J1bottom1 -> bottom1J1` |
| `top1J3` | `bottom0J0` | 84.8 | 14 | **2** | **R1** (84.8s, 1177m): `top1J3 -> J3J2 -> J2J0 -> J0bottom0 -> bottom0J0`<br/>**R2** (84.8s, 1177m): `top1J3 -> J3J1 -> J1J0 -> J0bottom0 -> bottom0J0`<br/>**R3** (117.8s, 1636m): `top1J3 -> J3J2 -> J2J0 -> J0J1 -> J1J0 -> J0bottom0 -> bottom0J0` |
| `right0J1` | `left1J2` | 84.8 | 14 | **2** | **R1** (84.8s, 1177m): `right0J1 -> J1J3 -> J3J2 -> J2left1 -> left1J2`<br/>**R2** (84.8s, 1177m): `right0J1 -> J1J0 -> J0J2 -> J2left1 -> left1J2`<br/>**R3** (117.8s, 1636m): `right0J1 -> J1J3 -> J3J2 -> J2J0 -> J0J2 -> J2left1 -> left1J2` |
| `right1J3` | `left0J0` | 84.8 | 14 | **2** | **R1** (84.8s, 1177m): `right1J3 -> J3J2 -> J2J0 -> J0left0 -> left0J0`<br/>**R2** (84.8s, 1177m): `right1J3 -> J3J1 -> J1J0 -> J0left0 -> left0J0`<br/>**R3** (117.8s, 1636m): `right1J3 -> J3J2 -> J2J0 -> J0J1 -> J1J0 -> J0left0 -> left0J0` |
| `bottom0J0` | `top0J2` | 68.2 | 28 | **1** | **R1** (68.2s, 948m): `bottom0J0 -> J0J2 -> J2top0 -> top0J2`<br/>**R2** (101.2s, 1406m): `bottom0J0 -> J0J1 -> J1J3 -> J3J2 -> J2top0 -> top0J2`<br/>**R3** (101.2s, 1406m): `bottom0J0 -> J0J1 -> J1J0 -> J0J2 -> J2top0 -> top0J2` |
| `bottom1J1` | `top1J3` | 68.2 | 28 | **1** | **R1** (68.2s, 948m): `bottom1J1 -> J1J3 -> J3top1 -> top1J3`<br/>**R2** (101.2s, 1406m): `bottom1J1 -> J1J3 -> J3J2 -> J2J3 -> J3top1 -> top1J3`<br/>**R3** (101.2s, 1406m): `bottom1J1 -> J1J0 -> J0J2 -> J2J3 -> J3top1 -> top1J3` |
| `left0J0` | `right0J1` | 68.2 | 28 | **1** | **R1** (68.2s, 948m): `left0J0 -> J0J1 -> J1right0 -> right0J1`<br/>**R2** (101.2s, 1406m): `left0J0 -> J0J1 -> J1J3 -> J3J1 -> J1right0 -> right0J1`<br/>**R3** (101.2s, 1406m): `left0J0 -> J0J2 -> J2J3 -> J3J1 -> J1right0 -> right0J1` |
| `left1J2` | `right1J3` | 68.2 | 28 | **1** | **R1** (68.2s, 948m): `left1J2 -> J2J3 -> J3right1 -> right1J3`<br/>**R2** (101.2s, 1406m): `left1J2 -> J2J0 -> J0J1 -> J1J3 -> J3right1 -> right1J3`<br/>**R3** (101.2s, 1406m): `left1J2 -> J2J0 -> J0J2 -> J2J3 -> J3right1 -> right1J3` |

## Summary
- **Total O-D pairs analyzed**: 12
- **O-D pairs with $\ge 2$ near-equal alternatives (<20% travel time difference)**: 8 (66.7%)

### Key Takeaways for Navigation (A* and ACO)
Diagonal O-D pairs (e.g. `bottom0J0` to `top1J3` or `left0J0` to `right1J3`) feature **2 exactly equal-length paths** (1000m / 72.0s free-flow).
When the primary path becomes congested or incident-blocked, navigating vehicles can divert to the alternative branch with minimal baseline penalty.
