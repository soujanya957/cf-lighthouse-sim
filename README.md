# Lighthouse viz

Edit, simulate, and fly your Lab 2 Crazyflie code from the browser.

Requires the Lab 1 software setup (a `cf-env` virtual environment with `cflib`).
Not set up yet? Follow [SETUP.md](SETUP.md) first.

## Install and run

In the folder where you made `cf-env`:

**macOS / Linux**
```bash
source cf-env/bin/activate
git clone https://github.com/soujanya957/cf-lighthouse-sim.git lighthouse-viz
python lighthouse-viz/viz.py
```

**Windows (PowerShell)**
```powershell
.\cf-env\Scripts\Activate.ps1
git clone https://github.com/soujanya957/cf-lighthouse-sim.git lighthouse-viz
python lighthouse-viz\viz.py
```

No git? Download the ZIP, unzip it here, and rename the folder to `lighthouse-viz`.

The page opens at http://127.0.0.1:8765. Next time, just activate and run the last line.

## Use

1. Put your radio address from cfclient in the box at the top.
2. Complete the tasks in `flight.py`, then **Save**.
3. **Run simulation** to check the path.
4. Close cfclient, place the drone on the ground, then **Run hardware**. It runs preflight checks, then asks you to confirm **Fly**.
5. **Stop** lands the drone. Press it again to cut the motors.
