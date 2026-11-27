import tempfile
import unittest
from pathlib import Path

from config import config
from core.storage import (
    boot, get_membership, consume_ai_request,
    grant_membership, revoke_membership,
)


class MembershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_path = config.database_path
        object.__setattr__(config, "database_path", str(Path(self.temp.name) / "test.sqlite"))
        boot()

    def tearDown(self):
        object.__setattr__(config, "database_path", self.original_path)
        self.temp.cleanup()

    def test_trial_then_free_daily_limit(self):
        item = get_membership(12345)
        self.assertEqual(item["plan"], "trial")
        self.assertEqual(item["trial_remaining"], 10)
        for _ in range(10):
            self.assertTrue(consume_ai_request(12345)[0])
        self.assertEqual(consume_ai_request(12345), (False, "trial_exhausted"))
        self.assertEqual(get_membership(12345)["plan"], "free")
        for _ in range(3):
            self.assertTrue(consume_ai_request(12345)[0])
        self.assertEqual(consume_ai_request(12345), (False, "free_limit"))

    def test_owner_grant_and_revoke(self):
        grant_membership(999, 54321, 30)
        self.assertEqual(get_membership(54321)["plan"], "granted")
        self.assertTrue(consume_ai_request(54321)[0])
        revoke_membership(999, 54321)
        self.assertEqual(get_membership(54321)["plan"], "free")


if __name__ == "__main__":
    unittest.main()
