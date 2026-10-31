import inspect
import unittest

from model.data.conversion import ConversionError, convert_record
from model.segmentation.splitters import SPLITTERS, ko_atomic_suffix_v3, ko_script_suffix_v2, whitespace_default_v1

MAPPING = {'PERSON': 'person', 'KR_PHONE_NUMBER': 'phone number', 'EMAIL_ADDRESS': 'email address', 'URL': 'URL'}


class FakePlanner:
    """Whole text as one window; only the splitter under test varies."""
    max_span = 12

    def __init__(self, splitter):
        self.splitter = splitter

    def tokens(self, text):
        return self.splitter(text)

    def windows(self, text):
        tokens = self.splitter(text)
        return [(tokens[0][1], tokens[-1][2])]


def row(text, *entities):
    return {'text': text, 'entities': [{'type': t, 'start': a, 'end': b, 'surface': text[a:b]} for t, a, b in entities]}


class SplitterContractTest(unittest.TestCase):
    def test_splitters_take_original_text_only(self):
        for splitter in SPLITTERS.values():
            self.assertEqual(list(inspect.signature(splitter).parameters), ['text'])

    def test_reproduces_current_failure_and_candidates_fix_it(self):
        sample = row('김가상에게 연락', ('PERSON', 0, 3))
        with self.assertRaises(ConversionError) as caught:
            convert_record(sample, FakePlanner(whitespace_default_v1), MAPPING)
        self.assertEqual(caught.exception.code, 'unaligned_entity_boundary')
        for name in ('ko_particle_v1', 'ko_script_particle_v1', 'ko_script_suffix_v2'):
            result = convert_record(sample, FakePlanner(SPLITTERS[name]), MAPPING)
            self.assertEqual(result['examples'][0]['ner'], [[0, 0, 'person']], name)

    def test_digit_hangul_boundary_needs_script_cut(self):
        sample = row('번호는 010-1234-5678로 연락', ('KR_PHONE_NUMBER', 4, 17))
        for name in ('whitespace_default_v1', 'ko_particle_v1'):
            with self.assertRaises(ConversionError, msg=name):
                convert_record(sample, FakePlanner(SPLITTERS[name]), MAPPING)
        for name in ('ko_script_particle_v1', 'ko_script_suffix_v2'):
            convert_record(sample, FakePlanner(SPLITTERS[name]), MAPPING)

    def test_honorific_and_repeated_suffix_only_in_v2(self):
        sample = row('김가상님과 통화', ('PERSON', 0, 3))
        with self.assertRaises(ConversionError):
            convert_record(sample, FakePlanner(SPLITTERS['ko_script_particle_v1']), MAPPING)
        convert_record(sample, FakePlanner(ko_script_suffix_v2), MAPPING)

    def test_unsupported_boundary_is_rejected_not_repaired(self):
        sample = row('김가상씨에게', ('PERSON', 0, 3))   # '씨' is deliberately not a suffix in v2
        with self.assertRaises(ConversionError) as caught:
            convert_record(sample, FakePlanner(ko_script_suffix_v2), MAPPING)
        self.assertEqual(caught.exception.code, 'unaligned_entity_boundary')

    def test_coverage_offsets_and_no_normalization(self):
        texts = ['김가상에게 연락', '😀 김가상님과\n010-1234-5678로', '👨‍👩‍👧 가족', 'é́ combining', 'a\u3000b  c\t김이박은',
                 '김가상 김가상 김가상에게', '']
        for name, splitter in SPLITTERS.items():
            for text in texts:
                tokens = splitter(text)
                self.assertEqual(''.join(token for token, _, _ in tokens), ''.join(text.split()), (name, text))
                previous = 0
                for token, start, end in tokens:
                    self.assertEqual(text[start:end], token)
                    self.assertGreaterEqual(start, previous)
                    self.assertFalse(text[previous:start].strip())
                    previous = end

    def test_repeated_word_uses_actual_position(self):
        text = '김가상 김가상에게 김가상'
        sample = row(text, ('PERSON', 4, 7))
        result = convert_record(sample, FakePlanner(ko_script_suffix_v2), MAPPING)
        self.assertEqual(result['examples'][0]['ner'], [[1, 1, 'person']])
        self.assertEqual(result['windows'][0]['word_offsets'][1], [4, 7])

    def test_stems_are_never_emptied(self):
        for name, splitter in SPLITTERS.items():
            for text in ('은', '가', '님', '에게', '는', '인'):
                self.assertEqual([t[0] for t in splitter(text)], [text], (name, text))

    def test_url_wider_than_span_limit_needs_atomic_v3(self):
        url = 'https://www.example.com/auth/login?id=8be9b5bdb&next=/a/b/c/d'
        text = f'확인 후 {url}에서 가능합니다'
        sample = row(text, ('URL', 5, 5 + len(url)))
        for name in ('whitespace_default_v1', 'ko_script_suffix_v2'):
            with self.assertRaises(ConversionError) as caught:
                convert_record(sample, FakePlanner(SPLITTERS[name]), MAPPING)
            self.assertIn(caught.exception.code, ('entity_exceeds_max_width', 'unaligned_entity_boundary'), name)
        result = convert_record(sample, FakePlanner(ko_atomic_suffix_v3), MAPPING)
        self.assertEqual(result['examples'][0]['ner'], [[3, 3, 'URL']])   # '확인' is split by v2/v3 into 확/인 (known over-splitting)

    def test_atomic_tokens_exclude_trailing_punctuation_and_hangul(self):
        tokens = [t for t, _, _ in ko_atomic_suffix_v3('(http://x.org/a), 메일abc.d@live.com로.')]
        self.assertEqual(tokens, ['(', 'http://x.org/a', ')', ',', '메일', 'abc.d@live.com', '로', '.'])

    def test_deterministic(self):
        for splitter in SPLITTERS.values():
            self.assertEqual(splitter('김가상님께서 010-1234-5678로'), splitter('김가상님께서 010-1234-5678로'))


if __name__ == '__main__':
    unittest.main()
