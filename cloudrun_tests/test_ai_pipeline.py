"""No network: rules-first classification and strict optional AI boundaries."""
import unittest
from unittest.mock import patch

from services import ai_pipeline as ai


class AIMailTests(unittest.TestCase):
    def test_known_supplier_does_not_invoke_model(self):
        def forbidden(*args, **kwargs):
            self.fail("Model should not be invoked for a known EPG")
        result = ai.classify_mail(
            "Setanta Sports 1: новая сетка", "supplier@example.test",
            "Расписание на следующую неделю",
            ["EPG Setanta Sports 1 Kazakhstan.xlsx"], fallback=forbidden
        )
        self.assertEqual(result.category, "SCHEDULE_NEW")
        self.assertEqual(result.method, "rules")
        self.assertFalse(result.requires_review)

    def test_correction_is_only_classification_not_cancellation(self):
        result = ai.classify_mail(
            "Перенос эфира SPORT+", "", "Изменение времени игры", []
        )
        self.assertEqual(result.category, "SCHEDULE_CORRECTION")
        self.assertTrue(result.requires_review)
        cancelled = ai.classify_mail(
            "Отмена трансляции SPORT+", "", "Отмена матча", []
        )
        self.assertEqual(cancelled.category, "SCHEDULE_CANCELLATION")
        self.assertTrue(cancelled.requires_review)

    def test_an_opaque_sheet_is_not_automatically_promoted(self):
        result = ai.classify_mail(
            "Fw: приложение", "colleague@example.test", "",
            ["сетка Канала.xlsx"]
        )
        self.assertEqual(result.category, "AMBIGUOUS")
        self.assertTrue(result.requires_review)

    def test_model_is_used_only_on_unresolved_provider_mail(self):
        invoked = []
        def local(kind, instructions, payload):
            invoked.append((kind, payload["subject"]))
            return {"category": "SCHEDULE_UPDATE",
                    "cancel_all": True, "time": "20:00"}
        decision = ai.classify_mail(
            "Setanta", "known@example.test", "Расшифруй вложение", [],
            fallback=local
        )
        self.assertEqual(invoked, [("AI_MAIL", "Setanta")])
        self.assertEqual(decision.category, "SCHEDULE_UPDATE")
        self.assertTrue(decision.requires_review)

    def test_local_inference_cannot_send_mail_to_public_api(self):
        with patch.dict("os.environ", {
            "SPORT_AI_LOCAL_ENABLED": "true",
            "SPORT_AI_MAIL_URL": "https://api.provider.example/api/generate",
            "SPORT_AI_MAIL_MODEL": "free-model",
        }), patch.object(ai, "urlopen") as network:
            self.assertIsNone(ai._local_ai("AI_MAIL", "classify", {"text": "private"}))
            network.assert_not_called()

    def test_editor_only_suggests_text_missing_from_event(self):
        existing = {"sport": "Футбол", "title": "Команда А - Команда Б",
                    "channel": "Q LEAGUE", "time": "19:00",
                    "team1_ru": "КОМАНДА А", "team1_kz": "",
                    "team2_ru": "КОМАНДА Б", "team2_kz": "",
                    "subtitle_ru": "Футбол. КПЛ", "subtitle_kz": ""}
        def local(kind, instructions, payload):
            return {"team1_ru": "ИЗМЕНЕНО", "time": "21:00",
                    "is_live": True, "team1_kz": "A КОМАНДАСЫ",
                    "subtitle_kz": "Футбол. ҚПЛ"}
        result = ai.editor_suggestions(existing, fallback=local)
        self.assertEqual(result, {
            "team1_kz": "A КОМАНДАСЫ",
            "subtitle_kz": "Футбол. ҚПЛ",
        })
        self.assertEqual(existing["time"], "19:00")
        self.assertEqual(existing["team1_ru"], "КОМАНДА А")


if __name__ == "__main__":
    unittest.main()
