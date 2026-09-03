"""
The prompts a worker pastes into their own assistant.

WHY THESE ARE TESTED AT ALL. They are not decoration: a worker's assistant
is the last thing to touch the document before the submission gate sees it.
A prompt that omits a rule the gate enforces produces work rejected here; a
prompt that contradicts one produces work rejected by Eagle. Either way the
worker did as they were told and lost the task.

These assert on WORDING, which is unusual and deliberate. The wording is the
product here -- there is no behaviour to test underneath it -- so a rule
silently dropped from a prompt would otherwise be invisible until somebody's
submission came back.

The rules themselves, and the evidence behind each, are in
docs/TRANSLATION_POLISH_PROMPT.md.
"""
from django.test import TestCase

from . import worker_prompts


class WorkerPromptTestCase(TestCase):

    def setUp(self):
        self.prompts = worker_prompts.build(
            'en', 'fr', source_text='A tax of 16 and 2 thirds percent.')

    def test_every_prompt_carries_the_number_form_rule(self):
        """
        THE RULE THAT COST A SUBMISSION. On task 4813 the model turned "16
        and 2 thirds percent" into "16,67 pour cent" -- right arithmetic,
        invented precision, two blocking findings. A worker's assistant does
        exactly the same thing unless told not to.

        Note this is a DIFFERENT rule from "do not alter any number": 16.67
        arguably has the same value, which is precisely why the value rule
        alone did not prevent it. Both must be present.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('SAME FORM AND THE SAME PRECISION', text)
                self.assertIn('16 et deux tiers', text)
                self.assertIn('NEVER "16,67"', text)
                # The value rule, which the form rule sits beside.
                self.assertIn('Do not invent, remove, or alter any number', text)

    def test_every_prompt_forbids_leaving_source_text_untranslated(self):
        # A HIGH finding seen on a real task: a run of English carried
        # straight into the French. It does not block submission, so the
        # prompt is the only thing standing in front of it.
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('Do not leave any English word or phrase '
                              'untranslated', text)
                self.assertIn('Write only in French', text)

    def test_every_prompt_forbids_the_inventions_that_were_actually_seen(self):
        """Written from defects, not imagination: a real run supplied
        planethunters.org, a site the speaker never named, and another used
        square brackets the prompt had already forbidden."""
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('Do not add any website, domain name, URL', text)
                self.assertIn('square brackets', text)
                self.assertIn('No asterisks', text)

    def test_the_prompts_ask_for_the_right_domain_vocabulary(self):
        """
        NO HARDCODED LIST OF DOMAINS. Eagle sends legal, medical, academic,
        scientific, technical and financial material, and a fixed list would
        be wrong the first time something arrives that is not on it. The
        assistant is asked to identify the domain from the document, which
        is what the platform's own quality layer does.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('Domain accuracy', text)
                self.assertIn('Identify what the document is about', text)

    def test_french_typography_is_spelled_out_rule_by_rule(self):
        # Every line here is a check the gate runs, so the prompt states
        # them rather than assuming the assistant knows French convention.
        polish = self.prompts['polish']
        for rule in ('Narrow non-breaking space', 'Guillemets',
                     'Decimal comma', 'Space before the percent sign',
                     'Space as thousands separator'):
            self.assertIn(rule, polish)

    def test_a_language_with_no_measured_rules_gets_the_general_instruction(self):
        """
        INVENTING A TYPOGRAPHY LIST FOR A LANGUAGE NOBODY HAS MEASURED would
        be guessing, and a wrong rule is worse than none -- the worker cannot
        tell it is wrong, and the gate does not check it either.
        """
        spanish = worker_prompts.build('en', 'es')['polish']

        self.assertIn('SPANISH TYPOGRAPHY', spanish.upper())
        self.assertNotIn('Guillemets with inner spacing', spanish)
        self.assertIn('conventions of written Spanish', spanish)

    def test_languages_are_named_never_left_as_codes(self):
        """"Translate into fr" is not an instruction anybody should have to
        interpret -- neither the worker nor their assistant."""
        for text in self.prompts.values():
            self.assertIn('French', text)
            self.assertNotIn('into fr\n', text)

    def test_the_check_prompt_reports_and_does_not_rewrite(self):
        """
        A DIFFERENT JOB FROM POLISHING. Asked to improve a draft that is
        already correct, an assistant changes things for the sake of
        changing them. Asked to report, it gives a second opinion that can
        be compared against what the gate found.
        """
        review = self.prompts['review']
        self.assertIn('DO NOT REWRITE', review)
        self.assertIn('CLEAN', review)
        # It carries the source itself: pasting two documents in the right
        # order by hand is a step that goes wrong.
        self.assertIn('16 and 2 thirds percent', review)
        # And it names the three that actually cost submissions.
        self.assertIn('1. Numbers.', review)
        self.assertIn('2. Untranslated English.', review)
        self.assertIn('3. Inventions.', review)

    def test_nothing_may_be_reordered_however_much_is_rewritten(self):
        """
        The prompts ask for fluency rewriting, which is the whole point of
        the polish step -- but a rewrite that reorders or condenses trips
        check_length_anomaly and loses the task. The guard travels with the
        licence.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('Do not summarise, shorten, expand, or '
                              'reorganise', text)
                self.assertIn('in the same order', text)

    def test_the_prompts_end_where_the_worker_pastes(self):
        # The text is appended by the caller, so each prompt has to finish
        # on its handover line rather than trailing off mid-sentence.
        for name in ('translate', 'polish'):
            self.assertTrue(self.prompts[name].rstrip().endswith('Here is the text:'),
                            f'{name} does not end at the paste point')
        self.assertTrue(
            self.prompts['review'].rstrip().endswith('--- TRANSLATION (French) ---'))

    def test_an_unmapped_language_still_produces_something_usable(self):
        prompts = worker_prompts.build('en', 'xx')

        # Never a silent empty string where a language name belongs.
        self.assertIn('XX', prompts['translate'])
