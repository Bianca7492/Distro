"""Artist credentials (S2-07, S2-08).

A Verifiable Credential per artist, signed by a platform-controlled issuer key,
carrying the artist's DID, their legal identity reference, and — critically —
the **copyright retention assertion**.

That assertion is the product's whole promise: the artist retains copyright and
metadata autonomy, and the distribution terms contain no IP assignment. It is
legally load-bearing, so two things follow:

  1. The exact wording is stored, not just a boolean. Counsel reviews wording,
     and a flag cannot be audited after the fact.
  2. Signing is a real Ed25519 signature over the canonical payload. A credential
     that cannot be verified is decoration.

The issuer key is supplied by the caller. This module never generates or stores
a private key: key custody belongs to the service that issues credentials, and
a key in application code is a key that leaks.

The signing algorithm is Ed25519 (EdDSA), via `cryptography`. Verification is
deterministic and does not need the private key, so an artist or a third party
can verify a credential we issued.
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

# The assertion wording. Changing this text is a legal change, not a code
# change: it needs review, and existing credentials keep the version they were
# signed with.
ASSERTION_VERSION = "1.0"

ASSERTION_TEXT = (
    "The Artist retains all copyright in the Works and sound recordings "
    "delivered under this agreement, together with the right to control the "
    "metadata that describes them. Nothing in these distribution terms "
    "assigns, transfers or exclusively licenses that copyright to the "
    "Platform. The Platform is granted only the non-exclusive right to "
    "distribute the delivered material to the delivery targets the Artist "
    "selects, for as long as the Artist permits, and that right ends when the "
    "Artist withdraws the material."
)


class CredentialError(ValueError):
    """A credential cannot be built or verified."""


@dataclass
class ArtistCredential:
    """The unsigned content of an artist credential."""

    artist_id: str
    subject_did: str
    issuer: str
    identity_ref: str | None = None
    retains_copyright: bool = True
    assertion_text: str = ASSERTION_TEXT
    assertion_version: str = ASSERTION_VERSION
    credential_type: str = "ArtistCredential"
    issued_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.subject_did:
            raise CredentialError("subject_did is required")
        if not self.issuer:
            raise CredentialError("issuer is required")
        if not self.artist_id:
            raise CredentialError("artist_id is required")
        # A credential asserting retention without wording would be worthless:
        # there would be nothing to point at when it is disputed.
        if self.retains_copyright and not self.assertion_text.strip():
            raise CredentialError(
                "retains_copyright requires the assertion wording; a flag "
                "alone cannot be audited"
            )

    def to_payload(self) -> dict:
        """The canonical payload that gets signed.

        Keys are sorted and separators fixed so the bytes are identical
        wherever the payload is rebuilt. Signing a dict whose serialisation
        varies would produce signatures that do not verify.
        """
        payload = {
            "@context": [
                "https://www.w3.org/2018/credentials/v1",
                "https://boe.example/credentials/artist/v1",
            ],
            "type": ["VerifiableCredential", self.credential_type],
            "issuer": self.issuer,
            "issuanceDate": self.issued_at.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "credentialSubject": {
                "id": self.subject_did,
                "artistId": self.artist_id,
                "identityRef": self.identity_ref,
                "retainsCopyright": self.retains_copyright,
                "assertion": {
                    "text": self.assertion_text,
                    "version": self.assertion_version,
                },
            },
        }
        if self.expires_at is not None:
            payload["expirationDate"] = (
                self.expires_at.astimezone(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
        return payload


def canonical_bytes(payload: dict) -> bytes:
    """Deterministic serialisation of a payload, for signing and verifying."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _load_ed25519():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise CredentialError(
            "credential signing needs the 'cryptography' package"
        ) from exc
    return ed25519


def generate_keypair() -> tuple[str, str]:
    """Generate an Ed25519 keypair as (private_b64, public_b64).

    Provided for tests and for the issuer service to bootstrap a key. The
    private key must be held in a secret manager, never in configuration files
    and never in this repository.
    """
    ed25519 = _load_ed25519()
    from cryptography.hazmat.primitives import serialization

    private = ed25519.Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(
        private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode()
    public_b64 = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode()
    return private_b64, public_b64


def issue(
    credential: ArtistCredential,
    private_key_b64: str,
    verification_method: str | None = None,
) -> dict:
    """Sign a credential and return the full verifiable document.

    The returned dict is what gets stored in `artist_credentials.payload`, with
    the signature stored alongside it.
    """
    ed25519 = _load_ed25519()
    from cryptography.hazmat.primitives import serialization

    try:
        raw = base64.b64decode(private_key_b64, validate=True)
    except Exception as exc:
        raise CredentialError("private key is not valid base64") from exc

    try:
        private = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
    except Exception as exc:
        raise CredentialError(
            "private key is not a valid Ed25519 key"
        ) from exc

    payload = credential.to_payload()
    signature = private.sign(canonical_bytes(payload))

    # Derive the public key so the proof is self-describing.
    public_b64 = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode()

    document = dict(payload)
    document["proof"] = {
        "type": "Ed25519Signature2020",
        "created": payload["issuanceDate"],
        "proofPurpose": "assertionMethod",
        "verificationMethod": verification_method
        or f"{credential.issuer}#key-1",
        "publicKeyBase58": public_b64,
        "proofValue": base64.b64encode(signature).decode(),
    }
    return document


def verify(document: dict, public_key_b64: str) -> bool:
    """Verify a signed credential against a trusted public key.

    `public_key_b64` is required, and deliberately so. The proof block carries
    its own `publicKeyBase58`, which is convenient for transport but useless as
    a trust anchor: a signature that is checked against the key it travels with
    verifies no matter who produced it. That is key substitution — anyone can
    issue a credential asserting anything and it would pass.

    The caller supplies the key it trusts for this issuer. The embedded key must
    match it, and the signature must verify under it.
    """
    ed25519 = _load_ed25519()

    proof = document.get("proof")
    if not isinstance(proof, dict):
        return False
    proof_value = proof.get("proofValue")
    embedded_key = proof.get("publicKeyBase58")
    if not proof_value or not embedded_key:
        return False

    # The embedded key must be the trusted one. A mismatch means either a
    # rogue issuer or a swapped proof.
    if not secrets.compare_digest(str(embedded_key), str(public_key_b64)):
        return False

    payload = {k: v for k, v in document.items() if k != "proof"}

    try:
        signature = base64.b64decode(proof_value, validate=True)
        raw_public = base64.b64decode(public_key_b64, validate=True)
        public = ed25519.Ed25519PublicKey.from_public_bytes(raw_public)
        public.verify(signature, canonical_bytes(payload))
    except Exception:
        return False
    return True


def verify_retention(document: dict, public_key_b64: str) -> bool:
    """True when the credential is valid *and* asserts copyright retention.

    Both halves matter. A valid credential that dropped the assertion would
    otherwise pass a check that only looked at the signature.
    """
    if not verify(document, public_key_b64):
        return False
    subject = document.get("credentialSubject")
    if not isinstance(subject, dict):
        return False
    if subject.get("retainsCopyright") is not True:
        return False
    assertion = subject.get("assertion")
    if not isinstance(assertion, dict):
        return False
    return bool(str(assertion.get("text", "")).strip())
