from allennlp.service.predictors import Predictor
from nltk.tree import Tree
from preprocess import preprocess_sent
from pattern.en import mood, tenses, lemma
from hashlib import sha256
import top100
import json

def load_predictor(path="/var/lib/allennlp/elmo-constituency-parser-2018.03.14.tar.gz"):
    """Load local copy of AllenNLP model"""
    return Predictor.from_path(path)

def subject_verb_pairs_from_tree(tree):
    """ Returns the individual words associated with each verb and noun phraseself.

    Will soon return pairs in the format:
    [{‘vp’: [ {‘word’: ‘has’, ‘label’: ‘VBZ’},  {‘word’: ‘been’, ‘label’: ‘VBG’ }, { ‘word’: ‘owed’, ‘label’: ‘VBZ’ } ],
                    ‘np’: [ { ‘word’: ‘so’, ‘label’: ‘DT’ }, { ‘word’: ‘much’, ‘label’: ‘PN’ }] },  ….  ]
    """
    pairs = get_verb_subject_phrases(tree)
    words = []
    for pair in pairs['subjects_with_verbs']:
        np = pair['np']
        vp = pair['vp']
        words.append({'vp': verb_words_from_phrase(vp), 'np': subject_words_from_phrase(np)})
    return words


def get_verb_subject_phrases(tree):
    """
    Returns pairs in the format:
    { 'subjects_with_verbs': [{'vp': Tree(Verb phrase), 'np': Tree(Noun phrase)}, {'vp': Tree(second verb phrase), 'np': Tree(second noun phrase)}, ...] }
    """
    pairs = []

    # Declarative clause (most sentences):
    for s in tree.subtrees(lambda t: t.label() == 'S'):
        pairs += verb_subject_for_declarative_clause(s)

    # SQ: yes-no question or following a wh-phrase:
    for sq in tree.subtrees(lambda t: t.label() == 'SQ'):
        pairs += verb_subject_for_sq(sq)

    # SBARQ: "Direct question introduced by a wh-word or a wh-phrase"
    for sbarq in tree.subtrees(lambda t: t.label() == 'SBARQ'):
        pairs += verb_subject_for_sbarq(sbarq)

    # SBAR: Subordinating conjunction
    for sbar in tree.subtrees(lambda t: t.label() == 'SBAR'):
        pairs += verb_subject_for_sbar(sbar)

    # Fragments (parsed same as declarative clause):
    for s in tree.subtrees(lambda t: t.label() == 'FRAG'):
        pairs += verb_subject_for_declarative_clause(s)

    # Clauses with subject-auxillary inversion
    for s in tree.subtrees(lambda t: t.label() == 'SINV'):
        pairs += verb_subject_for_subject_inversion(s)

    return { 'subjects_with_verbs': pairs }

# MARK: Extracting pairs from various clauses:

def verb_subject_for_declarative_clause(tree):
    """ Takes in the tree for a vanilla declarative clause (S tag)
        Returns list of subject, verb pairs, empty if none

        Broadly, covers cases:
        (1) Standard noun-phrase and verb-phrase as siblings: "Joyce is amazing"
        (2) Multiple verb phrases: "At once I was in heaven and danced freely on the sand"
        (3) Declarative clause verb-phrase as subject: "Swinging from vines is fun"

    """
    np, s_child = None, None # Possible subjects
    vps = []
    for i in range(0, len(tree)):
        child = tree[i]
        np = child if child.label() == "NP" else np
        s_child = child if child.label() == "S" else s_child
        vps += [child] if child.label() == "VP" else []

    vps = sum([unpack_verb_phrases(vp) for vp in vps], [])
    if np is not None: # Noun phrase as subject
        return [{ 'vp': vp, 'np': np } for vp in vps]
    elif s_child is not None: # Declarative clause as subject
        return [{ 'vp': vp, 'np': s_child } for vp in vps]
    # TODO: Under what circumstances should we return a pair with None np?
    return []

