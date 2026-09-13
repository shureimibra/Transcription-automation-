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

    def test_every_prompt_names_short_words_as_not_exempt(self):
        """
        FROM AN 11-DOCUMENT REAL-SUBMISSION REVIEW: a pronoun, contraction
        or quantifier leaked untranslated far more than any other defect
        found, always inline in an otherwise-correctly-translated sentence
        -- "I'm suis en Indiana maintenant" (literally "I'm am in Indiana
        now"). The general "do not leave X untranslated" rule alone did not
        stop this in practice, because these are easy to read past.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('SHORT WORDS ARE NOT EXEMPT', text)
                self.assertIn("I'm suis en Indiana", text)

    def test_every_prompt_distinguishes_a_generic_phrase_from_an_official_name(self):
        """
        THE SAME REVIEW: "Federal corporations" (a category, not the name
        of anything) and a bare backreference like "the Act" leaked in
        English, protected by the same exemption that legitimately lets a
        real official name stay untranslated. And "core" -- a real,
        substantive technical term -- leaked a dozen times with nothing to
        contradict it, which the consistency rule alone cannot catch.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('GENERIC DESCRIPTIVE PHRASE IS NOT AN '
                              'OFFICIAL NAME', text)
                self.assertIn('"core"', text)

    def test_french_typography_covers_currency_format(self):
        """FROM THE SAME REVIEW: eight dollar figures in a row left in raw
        English format ($60,000) in a document where an earlier, similar
        figure had been correctly reformatted to French convention moments
        before."""
        polish = self.prompts['polish']
        self.assertIn('1 000 $', polish)
        self.assertIn('$1,000', polish)

    def test_every_prompt_forbids_the_inventions_that_were_actually_seen(self):
        """Written from defects, not imagination: a real run supplied
        planethunters.org, a site the speaker never named, and another used
        square brackets the prompt had already forbidden."""
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('Do not add any website, domain name, URL', text)
                self.assertIn('square brackets', text)
                self.assertIn('No asterisks', text)

    def test_every_prompt_covers_technical_notation(self):
        """
        Not math-only: mathematics, chemistry, physics and similar fields
        are named explicitly, and the rule sits beside the number-form rule
        it must not contradict (rule 1 protects VALUE; this one is about
        NOTATION for a value or expression that was spoken out in words).
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('Technical notation', text)
                self.assertIn('mathematics, chemistry, physics', text)
                self.assertIn('x²', text)
                self.assertIn('never LaTeX', text)

    def test_the_review_prompt_does_not_flag_notation_as_an_invented_number(self):
        """
        Regression guard for the same false-positive class a real Eagle
        rejection surfaced: a digit-shaped token that is not a spoken
        quantity (there, an ASR mishearing; here, deliberately-introduced
        notation) must not trip the strict number-preservation check the
        review prompt itself asks for.
        """
        review = self.prompts['review']
        self.assertIn('EXCEPTION', review)
        # Split across the checks the source text's own line-wrap breaks
        # on, rather than asserting one unbroken phrase that spans it.
        self.assertIn('Unicode notation standing in for spoken', review)
        self.assertIn('technical language', review)
        self.assertIn('x²', review)

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


class TranscriptCheckPromptTestCase(TestCase):
    """
    The one prompt offered for a transcription task -- see
    worker_prompts.build_transcript_check's own docstring for why there is
    no rewrite variant: a worker's assistant never hears the audio, so a
    guess at a name is a lead to go verify, not a fact to paste over the
    document.
    """

    def test_it_never_offers_to_rewrite(self):
        """THE WHOLE DESIGN DECISION, IN ONE ASSERTION. If this ever starts
        asking for a corrected document instead of a report, a worker could
        paste an unconfirmed guess straight into a submission without ever
        pressing play."""
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('Report only', prompt)
        self.assertIn('do not rewrite', prompt.lower())
        self.assertNotIn('Reply with the corrected', prompt)

    def test_it_asks_for_the_specific_defects_this_batch_actually_found(self):
        """FROM A REAL 19-DOCUMENT REVIEW, not imagination: real names
        mangled (Milnor, Dirac, de Broglie), the same term spelled two
        different wrong ways in one document (Sierpinski), and the literal
        subject of one talk wrong for half of it (bore/ball)."""
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('two different wrong ways', prompt)
        self.assertIn('speaker labels', prompt)

    def test_it_does_not_flag_alphanumeric_notation_as_a_number(self):
        """The same false positive already fixed in the deterministic
        check (E4, 3D, K1, exponents) -- the prompt should not ask a
        worker's assistant to re-introduce it by hand."""
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('K1', prompt)
        self.assertIn('3D', prompt)
        self.assertIn('ten to the minus', prompt)

    def test_it_does_not_flag_a_duration_or_a_citation_as_a_number(self):
        """The same false positives already fixed in the deterministic
        check (years/kilometers as durations, "Rule 8"/"No. 1" as
        citations, "million" riding on a real count) -- the prompt
        should not ask a worker's assistant to flag any of these by
        hand, from a second real-submission audit round."""
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('2 years ago', prompt)
        # Checked separately, not as one unbroken phrase: the source's own
        # line-wrap breaks "5 kilometers" and "50 million" across a line,
        # same as the notation test above.
        self.assertIn('plain duration', prompt)
        self.assertIn('kilometers', prompt)
        self.assertIn('Rule 8', prompt)
        self.assertIn('scale word', prompt)
        self.assertIn('million', prompt)

    def test_it_does_not_flag_a_possessive_as_a_contraction(self):
        """FROM A REAL SUBMISSION: "Archie Cox's office" was flagged as
        an unexpanded contraction by the deterministic check before that
        was fixed -- the same false positive must not be reintroduced by
        a worker's own assistant reading this prompt."""
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        # Checked separately, not as one unbroken phrase: the prompt's own
        # line-wrap can split "Archie Cox's office" across a newline, same
        # as the number-rule test above.
        self.assertIn('Archie', prompt)
        self.assertIn("Cox's office", prompt)
        self.assertIn('possessive', prompt)

    def test_it_does_not_flag_the_verb_cause_as_slang(self):
        """FROM A REAL SUBMISSION: "will cause the landlord to evict"
        flagged as informal "'cause"."""
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('ordinary verb', prompt)
        self.assertIn("'cause", prompt)

    def test_edited_verbatim_is_told_to_flag_informal_words(self):
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('flag every informal word you see', prompt)
        self.assertIn('edited verbatim', prompt)

    def test_neither_standard_is_told_to_flag_contractions(self):
        """
        CHANGED. Eagle's General Guidelines are explicit and
        unconditional: contractions are kept exactly as spoken, under
        either standard -- so this prompt must never ask a worker's
        assistant to flag one, regardless of which standard applies.
        """
        for name in ('edited_verbatim', 'strict_verbatim', ''):
            prompt = worker_prompts.build_transcript_check(name)
            self.assertIn('Do NOT flag a contraction', prompt)
            self.assertNotIn('flag every one you see', prompt)

    def test_strict_verbatim_is_told_the_opposite_for_informal_words(self):
        """THE RULE STILL INVERTS FOR SLANG. Flagging an informal word on
        a strict-verbatim transcript would ask the worker to break the
        very standard the task is graded on."""
        prompt = worker_prompts.build_transcript_check('strict_verbatim')
        self.assertIn('do NOT flag any of these', prompt)
        self.assertIn('strict verbatim', prompt)
        self.assertNotIn('flag every informal word you see', prompt)

    def test_yeah_is_named_as_the_correct_form_not_a_defect(self):
        """Eagle's Writing Rules table names "yeah" as the required
        spelling -- the prompt must not ask a worker's assistant to
        "fix" it."""
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('Do NOT flag "yeah"', prompt)

    def test_an_unknown_standard_defaults_to_the_safer_instruction(self):
        prompt = worker_prompts.build_transcript_check('')
        self.assertIn('unless you are told', prompt)
