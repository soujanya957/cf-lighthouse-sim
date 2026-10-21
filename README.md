# Lighthouse viz

Edit, simulate, and fly your Crazyflie code from the browser.

## Setup (once)

Run these in the folder where you want to keep your Crazyflie work. They make a `cf-env` virtual environment with
`cflib` and `cfclient`.

**macOS**
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # skip if you have Homebrew
brew install libusb
python3 -m venv cf-env
source cf-env/bin/activate
pip install --upgrade pip
pip install cflib cfclient
```

**Windows (PowerShell)**
```powershell
py -m venv cf-env
.\cf-env\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install cflib cfclient
```

- "running scripts is disabled": run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, reopen
  the terminal, and activate again.
- Using cmd instead of PowerShell: activate with `cf-env\Scripts\activate`.

**Linux**
```bash
sudo apt install python3 python3-pip python3-venv
python3 -m venv cf-env
source cf-env/bin/activate
pip install cflib cfclient
```

Then allow USB access to the Crazyradio and Crazyflie, log out and back in, and plug in the Crazyradio:

```bash
sudo tee /etc/udev/rules.d/99-bitcraze.rules > /dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="1915", ATTRS{idProduct}=="7777", MODE="0664", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1915", ATTRS{idProduct}=="0101", MODE="0664", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5740", MODE="0664", GROUP="plugdev"
EOF
sudo groupadd -f plugdev
sudo usermod -a -G plugdev $USER
sudo udevadm control --reload-rules
sudo udevadm trigger
```

In every new terminal, activate the environment again: `source cf-env/bin/activate` (macOS / Linux) or
`.\cf-env\Scripts\Activate.ps1` (Windows).

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

## Fly webGUI projects on several drones

[`lighthouse_runner/`](lighthouse_runner/README.md) flies Crazyswarm2 webGUI exports on one or more
Lighthouse-tracked Crazyflies, without ROS or Vicon. It has its own dashboard at http://127.0.0.1:8766:

```bash
source cf-env/bin/activate
pip install -r lighthouse-viz/lighthouse_runner/requirements.txt    # once
python lighthouse-viz/lighthouse_runner/dashboard.py
```

Pick a project, give each robot a Crazyflie ID, then **Simulate → Check drones → Fly**. Calibration, radio addresses
and troubleshooting are in [lighthouse_runner/README.md](lighthouse_runner/README.md).
