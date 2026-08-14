import unittest

from app.services.local_tesseract_service import _parse_tsv


class LocalTesseractTsvTests(unittest.TestCase):
    def test_parse_tsv_preserves_words_geometry_and_identifiers(self):
        tsv = "\n".join((
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "1\t1\t0\t0\t0\t0\t0\t0\t1225\t1536\t-1\t",
            "5\t1\t2\t3\t4\t1\t270\t60\t150\t32\t91.5\tКрылья",
            "5\t1\t2\t3\t4\t2\t430\t60\t170\t32\t88.0\tСоветов",
        ))
        lines, words, width, height = _parse_tsv(tsv)
        self.assertEqual((width, height), (1225, 1536))
        self.assertEqual(lines[0].text, "Крылья Советов")
        self.assertEqual([word.text for word in words], ["Крылья", "Советов"])
        self.assertEqual(words[0].line_key, (1, 2, 3, 4))
        self.assertEqual(
            (words[0].left, words[0].top, words[0].width, words[0].height, words[0].confidence),
            (270, 60, 150, 32, 91.5),
        )


if __name__ == "__main__":
    unittest.main()
