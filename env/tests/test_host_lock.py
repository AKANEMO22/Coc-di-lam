import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
from pipeline import host_lock


@unittest.skipUnless(os.name == "posix", "Production lock uses Linux flock")
class HostLockTests(unittest.TestCase):
    def test_second_process_waits_for_host_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "deploy.lock"
            marker = Path(directory) / "entered"
            code = (
                "import sys; from pathlib import Path; from pipeline import host_lock; "
                "\nwith host_lock(Path(sys.argv[1])): Path(sys.argv[2]).touch()"
            )
            environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "ci"))
            with host_lock(lock):
                child = subprocess.Popen(
                    [sys.executable, "-c", code, str(lock), str(marker)],
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                self.addCleanup(lambda: child.kill() if child.poll() is None else None)
                self.assertIn("Waiting for host-wide", child.stdout.readline())
                self.assertIsNone(child.poll())
                self.assertFalse(marker.exists())
            output, errors = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, errors)
            self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
