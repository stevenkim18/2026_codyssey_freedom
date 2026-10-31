"""Synthetic conversion oracles; no GLiNER installation, network or GPU."""
import copy
import json
import re
import unittest

from model.data.conversion import ConversionError, convert_record
from model.tests.helpers import record


LABELS = {'PERSON': 'person', 'EMAIL_ADDRESS': 'email address'}


def words(text):
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r'\w+|\S', text)]


class FakePlanner:
    """Interface fake, not a claim about the actual pinned GLiNER tokenizer."""

    def __init__(self, *, max_span=12, tokens=None, windows=None):
        self.max_span = max_span
        self._tokens = tokens
        self._windows = windows

    def tokens(self, text):
        if self._tokens is not None:
            return self._tokens(text) if callable(self._tokens) else self._tokens
        return words(text)

    def windows(self, text):
        if self._windows is not None:
            return self._windows(text) if callable(self._windows) else self._windows
        tokens = self.tokens(text)
        return [(tokens[0][1], tokens[-1][2])] if tokens else []


def gold(text, start, end, kind='PERSON'):
    return {'type': kind, 'start': start, 'end': end, 'surface': text[start:end]}


class ConversionTests(unittest.TestCase):
    def convert(self, text, entities, planner=None, labels=None):
        return convert_record(record(text=text, entities=entities), planner or FakePlanner(),
                              LABELS if labels is None else labels)

    def reject(self, code, text, entities, planner=None, labels=None):
        with self.assertRaises(ConversionError) as caught:
            self.convert(text, entities, planner, labels)
        self.assertEqual(caught.exception.code, code)

    def roundtrip(self, text, entities, result):
        self.assertEqual(len(result['examples']), len(result['windows']))
        self.assertEqual(result['source_entity_count'], len(entities))
        covered = set()
        reverse = {value: key for key, value in LABELS.items()}
        for example, window in zip(result['examples'], result['windows']):
            offsets = window['word_offsets']
            self.assertEqual(len(offsets), len(example['tokenized_text']))
            for token, (start, end) in zip(example['tokenized_text'], offsets):
                self.assertEqual(token, text[start:end])
                self.assertTrue(window['char_start'] <= start < end <= window['char_end'])
            reconstructed = {(reverse[label], offsets[start][0], offsets[end][1])
                             for start, end, label in example['ner']}
            expected = {(entities[i]['type'], entities[i]['start'], entities[i]['end'])
                        for i in window['gold_entity_indices']}
            self.assertEqual(reconstructed, expected)
            covered.update(window['gold_entity_indices'])
        self.assertEqual(covered, set(range(len(entities))))

    def test_single_word_uses_inclusive_equal_indices(self):
        text = '김가상'
        entities = [gold(text, 0, 3)]
        result = self.convert(text, entities)
        self.assertEqual(result['examples'][0]['ner'], [[0, 0, 'person']])
        self.roundtrip(text, entities, result)

    def test_multiword_end_is_inclusive_last_word(self):
        text = '가상 회사'
        entities = [gold(text, 0, len(text))]
        result = self.convert(text, entities)
        self.assertEqual(result['examples'][0]['ner'], [[0, 1, 'person']])
        self.roundtrip(text, entities, result)

    def test_repeated_surface_uses_supplied_second_offset(self):
        text = '김가상 김가상'
        entities = [gold(text, 4, 7)]
        result = self.convert(text, entities)
        self.assertEqual(result['examples'][0]['ner'], [[1, 1, 'person']])
        self.roundtrip(text, entities, result)

    def test_attached_korean_particle_is_not_added_to_gold(self):
        text = '김가상에게 연락'
        self.reject('unaligned_entity_boundary', text, [gold(text, 0, 3)])

    def test_midword_start_is_not_expanded(self):
        text = '접두김가상'
        self.reject('unaligned_entity_boundary', text, [gold(text, 2, 5)])

    def test_gold_whitespace_is_not_stripped_to_fit(self):
        text = ' 김가상 '
        self.reject('unaligned_entity_boundary', text, [gold(text, 0, 4)])

    def test_spaces_tabs_and_newlines_preserve_original_offsets(self):
        text = '\t김가상  \n이가상 '
        entities = [gold(text, text.index('김가상'), text.index('김가상') + 3),
                    gold(text, text.index('이가상'), text.index('이가상') + 3)]
        result = self.convert(text, entities)
        self.assertEqual(result['examples'][0]['ner'], [[0, 0, 'person'], [1, 1, 'person']])
        self.roundtrip(text, entities, result)

    def test_non_bmp_prefix_uses_codepoints(self):
        text = '😀 김가상'
        entities = [gold(text, 2, 5)]
        result = self.convert(text, entities)
        self.assertEqual(result['examples'][0]['ner'], [[1, 1, 'person']])
        self.roundtrip(text, entities, result)

    def test_decomposed_accent_is_not_normalized(self):
        text = 'e\u0301 김가상'
        entities = [gold(text, 3, 6)]
        result = self.convert(text, entities)
        self.assertEqual(result['examples'][0]['ner'], [[2, 2, 'person']])
        self.roundtrip(text, entities, result)

    def test_zwj_emoji_nonwhitespace_codepoints_are_preserved(self):
        text = '👩\u200d💻 김가상'
        entities = [gold(text, 4, 7)]
        result = self.convert(text, entities)
        self.assertEqual(result['examples'][0]['ner'], [[3, 3, 'person']])
        self.roundtrip(text, entities, result)

    def test_missing_zwj_coverage_is_rejected(self):
        text = '👩\u200d💻 김가상'
        planner = FakePlanner(tokens=[('👩', 0, 1), ('💻', 2, 3), ('김가상', 4, 7)])
        self.reject('invalid_token_coverage', text, [gold(text, 4, 7)], planner)

    def test_max_span_is_accepted_and_next_word_is_rejected(self):
        for width in (12, 13):
            with self.subTest(width=width):
                text = ' '.join('w' + str(i) for i in range(width))
                entities = [gold(text, 0, len(text))]
                if width == 12:
                    result = self.convert(text, entities)
                    self.assertEqual(result['examples'][0]['ner'], [[0, 11, 'person']])
                    self.roundtrip(text, entities, result)
                else:
                    self.reject('entity_exceeds_max_width', text, entities)

    def test_overlapping_windows_preserve_full_gold_and_restore_offsets(self):
        text = 'a b c d e f g h i'
        entities = [gold(text, 6, 9)]  # d e appears wholly in both windows.
        planner = FakePlanner(max_span=3, windows=[(0, 11), (6, 17)])
        result = self.convert(text, entities, planner)
        self.assertEqual([x['ner'] for x in result['examples']],
                         [[[3, 4, 'person']], [[0, 1, 'person']]])
        self.roundtrip(text, entities, result)

    def test_partial_gold_in_any_window_rejects_whole_document(self):
        text = 'a b c d e f g h i'
        entities = [gold(text, 8, 13)]  # e f g is partial in the first window.
        planner = FakePlanner(max_span=3, windows=[(0, 11), (6, 17)])
        self.reject('partial_entity_in_window', text, entities, planner)

    def test_safe_window_boundaries_do_not_create_false_negatives(self):
        text = 'a b c d e f g h i'
        entities = [gold(text, 8, 13)]
        planner = FakePlanner(max_span=3, windows=[(0, 7), (8, 17)])
        result = self.convert(text, entities, planner)
        self.assertEqual(result['examples'][0]['ner'], [])
        self.assertEqual(result['examples'][1]['ner'], [[0, 2, 'person']])
        self.roundtrip(text, entities, result)

    def test_missing_gold_window_never_returns_partial_success(self):
        text = 'a b 김가상'
        planner = FakePlanner(windows=[(0, 3)])
        with self.assertRaises(ConversionError) as caught:
            self.convert(text, [gold(text, 4, 7)], planner)
        self.assertIn(caught.exception.code, {'incomplete_gold_coverage', 'invalid_token_coverage'})

    def test_unknown_label_is_not_dropped(self):
        text = '김가상'
        self.reject('unknown_entity_type', text, [gold(text, 0, 3, 'UNKNOWN')])

    def test_unhashable_entity_type_uses_conversion_error(self):
        for kind in ([], {}):
            with self.subTest(kind=kind):
                row = record()
                row['entities'][0]['type'] = kind
                with self.assertRaises(ConversionError) as caught:
                    convert_record(row, FakePlanner(), LABELS)
                self.assertEqual(caught.exception.code, 'unknown_entity_type')

    def test_empty_and_whitespace_documents_do_not_succeed_vacuously(self):
        for text in ('', ' \n\t'):
            with self.subTest(text=repr(text)):
                with self.assertRaises(ConversionError):
                    self.convert(text, [])

    def test_duplicate_window_cannot_duplicate_training_weight(self):
        self.reject('invalid_window', '김가상', [gold('김가상', 0, 3)],
                    FakePlanner(windows=[(0, 3), (0, 3)]))

    def test_windows_must_follow_original_source_order(self):
        text = '김가상 이가상'
        self.reject('invalid_window', text, [gold(text, 0, 3), gold(text, 4, 7)],
                    FakePlanner(windows=[(4, 7), (0, 3)]))

    def test_missing_nongold_text_coverage_is_rejected(self):
        self.reject('invalid_token_coverage', '맑은 하늘', [],
                    FakePlanner(windows=[(0, 2)]))

    def test_missing_planner_limit_uses_conversion_error(self):
        planner = FakePlanner()
        del planner.max_span
        with self.assertRaises(ConversionError) as caught:
            self.convert('김가상', [gold('김가상', 0, 3)], planner)
        self.assertEqual(caught.exception.code, 'invalid_planner')

    def test_same_word_span_with_two_labels_keeps_both(self):
        text = '김가상'
        entities = [gold(text, 0, 3), gold(text, 0, 3, 'EMAIL_ADDRESS')]
        result = self.convert(text, entities)
        self.assertEqual(result['examples'][0]['ner'],
                         [[0, 0, 'person'], [0, 0, 'email address']])
        self.roundtrip(text, entities, result)

    def test_nonstring_mapping_key_is_rejected(self):
        self.reject('invalid_label_mapping', '김가상', [gold('김가상', 0, 3)],
                    labels={1: 'person'})

    def test_invalid_label_maps_are_rejected(self):
        for mapping in ({}, {'PERSON': ''}, {'PERSON': 123},
                        {'PERSON': 'same', 'EMAIL_ADDRESS': 'same'}):
            with self.subTest(mapping=mapping):
                self.reject('invalid_label_mapping', '김가상', [gold('김가상', 0, 3)], labels=mapping)

    def test_all_ner_labels_are_retained_in_fixed_order(self):
        result = self.convert('김가상', [gold('김가상', 0, 3)])
        self.assertEqual(result['examples'][0]['ner_labels'], list(LABELS.values()))

    def test_negative_example_has_empty_ner_and_same_label_inventory(self):
        result = self.convert('맑은 하늘', [])
        self.assertEqual(result['source_entity_count'], 0)
        self.assertEqual(result['examples'][0]['ner'], [])
        self.assertEqual(result['examples'][0]['ner_labels'], list(LABELS.values()))

    def test_invalid_gold_types_ranges_and_surface_are_rejected(self):
        for change in ({'start': False}, {'end': True}, {'start': -1}, {'end': 4},
                       {'start': 2, 'end': 1}, {'start': 1, 'end': 1}, {'surface': 'wrong'}):
            with self.subTest(change=change):
                entity = gold('김가상', 0, 3)
                entity.update(change)
                self.reject('invalid_gold_span', '김가상', [entity])

    def test_bad_token_strings_ranges_and_order_are_rejected(self):
        cases = [[('WRONG', 0, 3)], [('김가상', False, 3)], [('김가상', 0, 99)],
                 [('이가상', 4, 7), ('김가상', 0, 3)], [('김가상', 0, 3)]]
        text = '김가상 이가상'
        for tokens in cases:
            with self.subTest(tokens=tokens):
                self.reject('invalid_token_coverage', text, [gold(text, 0, 3)], FakePlanner(tokens=tokens))

    def test_input_row_and_source_metadata_are_not_mutated_or_leaked(self):
        row = record(text='김가상', entities=[gold('김가상', 0, 3)])
        row['template_text'] = 'ANSWER_LEAK_TEMPLATE'
        row['meta'] = {'answer': 'ANSWER_LEAK_META'}
        row['tokens'] = ['OLD_BIO_TOKEN_MUST_NOT_BE_USED']
        original = copy.deepcopy(row)
        result = convert_record(row, FakePlanner(), LABELS)
        self.assertEqual(row, original)
        self.assertEqual(set(result['examples'][0]), {'tokenized_text', 'ner', 'ner_labels'})
        rendered = json.dumps(result, ensure_ascii=False)
        for excluded in ('ANSWER_LEAK_TEMPLATE', 'ANSWER_LEAK_META', 'OLD_BIO_TOKEN_MUST_NOT_BE_USED'):
            self.assertNotIn(excluded, rendered)

    def test_error_does_not_include_raw_text_or_gold_value(self):
        text = 'SENSITIVE_CANARY_WITH_PARTICLE'
        with self.assertRaises(ConversionError) as caught:
            self.convert(text, [gold(text, 0, 3)])
        self.assertNotIn(text, str(caught.exception))
        self.assertNotIn(text[:3], str(caught.exception))


if __name__ == '__main__':
    unittest.main()
