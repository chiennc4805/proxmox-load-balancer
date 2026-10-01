"""Run with SCHEDULER_TEST_DB=1 against an isolated PostgreSQL container."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.app.jobs.store import JobStore
from backend.app.scheduler.scheduler import Scheduler


@unittest.skipUnless(os.getenv("SCHEDULER_TEST_DB") == "1", "needs isolated test DB")
class JobStorePlacementIntegrationTests(unittest.TestCase):
    def test_concurrent_round_robin_reservations_use_job_state_cursor(self):
        store = JobStore()
        store.set_config("round_robin")

        def reserve(_):
            job_id = store.create_job("vm.clone", {"template_vmid": 9000})

            def choose(last_node):
                decision = Scheduler("round_robin").select_node(
                    ["pve3", "pve1", "pve2"], last_node
                )
                return {"node": decision.node, "attempted_nodes": [decision.node]}

            return store.reserve_placement(job_id, "vm.clone", "round_robin", choose)["node"]

        with ThreadPoolExecutor(max_workers=3) as executor:
            choices = list(executor.map(reserve, range(3)))

        self.assertEqual(set(choices), {"pve1", "pve2", "pve3"})
        self.assertIn(store.get_config()["last_node"], {"pve1", "pve2", "pve3"})


if __name__ == "__main__":
    unittest.main()