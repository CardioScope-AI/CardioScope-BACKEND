import unittest

from pydantic import ValidationError

from app.profile import COLUMNS, PatientProfile


VALID = {
    "age": 63, "sex": 1, "cp": 1, "trestbps": 145, "chol": 233, "fbs": 1,
    "restecg": 2, "thalach": 150, "exang": 0, "oldpeak": 2.3, "slope": 3,
    "ca": 0, "thal": 6,
}


class PatientProfileTests(unittest.TestCase):
    def test_accepts_contract_example_and_rebuilds_column_order(self):
        profile = PatientProfile.model_validate(VALID)

        self.assertEqual(list(profile.ordered_columns()), list(COLUMNS))
        self.assertEqual(profile.ordered_columns()["oldpeak"], 2.3)
        self.assertIsInstance(profile.age, int)
        self.assertIsInstance(profile.thalach, int)

    def test_accepts_any_key_order_and_ignores_extra_fields(self):
        payload = {name: VALID[name] for name in reversed(COLUMNS)}
        payload["num"] = 4
        payload["note"] = "profil fictif"

        profile = PatientProfile.model_validate(payload)

        self.assertEqual(list(profile.ordered_columns()), list(COLUMNS))
        self.assertNotIn("num", profile.model_dump())
        self.assertNotIn("note", profile.model_dump())

    def test_accepts_decimal_measurements_without_business_bounds(self):
        payload = dict(VALID)
        payload.update(age=-1, thalach=0, trestbps=-12.5, chol=0, oldpeak=-2.3)

        profile = PatientProfile.model_validate(payload)

        self.assertEqual(profile.age, -1)
        self.assertEqual(profile.thalach, 0)
        self.assertEqual(profile.trestbps, -12.5)
        self.assertEqual(profile.chol, 0.0)
        self.assertEqual(profile.oldpeak, -2.3)

    def test_rejects_each_missing_field(self):
        for name in COLUMNS:
            payload = dict(VALID)
            del payload[name]
            with self.assertRaises(ValidationError) as caught:
                PatientProfile.model_validate(payload)
            self.assertIn(name, [error["loc"][0] for error in caught.exception.errors()])

    def test_rejects_null_for_each_field(self):
        for name in COLUMNS:
            payload = dict(VALID)
            payload[name] = None
            with self.assertRaises(ValidationError) as caught:
                PatientProfile.model_validate(payload)
            self.assertIn(name, [error["loc"][0] for error in caught.exception.errors()])

    def test_rejects_wrong_types(self):
        replacements = {
            "age": "63",
            "sex": True,
            "cp": 1.0,
            "trestbps": "145",
            "chol": "233",
            "fbs": 1.5,
            "restecg": "2",
            "thalach": 150.5,
            "exang": False,
            "oldpeak": "2.3",
            "slope": 3.0,
            "ca": "0",
            "thal": 6.0,
        }
        for name, value in replacements.items():
            payload = dict(VALID)
            payload[name] = value
            with self.assertRaises(ValidationError, msg=name) as caught:
                PatientProfile.model_validate(payload)
            self.assertIn(name, [error["loc"][0] for error in caught.exception.errors()])

    def test_rejects_unauthorized_codes(self):
        replacements = {
            "sex": 2,
            "cp": 0,
            "fbs": 2,
            "restecg": 3,
            "exang": -1,
            "slope": 4,
            "ca": 4,
            "thal": 1,
        }
        for name, value in replacements.items():
            payload = dict(VALID)
            payload[name] = value
            with self.assertRaises(ValidationError, msg=name) as caught:
                PatientProfile.model_validate(payload)
            self.assertIn(name, [error["loc"][0] for error in caught.exception.errors()])

    def test_rejects_non_finite_measurements(self):
        for name, value in (("trestbps", float("inf")), ("chol", float("nan")), ("oldpeak", float("-inf"))):
            payload = dict(VALID)
            payload[name] = value
            with self.assertRaises(ValidationError, msg=name):
                PatientProfile.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
