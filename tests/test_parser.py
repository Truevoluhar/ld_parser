import io
import unittest
from pathlib import Path
from PIL import Image
import pypdfium2 as pdfium
from imaging import normalize
from models import LDResult, validated_result, strict_schema


def png():
    out=io.BytesIO();Image.new('RGB',(300,420),'white').save(out,format='PNG');return out.getvalue()


class PairingTests(unittest.TestCase):
    def test_sorts_and_pairs_by_numeric_id(self):
        content=png();pairs=normalize([('00000004.png',content),('00000001.png',content),('00000003.png',content),('00000002.png',content)])
        self.assertEqual([p.first_page for p in pairs],[1,3])
        for pair in pairs:
            doc=pdfium.PdfDocument(pair.pdf)
            self.assertEqual(len(doc),2);doc.close()
    def test_missing_duplicate_and_wrong_names_rejected(self):
        content=png()
        for files in [[('3.png',content)],[('3.png',content),('6.png',content)],[('3.png',content),('3.png',content)],[('scan.png',content)]]:
            with self.subTest(files=[f[0] for f in files]),self.assertRaises(ValueError):normalize(files)
    def test_multipage_pdf_requires_odd_start_and_even_length(self):
        out=io.BytesIO();a=Image.new('RGB',(200,300),'white');a.save(out,format='PDF',save_all=True,append_images=[a]);content=out.getvalue()
        self.assertEqual(normalize([('scan.pdf',content)],187)[0].first_page,187)
        with self.assertRaises(ValueError):normalize([('188.pdf',content)])
        with self.assertRaises(ValueError):normalize([('scan.pdf',content)],2)
    def test_samples_form_exactly_five_pairs(self):
        root=Path('/examples')
        if not root.exists():root=Path(__file__).resolve().parents[1]/'example_docs'
        files=[(p.name,p.read_bytes()) for p in root.glob('*.tif')]
        if not files:self.skipTest('Example documents not mounted')
        pairs=normalize(files)
        self.assertEqual([p.first_page for p in pairs],[3,5,7,187,189])
        self.assertTrue(all(max(size)<=2400 for p in pairs for size in p.sizes))
    def test_blank_zero_false_and_signature_semantics(self):
        data=LDResult().model_dump();data.update(other_country=False,amount1=0.0,signature_present=False)
        result=validated_result(data)
        self.assertEqual(result['amount1'],0.0)
        self.assertIsNone(result['amount2']);self.assertFalse(result['signature_present'])
        self.assertTrue(result['needs_review'])
    def test_missing_fields_and_nonfinite_rejected(self):
        with self.assertRaises(ValueError):validated_result({'amount1':1})
        data=LDResult().model_dump();data['amount1']=float('nan')
        with self.assertRaises(ValueError):validated_result(data)
    def test_schema_requires_nullable_fields(self):
        schema=strict_schema();self.assertEqual(set(schema['required']),set(LDResult.model_fields))
        self.assertFalse(schema['additionalProperties'])


if __name__=='__main__':unittest.main()
