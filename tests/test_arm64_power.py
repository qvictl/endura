"""CPU policy and kernel-control regressions on EPP-less Linux systems."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from endura import tweaks


class CpuPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cpu = self.root / "cpu"
        self.cpu.mkdir()
        self.store = self.root / "state" / "cpu_governors.json"
        patcher = mock.patch.object(tweaks, "CPU", str(self.cpu))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(tweaks.CpuPolicy, "STORE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        original_read = tweaks.read

        def isolated_read(path, default=None):
            if Path(path).is_relative_to(self.root):
                return original_read(path, default)
            return default

        patcher = mock.patch.object(tweaks, "read", side_effect=isolated_read)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tweak = tweaks.CpuPolicy()

    def policy(self, index, governor="performance", available="performance schedutil"):
        p = self.cpu / "cpufreq" / f"policy{index}"
        p.mkdir(parents=True)
        for name, value in {"scaling_governor": governor,
                            "scaling_available_governors": available,
                            "scaling_driver": "qcom-cpufreq-hw",
                            "scaling_min_freq": "300000",
                            "scaling_max_freq": "2438400"}.items():
            (p / name).write_text(value)
        return p

    def test_epp_less_policies_apply_and_restore_across_instances(self):
        p0 = self.policy(0)
        p4 = self.policy(4, governor="ondemand")
        self.assertTrue(self.tweak.available())
        self.assertFalse(self.tweak.status()["active"])
        self.tweak.ensure(True)
        self.tweak.ensure(True)
        self.assertTrue(self.tweak.status()["active"])
        self.assertIn("qcom-cpufreq-hw", self.tweak.status()["detail"])
        self.assertEqual(json.loads(self.store.read_text()),
                         {str(p0 / "scaling_governor"): "performance",
                          str(p4 / "scaling_governor"): "ondemand"})
        for p in (p0, p4):
            self.assertEqual((p / "scaling_min_freq").read_text(), "300000")
            self.assertEqual((p / "scaling_max_freq").read_text(), "2438400")
        with mock.patch.object(tweaks, "run") as run:
            tweaks.CpuPolicy().revert()
            run.assert_not_called()
        self.assertEqual((p0 / "scaling_governor").read_text(), "performance")
        self.assertEqual((p4 / "scaling_governor").read_text(), "ondemand")
        self.assertFalse(self.store.exists())

    def test_temporarily_missing_policy_keeps_restore_state(self):
        p = self.policy(0)
        self.tweak.ensure(True)
        governor = p / "scaling_governor"
        governor.unlink()
        self.tweak.revert()
        self.assertEqual(json.loads(self.store.read_text()), {str(governor): "performance"})
        governor.write_text("schedutil")
        self.tweak.revert()
        self.assertEqual(governor.read_text(), "performance")
        self.assertFalse(self.store.exists())

    def test_already_schedutil_is_unchanged(self):
        self.policy(0, governor="schedutil")
        self.tweak.ensure(True)
        self.assertTrue(self.tweak.status()["active"])
        self.assertFalse(self.store.exists())
        self.tweak.revert()

    def test_no_supported_governor_is_unavailable(self):
        p = self.policy(0, available="performance userspace")
        self.assertFalse(self.tweak.available())
        self.assertFalse(self.tweak.status()["active"])
        self.tweak.ensure(True)
        self.assertEqual((p / "scaling_governor").read_text(), "performance")

    def test_ac_does_not_apply_battery_governor(self):
        p = self.policy(0)
        self.tweak.ensure(False)
        self.assertEqual((p / "scaling_governor").read_text(), "performance")
        self.assertFalse(self.store.exists())

    def test_latency_floor_is_reversible_and_keeps_maximum(self):
        p = self.policy(0, governor="schedutil")
        t = tweaks.CpuPolicy({"battery_min_freq_mhz": 2000})
        t.ensure(True)
        t.ensure(True)
        self.assertEqual((p / "scaling_min_freq").read_text(), "2000000")
        self.assertEqual((p / "scaling_max_freq").read_text(), "2438400")
        self.assertTrue(t.status()["active"])
        tweaks.CpuPolicy().revert()
        self.assertEqual((p / "scaling_min_freq").read_text(), "300000")

    def test_clearing_floor_restores_minimum_while_policy_stays_enabled(self):
        p = self.policy(0, governor="schedutil")
        opts = {"battery_min_freq_mhz": 2000}
        t = tweaks.CpuPolicy(opts)
        t.ensure(True)
        opts["battery_min_freq_mhz"] = 0
        t.ensure(True)
        self.assertEqual((p / "scaling_min_freq").read_text(), "300000")
        self.assertEqual((p / "scaling_governor").read_text(), "schedutil")
        self.assertFalse(self.store.exists())

    def test_floor_respects_original_minimum_and_existing_maximum(self):
        p = self.policy(0, governor="schedutil")
        (p / "scaling_min_freq").write_text("2100000")
        t = tweaks.CpuPolicy({"battery_min_freq_mhz": 1000})
        t.ensure(True)
        self.assertEqual((p / "scaling_min_freq").read_text(), "2100000")
        self.assertFalse(self.store.exists())
        t.opts["battery_min_freq_mhz"] = 4000
        t.ensure(True)
        self.assertEqual((p / "scaling_min_freq").read_text(), "2438400")
        self.assertEqual((p / "scaling_max_freq").read_text(), "2438400")
        t.revert()
        self.assertEqual((p / "scaling_min_freq").read_text(), "2100000")

    def test_ondemand_tuning_and_governor_are_both_restored(self):
        p = self.policy(0, governor="schedutil", available="schedutil ondemand")
        threshold = p / "ondemand" / "up_threshold"
        threshold.parent.mkdir()
        threshold.write_text("95")
        opts = {"battery_governor": "ondemand", "battery_min_freq_mhz": 1200}
        t = tweaks.CpuPolicy(opts)
        t.ensure(True)
        self.assertEqual((p / "scaling_governor").read_text(), "ondemand")
        self.assertEqual(threshold.read_text(), "25")
        self.assertTrue(t.status()["active"])
        opts["battery_governor"] = "schedutil"
        t.ensure(True)
        self.assertEqual(threshold.read_text(), "95")
        self.assertEqual((p / "scaling_governor").read_text(), "schedutil")
        t.revert()
        self.assertEqual((p / "scaling_min_freq").read_text(), "300000")
        self.assertEqual(threshold.read_text(), "95")

    def test_epp_hardware_keeps_existing_policy(self):
        p = self.policy(0)
        (p / "energy_performance_preference").write_text("power")
        (p / "cpuinfo_max_freq").write_text("3000000")
        core = self.cpu / "cpu0"
        core.mkdir()
        (core / "cpufreq").symlink_to(p, target_is_directory=True)
        boost = self.cpu / "cpufreq" / "boost"
        boost.write_text("1")
        self.tweak.ensure(True)
        self.assertEqual((p / "energy_performance_preference").read_text(), "balance_performance")
        self.assertEqual((p / "scaling_governor").read_text(), "performance")
        self.assertEqual((p / "scaling_max_freq").read_text(), "3000000")
        self.assertEqual(boost.read_text(), "0")
        self.assertFalse(self.store.exists())
        with mock.patch.object(tweaks, "run") as run:
            self.tweak.revert()
            run.assert_called_once_with(["systemctl", "try-restart", "power-profiles-daemon", "tuned"])


class SysctlsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.object(tweaks.Sysctls, "ROOT", str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tweak = tweaks.Sysctls()

    def control(self, key, value):
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)
        return p

    def test_missing_nmi_does_not_hide_working_writeback_control(self):
        p = self.control("vm/dirty_writeback_centisecs", "500")
        self.assertTrue(self.tweak.available())
        self.assertFalse(self.tweak.status()["active"])
        self.tweak.ensure(True)
        self.assertTrue(self.tweak.status()["active"])
        self.assertEqual(p.read_text(), "1500")
        self.assertNotIn("nmi_watchdog", self.tweak.status()["detail"])
        self.tweak.revert()
        self.assertEqual(p.read_text(), "500")
        self.assertFalse((self.root / "kernel/nmi_watchdog").exists())

    def test_missing_all_controls_is_not_active_or_available(self):
        self.assertFalse(self.tweak.available())
        self.assertFalse(self.tweak.status()["active"])
        self.tweak.ensure(True)
        self.tweak.revert()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_nmi_still_participates_in_status(self):
        p = self.control("kernel/nmi_watchdog", "1")
        self.control("vm/dirty_writeback_centisecs", "1500")
        self.assertFalse(self.tweak.status()["active"])
        self.tweak.ensure(True)
        self.assertTrue(self.tweak.status()["active"])
        self.assertEqual(p.read_text(), "0")
        self.tweak.revert()
        self.assertEqual(p.read_text(), "1")


if __name__ == "__main__":
    unittest.main()
