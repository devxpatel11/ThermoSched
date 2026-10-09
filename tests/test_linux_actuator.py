from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time

import psutil
import pytest

from thermosched.scheduler.actuator import ActuationError, LinuxActuator, SafetyViolation


pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux guest required")


@pytest.fixture
def child_process() -> subprocess.Popen[str]:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
    )
    try:
        yield child
    finally:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGCONT)
            child.terminate()
            child.wait(timeout=5)


def test_live_child_affinity_set_readback_and_restore(
    child_process: subprocess.Popen[str],
) -> None:
    actuator = LinuxActuator()
    target = actuator.register_child(child_process.pid)
    original = target.original_affinity
    if len(original) < 2:
        pytest.skip("live migration requires at least two eligible guest CPUs")

    first, second = original[:2]
    assert actuator.set_affinity(target, (first,)) == (first,)
    assert actuator.set_affinity(target, (second,)) == (second,)

    actuator.restore(target)
    assert actuator.get_affinity(target) == original


def test_live_child_pacing_is_bounded_and_resumes(
    child_process: subprocess.Popen[str],
) -> None:
    actuator = LinuxActuator()
    target = actuator.register_child(child_process.pid)

    elapsed = actuator.pace(target, 40, 100)

    assert 0.04 <= elapsed < 0.5
    assert psutil.Process(child_process.pid).status() != psutil.STATUS_STOPPED


def test_pacing_rejects_duration_above_configured_bound(
    child_process: subprocess.Popen[str],
) -> None:
    actuator = LinuxActuator()
    target = actuator.register_child(child_process.pid)

    with pytest.raises(ActuationError, match="exceeds configured maximum"):
        actuator.pace(target, 101, 100)


def test_actuator_rejects_controller_parent_and_non_child(
    child_process: subprocess.Popen[str],
) -> None:
    actuator = LinuxActuator()

    with pytest.raises(SafetyViolation, match="protected"):
        actuator.register_child(os.getpid())
    with pytest.raises(SafetyViolation, match="protected"):
        actuator.register_child(os.getppid())


def test_out_of_mask_affinity_is_rejected_without_change(
    child_process: subprocess.Popen[str],
) -> None:
    actuator = LinuxActuator()
    target = actuator.register_child(child_process.pid)
    invalid_cpu = max(target.original_affinity) + 1000

    with pytest.raises(SafetyViolation, match="outside the eligible guest mask"):
        actuator.set_affinity(target, (invalid_cpu,))

    assert actuator.get_affinity(target) == target.original_affinity


def test_context_restores_after_exception(child_process: subprocess.Popen[str]) -> None:
    actuator = LinuxActuator()
    target = actuator.register_child(child_process.pid)

    with pytest.raises(RuntimeError, match="test failure"):
        with actuator:
            actuator.set_affinity(target, (target.original_affinity[0],))
            raise RuntimeError("test failure")

    assert tuple(sorted(psutil.Process(child_process.pid).cpu_affinity())) == target.original_affinity
    assert psutil.Process(child_process.pid).status() != psutil.STATUS_STOPPED


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_guard_restores_and_resumes_child(signum: signal.Signals) -> None:
    helper_code = textwrap.dedent(
        """
        import json
        import signal
        import subprocess
        import sys
        from thermosched.scheduler.actuator import LinuxActuator

        workload = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        actuator = LinuxActuator()
        target = actuator.register_child(workload.pid)
        with actuator:
            actuator.set_affinity(target, (target.original_affinity[0],))
            print(json.dumps({'pid': workload.pid, 'original': target.original_affinity}), flush=True)
            signal.pause()
        """
    )
    helper = subprocess.Popen(
        [sys.executable, "-c", helper_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert helper.stdout is not None
    line = helper.stdout.readline()
    assert line, helper.stderr.read() if helper.stderr else "helper produced no output"
    details = json.loads(line)
    workload = psutil.Process(details["pid"])
    try:
        os.kill(helper.pid, signum)
        helper.wait(timeout=5)
        time.sleep(0.05)

        assert tuple(sorted(workload.cpu_affinity())) == tuple(details["original"])
        assert workload.status() != psutil.STATUS_STOPPED
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=5)
        if workload.is_running():
            workload.send_signal(signal.SIGCONT)
            workload.terminate()
            workload.wait(timeout=5)
