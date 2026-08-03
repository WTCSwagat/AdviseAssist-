"""
PII scrubbing for Advise Assist. Excerpt from a private codebase.

Runs on every student email before any text reaches a language model. Two layers:
regex for structured identifiers, then spaCy NER for the unstructured ones.

The hard part is not redaction, it is disambiguation. A student email is full of
numbers that look alike: "drop by 3/15" is a deadline, "scored 45/100" is a grade,
"my GPA fell to 1.8" is protected data. Blanking all of them leaves the model
unable to answer; blanking none of them leaks the record.
"""

import re

import spacy

nlp = spacy.load("en_core_web_sm")


def bucket_gpa(match):
    """Convert a GPA into a severity band instead of redacting it entirely.

    The model needs to know the student is in academic trouble in order to draft a
    useful reply. It does not need the number. Bucketing keeps the signal and drops
    the protected value.
    """
    gpa = float(match.group(1))
    if gpa < 1.5:
        return "[GPA: critically low]"
    if gpa < 2.0:
        return "[GPA: below standing]"
    if gpa < 2.5:
        return "[GPA: marginal]"
    return "[GPA: standard]"


def scrub_pii(text: str) -> str:
    # Layer 1: regex for structured PII.

    # SSN before the bare 9-digit ID rule, so it isn't half-matched.
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN]", text)

    # Date of birth requires a birth-related word right before the date, so policy
    # deadlines ("drop by 3/15") survive untouched.
    text = re.sub(
        r"\b(born|birth\s*date|DOB|date of birth)\b[^0-9\n]{0,15}"
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
        "[DOB]",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(r"\b\d{9}\b", "[STUDENT_ID]", text)

    # GPA, two phrasings. Allow a few words between "GPA" and the number
    # ("GPA dropped to 1.8"), but stop before crossing another digit so the wrong
    # value isn't captured.
    text = re.sub(
        r"GPA\b[^0-4\n]{0,25}?([0-4]\.\d{1,2})",
        lambda match: bucket_gpa(match),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b([0-4]\.\d{1,2})\s*(GPA|grade point)",
        lambda match: bucket_gpa(match),
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(r"[\w\.-]+@[\w\.-]+\.\w+", "[EMAIL]", text)

    # (?<!\d)...(?!\d) rather than \b, so the leading "(" of "(865)" is included.
    text = re.sub(
        r"(?<!\d)(\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)",
        "[PHONE]",
        text,
    )

    text = re.sub(r"\$[\d,]+(\.\d{2})?", "[AMOUNT]", text)

    # Grade fractions, but only with a grade verb nearby ("got a 45/100"), so a
    # deadline like "3/15/2025" isn't mistaken for a grade. The prefix word is kept
    # and only the fraction is redacted, so the sentence still reads.
    text = re.sub(
        r"(\b(?:scored?|got|grade[ds]?|earned|received|made|mark[eds]*)\b[^/\n0-9]{0,15})"
        r"\d{1,3}\s*(?:/|out of)\s*\d{1,3}\b",
        r"\1[GRADE]",
        text,
        flags=re.IGNORECASE,
    )

    # Layer 2: spaCy NER for unstructured PII (names, organizations).
    #
    # Layer 1 has already inserted placeholders like [STUDENT_ID]. NER will happily
    # tag those as entities and double-redact them into nonsense, so track their
    # spans and skip any entity that overlaps one.
    placeholder_spans = [
        (match.start(), match.end()) for match in re.finditer(r"\[[^\]]+\]", text)
    ]

    doc = nlp(text)
    redacted = text

    # Reversed, so replacing an entity never invalidates the offsets of the ones
    # still to be processed.
    for ent in reversed(doc.ents):
        overlaps_placeholder = any(
            ent.start_char < end and ent.end_char > start
            for start, end in placeholder_spans
        )
        if overlaps_placeholder:
            continue
        if ent.label_ in ["PERSON", "ORG"]:
            redacted = (
                redacted[: ent.start_char]
                + f"[{ent.label_}]"
                + redacted[ent.end_char :]
            )

    return redacted
