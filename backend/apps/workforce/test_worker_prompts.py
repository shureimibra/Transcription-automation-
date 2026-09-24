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

    def test_every_prompt_asks_what_a_quantity_covers(self):
        """
        FROM A FAILED REVIEW: "un montant supplementaire de 100 $ en
        revenus d'interets, en revenus de dividendes et en gains en
        capital" was marked down because a single $100 amount cannot
        naturally be all three income types at once -- the source may
        mean $100 of each. check_numbers verifies a VALUE survives, not
        what it applies to, so this has to live in the prompt rather than
        a deterministic check (which would flag ordinary shared totals,
        "$100 in fees and taxes", just as often as real splits).
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('ONE QUANTITY PRECEDES SEVERAL CATEGORIES', text)
                self.assertIn('shared total', text)

    def test_every_prompt_allows_redrawing_sentence_boundaries(self):
        """FROM A COMPARISON AGAINST A COACH'S OWN AUDIT PROMPT: this prompt
        used to say "fix word order", which licenses reordering words
        inside a sentence but never says a long English sentence may become
        two French ones, or the reverse -- exactly what natural French
        rhythm often calls for on a spoken transcript."""
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('split one long', text)
                self.assertIn('Vary your connectors', text)

    def test_every_prompt_names_false_cognates_as_their_own_category(self):
        """opportunite/impacter are grammatically correct French words used
        in the wrong sense -- invisible to a spellchecker and to "translate
        every term" alike, since the word IS translated, just wrongly."""
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('FALSE COGNATES', text)
                self.assertIn('opportunité', text)
                self.assertIn('impacter', text)

    def test_french_typography_requires_the_superscript_century_ordinal(self):
        """XIXe siecle, not 19e siecle or spelled out -- a real French
        convention this prompt never stated until compared against a
        coach's own audit prompt that does."""
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('XIXᵉ siècle', text)
                self.assertIn('never spelled\n  out', text)

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

    def test_the_check_prompt_asks_what_a_quantity_covers_too(self):
        """The check prompt only reports, so it has no VERIFY AGAINST
        AUDIO line of its own -- an unclear quantity split is just another
        finding for a human to read, same as any other item on the
        numbered list."""
        review = self.prompts['review']
        self.assertIn('Also\n     check what a quantity COVERS', review)

    def test_the_polish_prompt_now_carries_the_source_too(self):
        """
        THE GAP A REAL SIDE-BY-SIDE COMPARISON EXPOSED: a document polished
        with the OLD, source-blind prompt still had a dropped sentence and
        a proper name normalized to the wrong one of two forms the source
        actually used at different points -- both invisible from reading
        the French alone, both only catchable next to the source. Polish
        gets the same source-embedding review already had.
        """
        polish = self.prompts['polish']
        self.assertIn('16 and 2 thirds percent', polish)
        self.assertIn('SOURCE (English)', polish)

    def test_the_polish_prompt_asks_for_name_research(self):
        """
        THE OTHER HALF OF THE SAME REAL COMPARISON: a corrupted case name
        ("St Edmund and St Edmund", almost certainly "Steadman and
        Steadman") survived a polish pass unresearched. Source-awareness
        alone would not have fixed this one -- it needed the explicit
        instruction to research rather than transcribe a corruption,
        borrowed from the review prompt's own equivalent rule.
        """
        polish = self.prompts['polish']
        self.assertIn('RESEARCH EVERY PROPER NAME', polish)

    def test_the_polish_prompt_flags_uncertain_corrections_for_audio_verification(self):
        """
        FROM A REAL, LIVE DISAGREEMENT this exact rule was written to
        resolve: one assistant researched a garbled "Alino... JoJo
        Scanlon" into the confident, detailed "Samuel A. Alito Jr. ...
        Diarmuid F. O'Scannlain... Ninth Circuit"; a second assistant,
        asked to audit that result, called the added detail an
        unconfirmed invention and argued for keeping it anyway on the
        grounds that a formal bio plausibly said it. BOTH were guessing
        -- neither had heard the audio, and there was no way to settle it
        from text alone. The fix is not "trust the first assistant" or
        "trust the second" -- it is "don't let either guess become the
        submitted text unmarked."
        """
        polish = self.prompts['polish']
        self.assertIn('VERIFY AGAINST AUDIO', polish)
        self.assertIn('ONLY GO AS FAR AS RESEARCH CAN ACTUALLY SETTLE', polish)
        # The exact detail class the real disagreement was about.
        self.assertIn('"Jr."', polish)
        self.assertIn('published" versus "forthcoming', polish)

    def test_the_polish_prompt_still_requires_researching_the_clear_cases(self):
        """The new limit must not regress into never correcting anything
        -- an unambiguous match (the original evidence: "bond lecture" is
        certainly "Vaughan lecture", there is no second reading) still
        gets fixed outright, not flagged."""
        polish = self.prompts['polish']
        self.assertIn('exactly one real, well-known match', polish)
        self.assertIn('use the correct form', polish)

    def test_the_polish_prompt_requires_an_exact_quotable_anchor_per_verify_item(self):
        """
        WRITTEN FOR A NEW FEATURE: a worker's own "Find" button jumps
        straight to the matching English source segment and audio for
        each VERIFY AGAINST AUDIO item, the same way the transcription
        accuracy-check's "Find it" already does. That only works if the
        quoted phrase is copied character-for-character from the source
        -- a paraphrase or a translated phrase would never match anything
        in the source text to search for. Also bans backticks/markdown
        specifically: a real accuracy-check report started wrapping
        phrases in backticks and broke every "Find it" on that document
        until the parser was fixed to strip them (2026-09-22) -- this
        heads the same failure off before it can happen here at all.
        """
        polish = self.prompts['polish']
        self.assertIn('quoted exactly', polish)
        self.assertIn('character-for-character', polish)
        self.assertIn('no backticks, no', polish)

    def test_the_polish_prompt_extends_audio_verification_to_quantity_scope(self):
        """
        Same mechanism as the garbled-name case, same VERIFY AGAINST AUDIO
        line -- a single figure in front of a list of categories can be
        just as unsettleable from text alone as a mangled name, and for
        the same reason: only the audio can say which reading the speaker
        meant.
        """
        polish = self.prompts['polish']
        self.assertIn('THE SAME LIMIT APPLIES TO WHAT A QUANTITY COVERS', polish)
        self.assertIn('name the spot on the VERIFY AGAINST AUDIO line', polish)

    def test_the_polish_prompt_does_not_smooth_over_a_genuine_contradiction(self):
        """
        FROM A COMPARISON AGAINST A COACH'S OWN AUDIT PROMPT: its rule --
        "do not fix logical conflicts in the original text unless it is an
        explicit typo" -- is the missing other half of the garbled-clause
        addition. Without it, "translate the most plausible reading" risks
        treating a speaker's real, uncomfortable point as transcription
        noise and quietly smoothing it into something tidier.
        """
        polish = self.prompts['polish']
        self.assertIn('NOT ABOUT WHAT THE SPEAKER ACTUALLY\nMEANT', polish)
        self.assertIn('contradiction and all', polish)

    def test_the_polish_prompt_extends_audio_verification_to_garbled_source_prose(self):
        """
        FROM A REAL CONTROLLED COMPARISON: the same task, same prompt, run
        through two assistants. One flagged four spots on VERIFY AGAINST
        AUDIO, all four places the ASR source itself did not parse
        ("our tankers' fades", "it's easier, more so pity"). The other
        flagged zero and silently resolved every one itself -- in one case
        by quietly inverting a clause's polarity. Neither was wrong by the
        OLD prompt's letter: the mechanism was scoped to a garbled name or
        a quantity's scope, and plain garbled prose was neither. This
        widens it to the general case so both assistants land on the safer
        behaviour instead of one of them guessing silently.
        """
        polish = self.prompts['polish']
        self.assertIn(
            'ANY SPOT WHERE THE SOURCE ITSELF IS GARBLED OR\nINCOMPLETE',
            polish)
        self.assertIn("our tankers' fades", polish)
        # It must still say translate a reading, not refuse to translate.
        self.assertIn('Translate the most plausible reading', polish)

    def test_the_no_extra_content_rule_no_longer_contradicts_verify_against_audio(self):
        """
        A REAL, INDEPENDENTLY-CONFIRMED READING PROBLEM: a second assistant,
        asked to evaluate this exact prompt, correctly noticed that
        _MUST_NOT's "your entire reply must be the corrected text and
        nothing else" appears BEFORE the VERIFY AGAINST AUDIO instruction,
        and concluded the two contradict -- recommending VERIFY AGAINST
        AUDIO be removed rather than the earlier rule be reconciled. The
        fix is the reconciliation, not the removal: the "nothing else"
        rule now names its own exception.
        """
        polish = self.prompts['polish']
        # Split across the prompt's own line-wrap break, same as this
        # file's other wrapped-text assertions.
        self.assertIn('nothing else, UNLESS a', polish)
        self.assertIn('later instruction in this same prompt', polish)
        # The exception is still real, not a loophole: still no OTHER
        # commentary is licensed anywhere in the prompt.
        self.assertIn('Do not add explanatory notes', polish)

    def test_ambiguous_domain_terms_still_get_no_inline_note_but_flagged_names_do(self):
        """The other half of the same reconciliation: _WHAT_TO_FIX's
        "never flag it in the text" (written for an ambiguous DOMAIN term,
        long before VERIFY AGAINST AUDIO existed) must not also swallow
        the new, deliberately separate flagging mechanism for a proper
        name research could only partly settle."""
        polish = self.prompts['polish']
        self.assertIn('never flag it with a note\n   inside the text itself',
                      polish)
        self.assertIn('a separate,\n   structured way to flag each of those elsewhere', polish)

    def test_a_clean_check_hands_back_something_to_paste(self):
        """
        THE OTHER HALF OF THE PROBLEM THIS SESSION SURFACED: a worker
        reading "CLEAN" had nowhere to get the draft back from except
        asking their assistant a second, separate question, or copying it
        by hand from the on-screen table. The instruction now asks for
        both in one reply.
        """
        review = self.prompts['review']
        self.assertIn('CLEAN', review)
        self.assertIn('unchanged', review)
        self.assertIn('=== TRANSLATION START ===', review)
        self.assertIn('=== TRANSLATION END ===', review)

    def test_the_reply_shape_marks_off_what_is_and_is_not_the_document(self):
        """
        A REAL LEAKAGE RISK, FLAGGED DIRECTLY BY A TASKER: every polish
        reply ends with a VERIFY AGAINST AUDIO footer, and every CLEAN
        check reply starts with a verdict word -- both read as ordinary
        text, so copying the whole assistant reply (the natural instinct)
        risked pasting them straight into the submission along with the
        real document. Both prompts wrap the document in unambiguous
        markers so the save path (strip_reply_scaffolding) has something
        reliable to find it by -- and now explicitly tell the worker they
        do NOT need to hand-select around those markers, since that
        turned out not to hold up against a worker's own assistant UI
        offering one copy button for its whole reply.
        """
        polish = self.prompts['polish']
        self.assertIn('=== TRANSLATION START ===', polish)
        self.assertIn('=== TRANSLATION END ===', polish)
        self.assertIn('do not need to select just part', polish)
        self.assertIn('paste your whole\nanswer, markers and all', polish)

        review = self.prompts['review']
        self.assertIn('do not need to select just part', review)

    def test_the_translate_prompt_carries_the_same_audio_verification_as_polish(self):
        """
        FROM A REAL AUDIT: a worker asked whether "Copy prompt + English"
        (source_text=None, no machine draft to polish -- the from-scratch
        path, increasingly common during a Gemini outage that blocks the
        normal draft-then-polish flow) would catch the same problems as
        "Copy prompt + translation". It would not have: _TRANSLATE never
        had the VERIFY AGAINST AUDIO mechanism at all, so a garbled name or
        an unsettleable quantity split went straight through unflagged on
        that path, even though the underlying source text handed to both
        prompts is identical. This locks in the fix.
        """
        translate = self.prompts['translate']
        self.assertIn('VERIFY AGAINST AUDIO', translate)
        self.assertIn('ONLY GO AS FAR AS RESEARCH CAN ACTUALLY SETTLE', translate)
        self.assertIn('"Jr."', translate)
        self.assertIn('quoted exactly', translate)
        self.assertIn('character-for-character', translate)
        self.assertIn('no backticks, no', translate)
        self.assertIn('THE SAME LIMIT APPLIES TO WHAT A QUANTITY COVERS', translate)
        self.assertIn('name the spot on the VERIFY AGAINST AUDIO line', translate)
        self.assertIn(
            'ANY SPOT WHERE THE SOURCE ITSELF IS GARBLED OR\nINCOMPLETE',
            translate)

    def test_the_translate_prompt_reply_shape_matches_polish(self):
        """
        Same reasoning as test_the_reply_shape_marks_off_what_is_and_is_not_
        the_document, extended to translate: strip_reply_scaffolding looks
        for these markers regardless of which prompt produced the reply, so
        translate gains full save-path support for free once its reply
        shape matches polish's -- but only if the prompt text actually asks
        for that shape, which it did not before this fix (it only asked for
        "the {target} text only", with no markers and no VERIFY AGAINST
        AUDIO footer at all).
        """
        translate = self.prompts['translate']
        self.assertIn('=== TRANSLATION START ===', translate)
        self.assertIn('=== TRANSLATION END ===', translate)
        self.assertIn('do not need to select just part', translate)
        self.assertIn('paste your whole\nanswer, markers and all', translate)

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

    def test_every_prompt_asks_for_real_paragraphs_not_one_sentence_each(self):
        """
        CONFIRMED ACROSS EVERY REAL TRANSLATION REVIEWED THIS PROJECT: the
        source text fed to these prompts is built by joining ASR segments
        with a blank line (worker_views.py's TranslationDraftView.get),
        so the old "keep the paragraph breaks of the input" instruction
        meant literally one sentence per paragraph for the whole
        document -- reading as a machine transcript, not the
        "professional publication standard" the prompt itself asks for.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('Write in real paragraphs', text)
                self.assertIn('one sentence per paragraph', text)
                # The old failure mode's opposite must still be guarded
                # against -- this is not permission to produce one giant
                # unbroken block instead.
                self.assertIn('collapsed into one unbroken block', text)

    def test_the_prompts_end_where_the_worker_pastes(self):
        # The text is appended by the caller, so each prompt has to finish
        # on its handover line rather than trailing off mid-sentence.
        # `translate` starts from the source and pastes the source itself
        # under a plain header; `polish` and `review` both now carry the
        # source embedded IN the prompt (see build's own docstring), so
        # both end on the same "--- TRANSLATION ---" handover line -- only
        # the French gets appended by the caller either way.
        self.assertTrue(self.prompts['translate'].rstrip().endswith('Here is the text:'),
                        'translate does not end at the paste point')
        for name in ('polish', 'review'):
            self.assertTrue(
                self.prompts[name].rstrip().endswith('--- TRANSLATION (French) ---'),
                f'{name} does not end at the paste point')

    def test_an_unmapped_language_still_produces_something_usable(self):
        prompts = worker_prompts.build('en', 'xx')

        # Never a silent empty string where a language name belongs.
        self.assertIn('XX', prompts['translate'])

    def test_every_prompt_bans_inventing_a_word_that_does_not_exist(self):
        """
        FROM A REAL REJECTED SUBMISSION: "modérément monastisée" -- not a
        real French word, however derivable it looks from "monastère".
        Nothing else about that passage was flagged; this rule exists so
        the same slip does not recur.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('NEVER COIN A', text)
                self.assertIn('modérément monastisée', text)

    def test_every_prompt_asks_for_real_wording_on_honours_and_awards(self):
        """
        FROM THE SAME REAL REJECTION: a ceremonial phrase ("named to an
        order") has fixed, established wording in the target language, the
        same way a statute name does -- a close paraphrase reads as an
        error even when the meaning survives.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('HONOURS, AWARDS AND CEREMONIAL TITLES', text)

    def test_every_prompt_asks_for_modifier_placement_to_resolve_ambiguity(self):
        """
        Two real rejected passages turned on exactly this: a modifier
        placed so it could describe either the noun beside it or the
        larger phrase around it, with the source never settling which.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn(
                    'DO NOT LEAVE A MODIFIER SITTING WHERE IT COULD '
                    'ATTACH TO TWO DIFFERENT', text)

    def test_every_prompt_prefers_the_more_common_word_when_both_are_correct(self):
        """
        A soft tie-breaker, not a hard ban: several weak/debatable items
        across two real rejections shared one shape -- the reviewer
        preferred the plainer, more common word over a correct but more
        literary one. Worded as a default, not a rule against the elevated
        option outright, since this pattern is evidence from two
        rejections, not a settled fact about every reviewer.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn(
                    'THE MORE COMMON ONE IS THE', text)

    def test_the_review_prompt_asks_to_scan_for_a_person_credited_twice(self):
        """
        THE RULE THAT COST A REAL SUBMISSION: a lecture introduction's
        speaker list named "le professeur Dominic Lieven" twice. Nothing
        else on the page was wrong -- the rejection was earned on this
        alone, and nothing in the review checklist would have caught it
        before this rule was added.
        """
        review = self.prompts['review']
        self.assertIn('A person credited more than once', review)

    def test_every_prompt_asks_for_the_documents_own_jurisdiction(self):
        """
        Confirmed by comparing four independent translations of the same
        real Ontario legal document on 2026-09-22: drafts that used
        Canadian legal terminology for "bailiff" and "equity" were right,
        and a generic or France-default guess in the same spots was
        wrong. Nothing told the assistant to identify the jurisdiction at
        all before this rule -- it was working by accident when it
        worked.
        """
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn(
                    "IDENTIFY THE DOCUMENT'S EVIDENT COUNTRY OR "
                    'JURISDICTION', text)

    def test_the_jurisdiction_rule_names_the_real_canadian_example(self):
        for name, text in self.prompts.items():
            with self.subTest(prompt=name):
                self.assertIn('exécution en équité', text)
                self.assertIn('civil-law system at all', text)


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

    def test_it_treats_a_label_word_plus_number_as_always_a_figure(self):
        """
        CORRECTED, not merely added -- see the docstring on this change in
        worker_prompts.py itself. Eagle's own General Guidelines name this
        exactly: the Capitalization table's "Item in sequence" row lists
        "line 3, page 23, paragraph 14, size 36, step 1, verse 2" as
        exceptions that are ALWAYS figures, and the Numbers table's "Number
        of item" row lists "size 6 | Quiz 2 | Chapter 7" the same way --
        independent of the plain 0-9-spelled-out rule.

        An earlier version of this prompt got this backwards: it told a
        worker's assistant to leave "step one, step two" and "number
        one"/"number six" spelled out, reasoning from a narrower "citation"
        exception rather than Eagle's actual, broader rule. That directly
        caused two correct findings in a real report to be rejected as
        wrong during an audit BEFORE this prompt text was checked against
        the actual General Guidelines. "The first three" pictures is kept
        as the contrasting case: a plain count, naming nothing, correctly
        still a word.
        """
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('LABEL WORD immediately followed by its number', prompt)
        self.assertIn('"step 1"', prompt)
        self.assertIn('"number 1"', prompt)
        self.assertIn('the first three', prompt)
        self.assertIn('PLAIN COUNT stays spelled out', prompt)

    def test_it_does_not_flag_a_missing_accent_mark_or_banned_punctuation(self):
        """Eagle's Punctuation rules ban accents/diacritics, ellipses, and
        exclamation marks outright (fiance not fiance-with-an-accent, El
        Nino not El Nino-with-a-tilde) -- none of this was in the check
        prompt at all, so an assistant unaware of it could flag a
        correctly de-accented name as if it were a mishearing."""
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('accent or diacritic', prompt)
        self.assertIn('El Nino', prompt)
        self.assertIn('ellipsis', prompt)
        self.assertIn('Yahoo!', prompt)

    def test_it_asks_for_actual_changes_only_not_a_confirmation_list(self):
        """FROM A REAL AUDIT: a report padded its count with entries like
        "two dice -> two dice" that proposed no change at all -- noise
        that makes the report look more thorough than it is without
        giving the worker anything to act on."""
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('Only list something here if you are proposing an actual change', prompt)

    def test_it_asks_for_spoken_math_converted_to_notation_on_any_subject(self):
        """
        ADDED after a design discussion prompted by a real audit: this
        document's mishearing checks were solid, but nothing asked the
        assistant to convert genuinely written-out equations into Eagle's
        symbolic house style ("p minus q a" -> "p - qA"), and every
        heavily mathematical transcript audited this session left that
        gap unfilled.

        DELIBERATELY NOT SCOPED TO A FIXED SUBJECT LIST -- a future
        recording may be about a subject nobody has written a rule for
        yet, and a hardcoded list of "math, physics, chemistry" would
        silently miss it. The prompt says so explicitly.

        The "Notation:" marker is load-bearing, not decorative: it is
        what accuracyFindings.js's classifyReason (frontend) uses to tell
        a formatting fix apart from a claim about what was actually
        said, so the checklist can stop asking a worker to verify a pure
        notation change against audio it does not need.
        """
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('not just the ones you recognise', prompt)
        self.assertIn('Greek letters', prompt)
        self.assertIn('Sigma', prompt)
        self.assertIn('word "Notation:" at the very start of the REASON', prompt)

    def test_it_puts_factorial_in_words_not_the_banned_exclamation_mark(self):
        """
        CONFIRMED LIVE: the notation section's own "Operators: the real
        symbol, not the word" instruction, with no exception carved out,
        led an assistant to correct spoken "2 factorial" to "2!" -- which
        Eagle's own gate then rejected, because rule 4b bans exclamation
        marks outright with no exception for mathematical notation. The
        prompt was asking for something its own earlier rule refuses.
        """
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('EXCEPT factorial', prompt)
        self.assertIn('"2 factorial" is', prompt)

    def test_it_says_the_marker_must_not_reach_the_corrected_value(self):
        """
        CONFIRMED LIVE: a real report put "Notation:" on the corrected-
        value line for four straight findings instead of the reason line
        -- not a fluke, a pattern. Apply wrote the literal word
        "Notation:" into a real transcript four times before this was
        caught. The frontend now defends against this too (see
        accuracyFindings.js's classifyFinding), but the prompt should say
        so plainly as well, not rely on the workaround alone.
        """
        prompt = worker_prompts.build_transcript_check('edited_verbatim')
        self.assertIn('not on the corrected value', prompt)
        self.assertIn('goes straight\ninto the transcript', prompt)


class ReplyScaffoldingTestCase(TestCase):
    """
    strip_reply_scaffolding -- recovering the document from a worker's
    assistant's WHOLE raw reply.

    WHY THIS REPLACED "just don't paste the footer": that instruction
    depended on a worker hand-selecting text around markers, and a real
    worker's assistant UI (Gemini's canvas view) turned out to offer one
    copy button for its entire reply -- markers, footer and all. This is
    what actually stands between that one-click copy and a leaked footer
    reaching Eagle, so every real reply shape the prompts ask for (and a
    few they do not, since a worker's own assistant will not always obey
    exactly) needs to come out clean here.
    """

    def test_the_documented_shape_round_trips_clean(self):
        """The exact shape _POLISH's own reply instruction asks for."""
        raw = (
            '=== TRANSLATION START ===\n'
            'Le mandataire agit pour le mandant.\n'
            '=== TRANSLATION END ===\n'
            'VERIFY AGAINST AUDIO: NONE'
        )
        document, verify_note = worker_prompts.strip_reply_scaffolding(raw)
        self.assertEqual(document, 'Le mandataire agit pour le mandant.')
        self.assertEqual(verify_note, '')

    def test_a_real_verify_note_survives_instead_of_vanishing(self):
        """
        THE ONE CASE THAT MUST NEVER SILENTLY DISAPPEAR: stripping the
        footer out of the saved document is only safe because the caller
        is required to still surface it. Losing this would quietly
        recreate the exact Alito/O'Scannlain-style problem
        VERIFY AGAINST AUDIO was built to prevent.
        """
        raw = (
            '=== TRANSLATION START ===\n'
            'Le juge Alito a écrit l\'opinion majoritaire.\n'
            '=== TRANSLATION END ===\n'
            'VERIFY AGAINST AUDIO: whether "Jr." belongs after Alito -- '
            'the source never says it.'
        )
        document, verify_note = worker_prompts.strip_reply_scaffolding(raw)
        self.assertEqual(document, 'Le juge Alito a écrit l\'opinion majoritaire.')
        self.assertIn('Jr.', verify_note)

    def test_a_clean_verdict_with_markers_is_stripped_too(self):
        """The exact shape _REVIEW's CLEAN branch asks for."""
        raw = (
            'CLEAN\n\n'
            '=== TRANSLATION START ===\n'
            'Bonjour tout le monde.\n'
            '=== TRANSLATION END ===\n'
        )
        document, verify_note = worker_prompts.strip_reply_scaffolding(raw)
        self.assertEqual(document, 'Bonjour tout le monde.')
        self.assertEqual(verify_note, '')

    def test_no_markers_at_all_is_the_ordinary_case_and_is_untouched(self):
        """Most replies will not need any of this -- an assistant that
        just returns clean prose must pass through byte-for-byte."""
        document, verify_note = worker_prompts.strip_reply_scaffolding(
            'Le mandataire agit pour le mandant.')
        self.assertEqual(document, 'Le mandataire agit pour le mandant.')
        self.assertEqual(verify_note, '')

    def test_a_trailing_footer_with_no_markers_is_still_pulled_out(self):
        """An assistant that ignores the marker instruction but still
        appends the footer -- the pre-marker shape this session started
        with -- must still not leak into the saved document."""
        raw = (
            'Le mandataire agit pour le mandant.\n'
            'VERIFY AGAINST AUDIO: the spelling of the witness\'s name.'
        )
        document, verify_note = worker_prompts.strip_reply_scaffolding(raw)
        self.assertEqual(document, 'Le mandataire agit pour le mandant.')
        self.assertIn('witness', verify_note)

    def test_an_unmatched_stray_marker_does_not_survive_into_the_document(self):
        """DEFENSIVE: a truncated paste or a half-followed instruction
        should not leave a literal marker line sitting in what gets
        saved and eventually submitted."""
        document, _ = worker_prompts.strip_reply_scaffolding(
            '=== TRANSLATION START ===\nBonjour.')
        self.assertNotIn('TRANSLATION START', document)
        self.assertIn('Bonjour.', document)

    def test_a_genuine_document_that_happens_to_start_with_the_word_clean(self):
        """The CLEAN-stripping must not eat real content: only a bare
        CLEAN verdict on its own first line is a verdict at all."""
        document, _ = worker_prompts.strip_reply_scaffolding(
            'Clean water access remains a major policy challenge.')
        self.assertEqual(
            document, 'Clean water access remains a major policy challenge.')

    def test_case_and_spacing_variations_in_the_markers_still_match(self):
        """A worker's own assistant will not always reproduce whitespace
        or capitalisation exactly -- this only has to recognise its own
        vocabulary, not demand an exact byte match."""
        raw = '===  translation start  ===\nBonjour.\n=== TRANSLATION END ===\n'
        document, _ = worker_prompts.strip_reply_scaffolding(raw)
        self.assertEqual(document, 'Bonjour.')
