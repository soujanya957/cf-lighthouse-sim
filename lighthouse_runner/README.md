# Lighthouse runner

Fly a [Brown-crazyswarm2](https://github.com/USC-ACTLab/Brown-crazyswarm2) webGUI export on Lighthouse-tracked
Crazyflies from a Mac, Windows or Linux laptop, without ROS, Vicon or an Ubuntu VM. One drone or several, from a
dashboard in the browser.

The export is not edited. Its `launch_nodes.py` runs unchanged: stand-ins for `rclpy` and `crazyflie_py` (in `shims/`)
hand it drones that are flown with cflib, and each drone gets its position from its own Lighthouse deck.

| ROS setup | Here |
|---|---|
| `crazyflies.yaml` (IDs → radio URIs) | **Drones** in the dashboard (saved to `fleet.yaml`); it can import `crazyflies.yaml` |
| `python3 configure.py` (asks the drone IDs) | Type the IDs in the dashboard's table |
| `ros2 launch launch.py` (Vicon, server, RViz) | No server; the 3D view shows the plan and the live drones |
| `python3 launch_nodes.py` | **Fly** in the dashboard |

## What you need

- A `cf-env` virtual environment with `cflib` and `cfclient` ([Setup](../README.md#setup-once)).
- A Crazyradio (PA or 2.0).
- Crazyflies with a **Lighthouse deck** (an LED ring deck is optional; without one, LED blocks are skipped).
- Lighthouse V2 base stations, at least 2, set up so every part of the flight area sees 2 or more.

## Install (once)

In the folder where you made `cf-env`, with this repo cloned as `lighthouse-viz`:

```bash
source cf-env/bin/activate                     # Windows: .\cf-env\Scripts\Activate.ps1
pip install -r lighthouse-viz/lighthouse_runner/requirements.txt
```

## Run

```bash
source cf-env/bin/activate
python lighthouse-viz/lighthouse_runner/dashboard.py
```

The dashboard opens at http://127.0.0.1:8766. Close cfclient first: only one program can use the Crazyradio.
Ctrl-C in the terminal stops the dashboard (and lands anything still flying).

## Setup (once per room, and once per new drone)

### 1. Calibrate the base stations

1. In cfclient, connect to one drone and open the **Lighthouse** tab.
2. Put the drone on the floor at the point that should be (0, 0), **facing the direction that should be +x**. The
   webGUI's coordinates (`starting_positions.yaml` and the blocks) are in this frame, so pick the same origin and
   +x as the room's usual frame (for example Vicon's). If +x points the other way, everything flies mirrored.
3. Run **Estimate geometry** and follow the steps.
4. **Save system config** to a file, for example `lighthouse_runner/lighthouse.yaml`. Git ignores
   `lighthouse*.yaml` here, so your calibration stays on your machine.

The geometry and calibration are stored on that drone and survive power cycles.

### 2. Copy the same config to every drone

Every drone must hold the **same** geometry, or they fly in different coordinate frames. Don't estimate per drone;
copy the file instead, either in cfclient (Lighthouse tab → **Load system config**, one drone at a time), or for
several at once (cfclient closed):

```bash
python lighthouse-viz/lighthouse_runner/lh_run.py upload-geometry lighthouse-viz/lighthouse_runner/lighthouse.yaml --ids 12,32,60
```

The preflight's **Same geometry** step fails if two drones differ. Only redo this when a base station moves.

### 3. Tell the dashboard about the drones

The drones live in `lighthouse_runner/fleet.yaml`. It's made from `fleet.example.yaml` the first time the runner
starts, and git ignores it, so your drones' addresses stay on your machine.

Click **Drones** (top right). Each row is a Crazyflie ID and the radio address it answers on:
`radio://<Crazyradio>/<channel>/<data rate>/<address>`.

- **Import crazyflies.yaml…** reads every robot from Crazyswarm2's `crazyflies.yaml` (`crazyflie/config/` in
  [Brown-crazyswarm2](https://github.com/USC-ACTLab/Brown-crazyswarm2)), then **Save**. Crazyswarm's `radio://*/…`
  becomes `radio://0/…` (the first Crazyradio).
- Or **+ Add drone**: type the ID and the address fills in as `E7E7E7E7` + the ID in hex (73 → `E7E7E7E749`).

A drone's channel and address are stored on the drone (cfclient: Connect → Configure 2.x). If one doesn't answer,
check its channel there; the radio frequency is 2400 MHz + channel.

**Put all drones that fly together on the same channel.** One Crazyradio can talk to drones on different channels,
but it then retunes for nearly every packet, and connecting three drones can take over a minute or time out. With a
second Crazyradio, give some drones Radio 1.

### 4. Check `fleet.yaml`

Edit `lighthouse_runner/fleet.yaml` (not the example):

- `fence`: the flight area, inside the base stations' coverage. Targets outside it are clamped to it.
- `safety`: battery, tracking-loss and start-position limits.
- `lighthouse_config`: the saved system config (for example `lighthouse.yaml`), so the dashboard draws the base
  stations before anything connects.
- `params`: firmware parameters set on every drone at connect (for example PID tuning).

## Every flight

1. **Add the project.** Put the webGUI export (the folder with `launch_nodes.py`) in `lighthouse_runner/projects/`,
   press **Refresh**, and pick it from the **Project** list. The export's `configure.py` and `launch.py` are for ROS
   and aren't used.
2. **Give each robot a Crazyflie ID** in the table. Its radio address shows underneath.
3. **1 Simulate.** The 3D view draws each drone's planned path; the summary reports the closest approach and anything
   outside the fence. Play it with ▶ or the space bar.
4. **Place the drones** on their start squares (the colored squares in 3D), facing +x, and switch them on.
5. **2 Check drones.** The dashboard connects, shows each drone live in 3D, and runs the preflight:

   | Step | Checks |
   |---|---|
   | Link | the drone answers on its radio address |
   | Drone | battery and supervisor state |
   | Decks | Lighthouse deck (and LED ring, if the project sets colors) |
   | Base stations | at least 2 seen at once |
   | Estimate | the position estimate has settled |
   | Start position | within `start_tolerance` of its start square (catches swapped IDs) |
   | Same geometry | every drone has the same base-station geometry |

   The **Status** column shows each drone's result; a failure's reason is in red under its row. The **Position**
   column says how far each drone is from its square and which way to move it (a dashed line in 3D points there too).
   Fix things, then **Check again**.
6. **3 Fly** (only after every check passes): confirm, and the show runs. The simulation plays alongside, so the plan
   (dashed) and the real flight (solid trails) can be compared.
7. **Stop (land)** lands every drone. Pressed again while they land, it **cuts the motors**. **Disconnect** frees the
   radio.

The line under the three steps always says what to do next. **▸ Code** shows the project's code.

Each flight saves `flight.csv` in `runs/`: position, attitude, Lighthouse status, which base stations were used and
received (bit masks, 15 = all four), battery and estimate variance, about 20 times a second.

## Command line

The dashboard runs these underneath; they also work on their own:

```bash
cd lighthouse-viz/lighthouse_runner
python lh_run.py dry-run projects/test_two_drones --ids 7,8    # simulate and plot, no radio
python lh_run.py check projects/test_two_drones --ids 7,8      # connect and preflight only
python lh_run.py fly projects/test_two_drones --ids 7,8        # simulate, preflight, confirm, fly
```

`--ids` are in `starting_positions.yaml` order. `fly` asks you to type `fly` before takeoff; Ctrl-C lands every drone,
and a second Ctrl-C cuts the motors.

## Safety while flying

- **Fence:** flight targets outside `fence` are clamped to it. A drone more than `kill_outside_fence` m outside the
  fence for 0.25 s gets its motors cut.
- **Faults land everyone:** Lighthouse tracking lost for `land_on_tracking_loss` s, low battery, an error in the
  project's code, or a lost radio link. A drone whose link is lost keeps its last command, so have someone ready to
  grab it.

## Troubleshooting

- **"no Crazyradio found":** plug it in and close cfclient.
- **A drone doesn't answer (`Too many packets lost`):** it's off, or on another channel or address than in **Drones**.
  Check in cfclient (Connect → Configure 2.x).
- **Check drones sits on "connecting" and fails after 90 s:** the drones are on different channels, or they still
  stream logs from a connection that crashed. Power-cycle them, and put them on the same channel. If nothing answers at
  all, unplug and replug the Crazyradio.
- **"no Lighthouse deck detected":** reseat the deck with the drone off; decks are detected at power-up.
- **"BS n not seen":** something blocks that base station, or the drone is outside its view.
- **The drone flies to the mirrored spot:** the calibration's +x is reversed from the project's frame (see
  [Calibrate the base stations](#1-calibrate-the-base-stations)).
- **The position jumps during flight, then the drone lands with "tracking lost":** usually infrared interference
  (turn Vicon off while flying Lighthouse) or reflections. Holding the drone up in cfclient's Lighthouse tab and
  walking the flight area shows where tracking drops.
- **USB Crazyflie (`usb://0`) not listed:** a charge-only cable, or the drone is switched off. Flashing works over the
  Crazyradio anyway (cfclient: Connect → Bootloader).

## Differences from the ROS setup

- **Show ending:** under ROS the export's worker timer would start the show again once it ends. Here a worker that has
  set `self.done = True` isn't run again, and the flight ends when all workers are done.
- **Streaming hand-back:** after `circle()` (which streams positions), the runner sends `notifySetpointsStop()` before
  the next high-level command, which the drone would otherwise ignore.
- **Errors:** an exception in a group's blocks lands everyone, instead of leaving the drones hovering.
- **Not supported:** uploaded trajectories (`uploadTrajectory`, `generate_trajectory`). No block uses them yet.
