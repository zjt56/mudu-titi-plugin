import unittest
from unittest.mock import patch
from workbench import server


class ManualAdvanceResetTest(unittest.TestCase):
    def test_manual_start_advance_resets_supervisor_budget_by_default(self):
        with patch.object(server, 'batch_dir', return_value='/tmp/unused'), \
             patch.object(server, '_launch_advance', return_value={'started': True}) as launch, \
             patch.object(server, 'auto_flow', object()):
            server.start_advance('batch')
        self.assertTrue(launch.call_args.kwargs['reset_budget'])


if __name__ == '__main__':
    unittest.main()
