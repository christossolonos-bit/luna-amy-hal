from tools.dialogue_quality import audit_bank, audit_collection, normalize


def kinds(report):
    return set(report['counts'])


def test_contractions_case_unicode_and_slots_do_not_count_as_new_wording():
    assert normalize("I’m listening, {name}.") == normalize('I am listening, {speaker}.')
    report=audit_bank(["I'm listening, {name}.",'I AM listening, {speaker}.'])
    assert 'normalized-duplicate' in kinds(report)


def test_user_example_rejects_same_question_with_added_tail():
    report=audit_bank(['Evening{name}. What shall we talk about?',
                      "Evening{name}. What shall we talk about? I'm listening."])
    assert {'repeated-clause','padded-containment'} <= kinds(report)
    assert any(f.get('evidence')=='what shall we talk about' for f in report['findings'])


def test_prefixes_do_not_hide_identical_core():
    report=audit_bank(["I didn't understand that sentence.",
                      "Let me put it this way: I did not understand that sentence."])
    assert 'padded-containment' in kinds(report)


def test_ordinary_words_and_different_genuine_questions_are_allowed():
    report=audit_bank(['Evening{name}. Which subject has caught your attention today?',
                      'Good evening{name}. Is there a story you would enjoy sharing?'])
    assert report['passed']


def test_single_word_greetings_are_not_substantial_reused_clauses():
    report=audit_bank(['Hello{name}. Where would you like this conversation to lead?',
                      'Hello{name}. Has anything unexpected happened since we last spoke?'])
    assert 'repeated-clause' not in kinds(report)


def test_placeholder_variation_does_not_inflate_count():
    report=audit_bank(['Where does {person} live?','Where does {name} live?'],minimum=2)
    assert 'below-minimum' in kinds(report)


def test_actual_names_can_be_explicitly_neutralized():
    report=audit_bank(['What would you like to discuss, Travis?',
                      'What would you like to discuss, Sam?'],names=('Travis','Sam'))
    assert 'normalized-duplicate' in kinds(report)


def test_substantial_near_duplicate_is_flagged():
    report=audit_bank(['Which interesting subject would you like us to discuss today?',
                      'Which fascinating subject would you like us to discuss today?'])
    assert 'near-duplicate' in kinds(report)


def test_required_fact_clause_allowlist_is_explicit():
    report=audit_bank(['A terrier is a kind of dog. Shall we examine its relatives?',
                      'A terrier is a kind of dog. Where did that breed originate?'],
                     allow_clauses=('A terrier is a kind of dog.',))
    assert 'repeated-clause' not in kinds(report)


def test_simple_intent_polarity_guard_does_not_certify_semantics():
    report=audit_bank(['Yes, that is correct.','No, that is not correct.','I do not know.'],intent='unknown')
    assert report['counts']['intent-polarity-mismatch']==2


def test_cross_family_reuse_is_reported():
    report=audit_collection({'greet':['What shall we talk about?'],
                             'thanks':['Thank you. What shall we talk about?']})
    assert report['cross_family_reuse'][0]['clause']=='what shall we talk about'
    assert not report['passed']


def test_cross_family_full_duplicate_is_not_hidden_by_short_clause_exemption():
    report=audit_collection({'ack':['Got it.'],'thanks':['GOT IT!']})
    assert report['cross_family_duplicate_response_count']==1
    assert not report['passed']


def test_findings_are_bounded_and_pairs_use_one_based_indices():
    report=audit_bank(['I do not understand that sentence.']*6,max_findings=2)
    assert len(report['findings'])==2
    assert report['findings_truncated']
    assert report['findings'][0]['first']==1
    assert report['findings'][0]['second']==2
