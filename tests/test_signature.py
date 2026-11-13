import unittest
from models import LDResult,validated_result


class SignatureEvidenceTests(unittest.TestCase):
    def test_positive_signature_requires_handwritten_evidence(self):
        data=LDResult(other_country=True,signature_present=True).model_dump()
        result=validated_result(data)
        self.assertIsNone(result['signature_present'])
        self.assertTrue(result['needs_review'])
        data['signature_evidence']='handwritten'
        self.assertIs(validated_result(data)['signature_present'],True)

    def test_conflicting_evidence_is_not_reported_as_definite(self):
        data=LDResult(other_country=True,signature_present=False,signature_evidence='handwritten').model_dump()
        self.assertIsNone(validated_result(data)['signature_present'])

if __name__=='__main__':unittest.main()
