"""Run with SCHEDULER_TEST_DB=1 against an isolated PostgreSQL container."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.app.scheduler.store import SchedulerError, SchedulerStore


@unittest.skipUnless(os.getenv("SCHEDULER_TEST_DB") == "1", "needs isolated test DB")
class SchedulerStoreIntegrationTests(unittest.TestCase):
    def test_concurrent_reservations_and_persistence(self):
        store = SchedulerStore()
        store.set_config("round_robin", ["pve1", "pve2", "pve3"])
        with ThreadPoolExecutor(max_workers=3) as executor:
            choices = list(executor.map(
                lambda _: store.reserve_node(["pve3", "pve1", "pve2"])["node"],
                range(3),
            ))
        self.assertEqual(set(choices), {"pve1", "pve2", "pve3"})
        with self.assertRaises(SchedulerError):
            store.reserve_node(["pve4"])
        self.assertEqual(SchedulerStore().reserve_node(["pve1", "pve2", "pve3"])["node"], "pve1")
        self.assertEqual(SchedulerStore().get_config()["last_node"], "pve1")


if __name__ == "__main__":
    unittest.main()
