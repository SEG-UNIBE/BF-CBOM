import hashlib
import hmac
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

HASH = hashes.SHA256()


def main():
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ec_key = ec.generate_private_key(ec.SECP256R1())

    aes_key = AESGCM.generate_key(bit_length=256)
    ct = AESGCM(aes_key).encrypt(os.urandom(12), b"data", None)

    legacy = Cipher(algorithms.TripleDES(os.urandom(24)), modes.CBC(os.urandom(8)))

    digest = hashlib.sha256(b"data").hexdigest()
    weak = hashlib.md5(b"data").hexdigest()

    tag = hmac.new(b"key", b"data", hashlib.sha512).digest()

    sig = ec_key.sign(b"data", ec.ECDSA(HASH))

    enc = rsa_key.public_key().encrypt(
        b"data",
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=os.urandom(16), iterations=600000)
    derived = kdf.derive(b"pw")


if __name__ == "__main__":
    main()
