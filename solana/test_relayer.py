"""Relayer helpers. No network, no key material."""
import importlib.util
import os
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("solana_relayer", Path(__file__).with_name("relayer.py"))
relayer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relayer)


class RelayerTests(unittest.TestCase):
    def test_chunks_cover_the_payload(self):
        blob = bytes(range(256)) * 3
        out = b""
        last = -1
        for offset, chunk in relayer.chunks(blob, 400):
            self.assertEqual(offset, len(out))
            self.assertLessEqual(len(chunk), 400)
            out += chunk
            last = offset
        self.assertEqual(out, blob)
        self.assertGreater(last, 0)

    def test_instruction_discriminator_is_eight_bytes(self):
        self.assertEqual(len(relayer.disc("submit_vote")), 8)
        self.assertNotEqual(relayer.disc("submit_vote"), relayer.disc("finalize"))

    def test_demo_refuses_without_secrets(self):
        saved = {name: os.environ.pop(name, None) for name in ("NEARAI_API_KEY", "SOLANA_KEYPAIR")}
        try:
            with self.assertRaises(relayer.RelayerError):
                relayer.demo()
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
