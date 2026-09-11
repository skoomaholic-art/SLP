import unittest

from services.event_filter import is_user_event


class EventFilterTests(unittest.TestCase):
    def test_studio_live_is_not_a_sport_broadcast(self):
        self.assertFalse(is_user_event({"title": "Студия Live"}))

    def test_mixed_latin_c_studio_is_rejected(self):
        self.assertFalse(is_user_event({"title": "Cтудия Livе"}))

    def test_real_match_is_kept(self):
        self.assertTrue(is_user_event({"title": "Футбол. Кайрат - Астана"}))


if __name__ == "__main__":
    unittest.main()