def verb_subject_for_sq(tree):
    """
    Takes tree for an SQ clause: follows a wh-word or a wh-phrase, or inverted yes-no question
    Returns list of subject, verb pairs, empty if none

    The verb is typically contained as usual inside the SQ as a series of verbs
    So we simply manually pass the noun
    Misses subjects in SBARQs, which are caught in SBARQ method
    """
    verb_labels = ["VP", "MD", "VB", "VBZ", "VBP", "VBD", "VBN", "VBG"]

    np = None
    for child in tree:
        np = child if child.label() == "NP" else np
    if np is not None:
        return [{ 'vp': tree, 'np': np }]
    return []

def verb_subject_for_sbar(tree):
    """
    Takes tree for a SBAR clause: subordinating conjunction
    Returns list of subject, verb pairs, empty if none

    Typically no subject, but sometimes subject is contained in wh-noun phrase
    Verb is typically contained in subsequent S clause
    """

    # Identify the wh-noun-phrase which contains subject
    # Identify subsequent S-clause which contains verb
    for i in range(0, len(tree) - 1):
        whnp = tree[i]
        s = tree[i + 1]
        if whnp.label() == 'WHNP' and s.label() == 'S':
            if subject_words_from_phrase(whnp):
                return [{ 'vp': s, 'np': whnp}]
    return []

def verb_subject_for_sbarq(tree):
    """
    Takes tree for a SBARQ clause: question introduced by a wh-word or a wh-phrase
    Returns list of subject, verb pairs, empty if none

    Subject is typically implied by the SQ after the question word.
    The subject of "Who is John" is "John", which is contained in the SQ
    """
    wh_words = ["WHADJP", "WHADVP", "WHNP", "WHPP"]
    # Identify the SQ (main clause of wh-question) which contains verb and subject
    # Restrict to those SQs which immediately follow wh words
    for i in range(0, len(tree) - 1):
        wh = tree[i]
        sq = tree[i + 1]
        if wh.label() in wh_words and sq.label() == 'SQ':
            if wh.label() == "WHNP" and subject_words_from_phrase(wh):
                return [{ 'vp': sq, 'np': wh}]
    return []

def verb_subject_for_subject_inversion(tree):
    """
    Takes tree for a SINV clause: clause with subject-auxillary inversion
    Example: "Never had I seen such a place"
    Returns list of subject, verb pairs, empty if none
    """
    verb_labels = ["MD", "VB", "VBZ", "VBP", "VBD", "VBN", "VBG"]

    # Find the subject, looking forwards
    for i in range(0, len(tree)):
        if tree[i].label() == 'NP':
            # Find the verb, looking backwards
            for j in reversed(range(0, i)):
                if tree[j].label() == 'VP':
                    return [{ 'np': tree[i], 'vp': tree[j] }]
                if tree[j].label() in verb_labels:
                    vp = Tree('VP', [tree[j]])
                    return [{ 'np': tree[i], 'vp': vp}]
    return []



# MARK: Helper functions for extracting pairs

def unpack_verb_phrases(vp):
    """
    If there are MULTIPLE verb phrases nested within the verb phrase, returns these
    Otherwise, returns a list with the original verb phrase
    """
    child_phrases = [child for child in vp if child.label() == 'VP']
    return child_phrases if len(child_phrases) > 1 else [vp]



def print_verb_subject_phrases(pairs):
    """Print verb_subject pairs in readable form"""
    print("Verb Subject Pairs: ")
    for pair in pairs['subjects_with_verbs']:
        print("Noun Phrase: ", ' '.join(pair['np'].leaves()) if type(pair['np']) is Tree else "None")
        print("Verb Phrase: ", ' '.join(pair['vp'].leaves()) if type(pair['vp']) is Tree else "None")



# MARK: SUBJECTS


