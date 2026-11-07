import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("quota", Path(__file__).parents[1]/"scripts/nixbench-quota.py")
quota = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quota)


class Quota(unittest.TestCase):
    def test_colored_opencode_limit(self):
        self.assertTrue(quota.quota_exhausted("\x1b[91m\x1b[1mError: \x1b[0mFree usage limit reached. Free models work in OpenCode"))

    def test_http_rate_limit(self):
        self.assertTrue(quota.quota_exhausted("HTTP 429"))

    def test_other_agent_failures_remain_failures(self):
        for text in ["Error: Permission denied: external_directory", "Error: evaluation failed", "Error: HTTP 503", "quota is configured by the test", "all tasks passed"]:
            with self.subTest(text=text):
                self.assertFalse(quota.quota_exhausted(text))


if __name__ == "__main__":
    unittest.main()
