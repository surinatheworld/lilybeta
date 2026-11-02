import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.core import quota

SECRET = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")  # RFC 8032, test 1


class Ed25519Tests(unittest.TestCase):
    def test_matches_rfc_8032_vector(self):
        self.assertEqual(quota.public_key(SECRET).hex(),
                         "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = quota.sign(SECRET, b"")
        self.assertTrue(sig.hex().startswith("e5564300c360ac729086e2cc806e828a"))
        self.assertTrue(quota.verify(quota.public_key(SECRET), b"", sig))
        self.assertFalse(quota.verify(quota.public_key(SECRET), b"x", sig))


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = {}

        def registry(user, value=None):
            if value is None:
                return json.loads(self.registry[user]) if user in self.registry else None
            self.registry[user] = value

        quota.parse_key.cache_clear()
        self.patches = [patch.dict(os.environ, {"APPDATA": self.tmp.name, "LILYZONO_UNLIMITED": "0"}),
                        patch.object(quota, "PUBLIC_KEY", quota.public_key(SECRET).hex()),
                        patch.object(quota, "_registry", registry)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        quota.parse_key.cache_clear()
        self.tmp.cleanup()

    def key(self, tokens, expires=None, user="u-1"):
        return quota.make_key(SECRET, quota.License(user, "An", tokens, expires, "2026-11-02"))

    def test_without_a_key_nothing_runs(self):
        with self.assertRaises(quota.TokenError):
            quota.charge(quota.PAGE_COST)

    def test_tokens_are_spent_until_the_balance_runs_out_and_a_top_up_continues(self):
        quota.install(self.key(25))
        quota.charge(10)
        quota.charge(10)
        self.assertEqual(quota.status()[1], 5)
        with self.assertRaises(quota.TokenError):
            quota.charge(10)
        self.assertEqual(quota.status()[1], 5)  # không đủ thì không trừ
        quota.install(self.key(125))  # nạp thêm: cùng mã người dùng, tổng lớn hơn
        quota.charge(10)
        self.assertEqual(quota.status()[1], 95)

    def test_forged_or_edited_keys_are_rejected(self):
        good = self.key(100)
        body, sig = good[len(quota.PREFIX):].split(".")
        payload = quota._unb64(body).replace(b'"t":100', b'"t":999999')
        with self.assertRaises(quota.TokenError):
            quota.install(quota.PREFIX + quota._b64(payload) + "." + sig)
        other = quota.make_key(b"\x01" * 32, quota.License("u-1", "An", 10 ** 9, None, "2026-11-02"))
        with self.assertRaises(quota.TokenError):
            quota.install(other)

    def test_editing_or_deleting_the_usage_file_does_not_refill_tokens(self):
        quota.install(self.key(30))
        quota.charge(20)
        path = os.path.join(self.tmp.name, "Lilyzono", "usage.json")
        data = json.load(open(path, encoding="utf-8"))
        data["u-1"]["s"] = 0  # sửa tay: mã kiểm tra không khớp
        json.dump(data, open(path, "w", encoding="utf-8"))
        self.assertEqual(quota.status()[1], 10)
        os.remove(path)  # xoá file: bản trong registry vẫn còn
        self.assertEqual(quota.status()[1], 10)

    def test_expired_and_unlimited_keys(self):
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        quota.install(self.key(100, yesterday))
        with self.assertRaises(quota.TokenError):
            quota.charge(1)
        quota.install(self.key(None, user="owner"))
        for _ in range(5):
            quota.charge(quota.PAGE_COST)
        self.assertIn("không giới hạn", quota.describe())


if __name__ == "__main__":
    unittest.main()