def subject_words_from_phrase(subject):
    """
    Given subject phrase as a tree, returns list of relevant nouns with labels
    """
    pronoun_tags = ["PRP"]
    singular_tags = ["NN", "NNP"]
    plural_tags = ["NNS", "NNPS"]
    adj_tags = ["JJ", "JJR", "JJS"]
    determiners = ["DT"]
    noun_tags = pronoun_tags + singular_tags + plural_tags

    if subject is None:
        # No subject
        return []

    if subject.label() == "S":
        # Declarative clause as subject: "Swinging from vines is fun". Extract verb phrases as subject.
        return verb_words_from_phrase(subject)

    if len(subject) == 1 and subject[0].label() in determiners:
        # If noun phrase is only one determiner, return that:
        return [{'word': subject[0][0], 'label': subject[0].label()}]

    # Standard noun phrase. Gather noun words from the phrase.
    # If no noun phrases are present, subject may be adjective: "Melancholy hung over James"
    noun_words, noun_indices = [], []
    adj_words = []
    for i, child in enumerate(subject):
        if child.label() == "NP": # Recursively identify sub-phrases
            noun_words += subject_words_from_phrase(child)
            noun_indices += [i] * len(noun_words)
        elif child.label() in noun_tags:
            noun_words.append({'word': child[0], 'label': child.label()})
            noun_indices.append(i)
        elif child.label() in adj_tags:
            adj_words.append({'word': child[0], 'label': child.label()})
    # Compress any adjacent nouns (e.g. "Farmer Brown" or "Mrs. Jones")
    noun_words = compress_nouns(noun_words, noun_indices)
    return noun_words if noun_words else adj_words
    return []

def compress_nouns(noun_words, noun_indices):
    """
    Compresses nouns into singular if they are immediately adjacent.
    For example, [(Mickey), (Mantle)] will get compressed to [(Mickey Mantle)]

    noun_words is a list of objects in the form {'word': 'Mickey', 'label': 'NNS'}
    noun_indices is the list of indices of those words from the subject tree

    We perform this recursively, by merging the first two elements of the list if adjacent
    """
    if len(noun_words) <= 1:
        return noun_words
    if noun_indices[0] + 1 == noun_indices[1]:
        # Adjacent, compress nouns
        noun_words[1]['word'] = noun_words[0]['word'] + " " + noun_words[1]['word']
        return compress_nouns(noun_words[1:], noun_indices[1:])
    return noun_words[:1] + compress_nouns(noun_words[1:], noun_indices[1:])

# MARK: Verbs

def verb_words_from_phrase(vp):
    """
    Given a verb phrase, returns a list of the verb words in the phrase

    Broadly, handles cases:
    (1) Typical verbs
    (2) "to" before verbs without nesting. Usually in subjects: "To dance is to be free."
    """
    verb_tags = ["MD", "VB", "VBZ", "VBP", "VBD", "VBN", "VBG"]
    to_labels = ["TO"]

    if vp is None:
        return []

    words = []
    for i in range(0, len(vp)):
        child = vp[i]
        if child.label() in verb_tags + to_labels:
            words.append( { 'word': child[0], 'label': child.label() })
        if child.label() == "VP":
            words += verb_words_from_phrase(child)
    return words

# MARK: Extract pairs from sentence

def get_subject_verb_pairs(sent, predictor):
    """
    Takes a sentence and AllenNLP predictor, returns the subject-verb pairs
    """
    processed = preprocess_sent(sent)
    parse = predictor.predict_json({"sentence": processed})
    tree = Tree.fromstring(parse["trees"])
    return subject_verb_pairs_from_tree(tree)


# MARK: Test Sentences and Pipeline

def test_pipeline(sent, predictor):
    """
    Test and log whole pipeline, from sentence to subject-verb pairs
    Takes sentence and AllenNLP predictor
    """
    print("Original Sentence: ", sent)
    sent = preprocess_sent(sent)
    print("Processed Sentence: ", sent)
    parse = predictor.predict_json({"sentence": sent})
    tree = Tree.fromstring(parse["trees"])
    print("Tree: \n", tree)
    pairs = get_verb_subject_phrases(tree)
    print_verb_subject_phrases(pairs)

    for pair in pairs['subjects_with_verbs']:
        subject = pair['np']
        print(subject_words_from_phrase(subject))
        vp = pair['vp']
        print(verb_words_from_phrase(vp))

    pairs = subject_verb_pairs_from_tree(tree)
    print(pairs)
    return pairs

def subject_verb_pairs_are_equal(actual, expected):
    """ Evaluates two given subjects_with_verbs object to check their equality"""
    # We do some really tedious checking here because sorting this list of
    # dictionaries is otherwise sorta annoying
    equal = True
    for pair in actual:
        if pair not in expected:
            equal = False
    for pair in expected:
        if pair not in actual:
            equal = False

    if equal:
        print("PASSED $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$ ")
    else:
        print("MISMATCH !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! ")
        print("Expected:", expected)
        print("Got:", actual)
    return equal
