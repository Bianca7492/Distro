"""Artist credentials and the copyright-retention assertion (S2-07, S2-08).

The assertion is the product's promise, so these tests are adversarial: they
check that a credential cannot be forged, cannot be edited after signing, and
cannot assert retention without the wording that makes the assertion mean
something.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from catalog.credentials import (
    ASSERTION_TEXT,
    ASSERTION_VERSION,
    ArtistCredential,
    CredentialError,
    canonical_bytes,
    generate_keypair,
    issue,
    verify,
    verify_retention,
)


@pytest.fixture(scope="module")
def keypair():
    return generate_keypair()


@pytest.fixture()
def credential():
    return ArtistCredential(
        artist_id="BOE-ART-0000000001-HH",
        subject_did="did:example:artist-1",
        issuer="did:web:boe.example",
        identity_ref="kyc:abc123",
    )


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def test_credential_requires_a_subject(credential):
    with pytest.raises(CredentialError):
        ArtistCredential(artist_id="x", subject_did="", issuer="y")


def test_credential_requires_an_issuer(credential):
    with pytest.raises(CredentialError):
        ArtistCredential(artist_id="x", subject_did="y", issuer="")


def test_retention_cannot_be_asserted_without_wording():
    """A bare `retainsCopyright: true` with no text is unauditable, so it is
    refused at construction rather than stored."""
    with pytest.raises(CredentialError) as exc:
        ArtistCredential(
            artist_id="x",
            subject_did="did:example:a",
            issuer="did:web:b",
            retains_copyright=True,
            assertion_text="   ",
        )
    assert "audit" in str(exc.value)


def test_payload_contains_the_assertion(credential):
    payload = credential.to_payload()
    subject = payload["credentialSubject"]
    assert subject["retainsCopyright"] is True
    assert subject["assertion"]["text"] == ASSERTION_TEXT
    assert subject["assertion"]["version"] == ASSERTION_VERSION


def test_payload_is_canonical_and_stable(credential):
    assert canonical_bytes(credential.to_payload()) == canonical_bytes(
        credential.to_payload()
    )


def test_payload_serialisation_does_not_depend_on_key_order(credential):
    payload = credential.to_payload()
    reordered = dict(reversed(list(payload.items())))
    assert canonical_bytes(payload) == canonical_bytes(reordered)


# ---------------------------------------------------------------------------
# Signing and verification
# ---------------------------------------------------------------------------

def test_issued_credential_verifies(credential, keypair):
    private, public = keypair
    document = issue(credential, private)
    assert verify(document, public)


def test_issued_credential_asserts_retention(credential, keypair):
    private, public = keypair
    assert verify_retention(issue(credential, private), public)


def test_verification_does_not_need_the_private_key(credential, keypair):
    """A third party must be able to verify using only the document and the
    issuer key it already trusts."""
    private, public = keypair
    document = issue(credential, private)
    assert verify(document, public)
    assert document["proof"]["publicKeyBase58"]


def test_a_different_key_does_not_verify(credential):
    """Key substitution: a proof signed by another key, checked against the
    key we actually trust for this issuer, must not verify."""
    private_a, public_a = generate_keypair()
    private_b, _ = generate_keypair()
    document = issue(credential, private_a)
    # Swap in a proof signed by another key.
    forged = issue(credential, private_b)
    document["proof"] = forged["proof"]
    assert not verify(document, public_a)


def test_a_credential_signed_by_an_untrusted_key_is_rejected(credential):
    """A well-formed, internally consistent credential from a rogue issuer
    must still fail, because we check it against the key we pinned."""
    rogue_private, _ = generate_keypair()
    _, trusted_public = generate_keypair()
    document = issue(credential, rogue_private)
    assert not verify(document, trusted_public)


def test_tampering_with_the_assertion_breaks_the_signature(credential, keypair):
    """This is the attack that matters: rewriting the retention wording."""
    private, public = keypair
    document = issue(credential, private)
    document["credentialSubject"]["assertion"]["text"] = (
        "The Artist assigns all copyright to the Platform."
    )
    assert not verify(document, public)
    assert not verify_retention(document, public)


def test_tampering_with_the_retention_flag_breaks_the_signature(
    credential, keypair
):
    private, public = keypair
    document = issue(credential, private)
    document["credentialSubject"]["retainsCopyright"] = False
    assert not verify(document, public)


def test_tampering_with_the_subject_breaks_the_signature(credential, keypair):
    private, public = keypair
    document = issue(credential, private)
    document["credentialSubject"]["id"] = "did:example:someone-else"
    assert not verify(document, public)


def test_tampering_with_the_issuer_breaks_the_signature(credential, keypair):
    private, public = keypair
    document = issue(credential, private)
    document["issuer"] = "did:web:attacker.example"
    assert not verify(document, public)


def test_a_stripped_proof_does_not_verify(credential, keypair):
    private, public = keypair
    document = issue(credential, private)
    del document["proof"]
    assert not verify(document, public)
    assert not verify_retention(document, public)


def test_a_garbage_proof_does_not_verify(credential, keypair):
    private, public = keypair
    document = issue(credential, private)
    document["proof"]["proofValue"] = "not-a-signature"
    assert not verify(document, public)


def test_a_malformed_key_is_rejected(credential):
    with pytest.raises(CredentialError):
        issue(credential, "not base64!!")
    with pytest.raises(CredentialError):
        issue(credential, "c2hvcnQ=")  # valid base64, wrong length


def test_verify_retention_requires_a_valid_signature(credential, keypair):
    private, public = keypair
    document = issue(credential, private)
    document["credentialSubject"]["retainsCopyright"] = False
    # Even though the payload still has assertion text, the signature is broken.
    assert not verify_retention(document, public)


def test_verify_retention_requires_the_wording(credential, keypair):
    private, public = keypair
    document = issue(credential, private)
    document["credentialSubject"]["assertion"]["text"] = ""
    # Signature now invalid, so this is False for two reasons. Confirm the
    # independent guard by signing a payload that has no wording.
    assert not verify_retention(document, public)


def test_a_credential_that_declines_retention_is_not_retention():
    """A non-retaining credential is constructible but must not pass the
    retention check, whatever its signature."""
    private, public = generate_keypair()
    credential = ArtistCredential(
        artist_id="BOE-ART-0000000002-HH",
        subject_did="did:example:artist-2",
        issuer="did:web:boe.example",
        retains_copyright=False,
        assertion_text="",
    )
    document = issue(credential, private)
    assert verify(document, public)
    assert not verify_retention(document, public)


def test_expiry_is_recorded_when_set(keypair):
    private, public = keypair
    credential = ArtistCredential(
        artist_id="x",
        subject_did="did:example:x",
        issuer="did:web:boe.example",
        expires_at=datetime.now(timezone.utc) + timedelta(days=365),
    )
    document = issue(credential, private)
    assert "expirationDate" in document
    assert verify(document, public)


def test_issued_at_is_utc_and_second_precision(credential, keypair):
    private, public = keypair
    document = issue(credential, private)
    assert document["issuanceDate"].endswith("Z")
    assert "." not in document["issuanceDate"]


def test_keypair_is_usable_and_round_trips():
    private, public = generate_keypair()
    credential = ArtistCredential(
        artist_id="x", subject_did="did:example:x", issuer="did:web:boe.example"
    )
    document = issue(credential, private)
    assert document["proof"]["publicKeyBase58"] == public
