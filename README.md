# Intelligent Traffic Signal Control System

A web-based intelligent traffic signal control simulation and monitoring platform.

## Architecture Overview
- **Simulation Core**: SUMO + Python (`traci`/`libsumo`) with three controller strategies:
  - Fixed-time baseline
  - Adaptive (queue / pressure-based)
  - Adaptive + Emergency-Vehicle Preemption
  - Genetic Algorithm (GA) parameter tuner & multi-seed statistical evaluation
- **Web Layer**:
  - Node.js (Express + Socket.io + SQLite) gateway orchestrating simulation processes and caching time-series data
  - React 18 dashboard (Vite + Recharts) rendering live junction SVG, ETA gauges, queue charts, and experiment comparisons

---

## Setup & Prerequisites

### 1. System Requirements
- **Python**: 3.10+
- **Eclipse SUMO**: 1.18+ (tested with SUMO 1.28.0)
- **Node.js**: 18+ (LTS recommended)
- **Git**

### 2. Configure SUMO_HOME
Ensure `SUMO_HOME` is defined in your environment and added to `PATH`:
- **Windows (PowerShell)**:
  ```powershell
  $env:SUMO_HOME = "C:\Program Files (x86)\SUMO"
  $env:PATH += ";$env:SUMO_HOME\bin;$env:SUMO_HOME\tools"
  ```
- **Linux/macOS**:
  ```bash
  export SUMO_HOME=/usr/share/sumo # or /opt/homebrew/opt/sumo/share/sumo
  export PATH=$PATH:$SUMO_HOME/bin:$SUMO_HOME/tools
  ```

Verify with:
```bash
sumo --version
netconvert --version
```

### 3. Python Virtual Environment Setup
```powershell
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -r sim/requirements.txt
```

### 4. Verify Installation (Smoke Test)
```bash
python sim/smoke_test.py
```
Should print `OK` when SUMO and TraCI are communicating correctly.
