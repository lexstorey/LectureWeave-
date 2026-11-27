import unittest

from credential_store import save_huggingface_token


class CredentialStoreTests(unittest.TestCase):
    def test_rejects_a_value_that_is_not_a_hugging_face_token(self):
        with self.assertRaises(ValueError):
            save_huggingface_token("not-a-token")


if __name__ == "__main__":
    unittest.main()
