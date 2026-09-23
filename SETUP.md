# Setup (from Lab 1)

Do this once, then go back to the [README](README.md).
Install [VS Code](https://code.visualstudio.com/Download) with the **Python** extension (or use your own IDE).

Run these in the folder where you want to keep your lab work.

## macOS

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install libusb
python3 -m venv cf-env
source cf-env/bin/activate
pip install --upgrade pip
pip install cflib cfclient
```

## Windows (PowerShell)

```powershell
py -m venv cf-env
.\cf-env\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install cflib cfclient
```

- "running scripts is disabled": run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, reopen the terminal, and activate again.
- Using cmd instead of PowerShell: activate with `cf-env\Scripts\activate`.

## Linux

```bash
sudo apt install python3 python3-pip python3-venv
python3 -m venv cf-env
source cf-env/bin/activate
pip install cflib cfclient
```

Allow USB access to the Crazyradio and Crazyflie:

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

Log out and back in, then plug in the Crazyradio.

## Every new terminal

Activate the environment again:

- macOS / Linux: `source cf-env/bin/activate`
- Windows: `.\cf-env\Scripts\Activate.ps1`
