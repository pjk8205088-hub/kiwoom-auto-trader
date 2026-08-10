import unittest

from kiwoom_auto_trader.app_security import (
    hash_secret,
    is_valid_pin,
    is_valid_recovery_password,
    mask_account_except_last_two,
    personalized_message,
    verify_secret,
)


class AppSecurityTests(unittest.TestCase):
    def test_hashes_and_verifies_without_storing_plaintext(self):
        encoded = hash_secret("123456", salt=b"0123456789abcdef")

        self.assertNotIn("123456", encoded)
        self.assertTrue(verify_secret("123456", encoded))
        self.assertFalse(verify_secret("654321", encoded))

    def test_validates_pin_and_recovery_password_policy(self):
        self.assertTrue(is_valid_pin("123456"))
        self.assertFalse(is_valid_pin("12345"))
        self.assertFalse(is_valid_pin("12A456"))
        self.assertTrue(is_valid_pin("111111"))
        self.assertTrue(is_valid_recovery_password("222222"))
        self.assertTrue(is_valid_recovery_password("Kawaii8!"))
        self.assertFalse(is_valid_recovery_password("Kawaii88"))

    def test_masks_every_account_digit_except_last_two(self):
        self.assertEqual(mask_account_except_last_two("6698-6208"), "****-**08")
        self.assertEqual(mask_account_except_last_two("66986208"), "****-**08")

    def test_personalizes_error_message(self):
        self.assertEqual(
            personalized_message("이진솔", "현재 주문 네트워크 연결에 문제가 발생했습니다."),
            "이진솔님, 현재 주문 네트워크 연결에 문제가 발생했습니다.",
        )


if __name__ == "__main__":
    unittest.main()
