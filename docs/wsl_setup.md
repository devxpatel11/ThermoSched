# Windows and Ubuntu WSL 2 setup

Run Windows commands in an Administrator PowerShell only where stated. Run all project, Git, Python, and Bash commands inside Ubuntu WSL 2.

## Install or verify Ubuntu

In Administrator PowerShell:

```powershell
wsl --install -d Ubuntu
wsl -l -v
```

Restart if requested. The Ubuntu row must report `VERSION 2`. If an existing distribution reports version 1, use its exact displayed name:

```powershell
wsl --set-version Ubuntu 2
```

Do not reinstall a working distribution. If installation reports a virtualization problem, enable hardware virtualization and follow the Microsoft WSL troubleshooting guide.

## Prepare Ubuntu

In the Ubuntu terminal:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
python3 --version
uname -r
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/devxpatel11/ThermoSched.git
cd ThermoSched
python3 -m venv .venv
source .venv/bin/activate
```

Python must be 3.10 or newer; the team baseline is 3.12. Install `requirements.txt` only after D1-A1 adds and pins the tested project dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Keep the checkout under the WSL Linux filesystem (`~/projects`), not `/mnt/c`, and use Git from Ubuntu. Activate `.venv` in each new terminal.

## Record E0

In PowerShell, record Windows version and the output of `wsl -l -v`. In Ubuntu, record:

```bash
python -c "import platform, sys; print('Python:', sys.version); print('Kernel:', platform.release())"
```

After D1-A1 adds psutil, also record:

```bash
python -c "import psutil; print('psutil:', psutil.__version__); print('Eligible guest CPUs:', psutil.Process().cpu_affinity()); print('Logical guest CPUs:', psutil.cpu_count())"
```

Enter the observed values in `docs/environment_matrix.md`. E0 records facts only; capability support is established later by disposable-child affinity, pause/resume, readback, and restoration probes. Never test those actions on the shell or an unrelated process.

Useful references: [Microsoft WSL installation](https://learn.microsoft.com/windows/wsl/install), [WSL version comparison](https://learn.microsoft.com/windows/wsl/compare-versions), and [WSL troubleshooting](https://learn.microsoft.com/windows/wsl/troubleshooting).
