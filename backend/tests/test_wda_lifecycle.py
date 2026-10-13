"""The restart workaround must activate the existing microphone process only."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from wda_session import activate_existing

class ForegroundContinuity(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_existing_pid_and_never_terminates(self):
        p = AsyncMock()
        p.process_identifier_for_bundle_identifier.return_value = 1403
        p.launch.return_value = 1403
        self.assertEqual(await activate_existing(p, "test.app", 1403), 1403)
        p.launch.assert_awaited_once_with("test.app", kill_existing=False)

    async def test_missing_or_changed_process_is_not_launched(self):
        for current, expected in ((0,1403),(1404,1403),(0,0)):
            p=AsyncMock()
            p.process_identifier_for_bundle_identifier.return_value=current
            with self.assertRaises(RuntimeError):
                await activate_existing(p,"test.app",expected)
            p.launch.assert_not_awaited()

    async def test_unexpected_new_pid_is_not_continuity_success(self):
        p=AsyncMock()
        p.process_identifier_for_bundle_identifier.return_value=1403
        p.launch.return_value=1404
        with self.assertRaisesRegex(RuntimeError,"continuity not proved"):
            await activate_existing(p,"test.app",1403)
