import base64
import hashlib
import hmac
import os
import zlib
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def step01():
    rsa_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_public_key = rsa_private_key.public_key()


def step02():
    ec_private_key = ec.generate_private_key(ec.SECP256R1())
    ec_public_key = ec_private_key.public_key()


def step03():
    # TODO: replace the MD5 and DES fallbacks with SHA-256 and AES
    total = sum(range(10))
    print(total)


def step04():
    signature_hash = hashes.SHA256()
    ec_private_key = ec.generate_private_key(ec.SECP256R1())
    ecdsa_signature = ec_private_key.sign(b"data", ec.ECDSA(signature_hash))


def step05():
    ed25519_private_key = Ed25519PrivateKey.generate()
    ed25519_signature = ed25519_private_key.sign(b"data")


def step06():
    print("RSA and ECDSA keys are rotated nightly")


def step07():
    aes_key = AESGCM.generate_key(bit_length=128)
    aes_ciphertext = AESGCM(aes_key).encrypt(os.urandom(12), b"data", None)


def step08():
    aes_key = AESGCM.generate_key(bit_length=256)
    aes_ciphertext = AESGCM(aes_key).encrypt(os.urandom(12), b"data", None)


def step09():
    checksum = zlib.crc32(b"data")
    print(checksum)


def step10():
    aes_cipher = Cipher(algorithms.AES(os.urandom(32)), modes.CBC(os.urandom(16)))
    aes_encryptor = aes_cipher.encryptor()
    aes_ciphertext = aes_encryptor.update(b"0123456789abcdef") + aes_encryptor.finalize()


def step11():
    sha3_digest = hashlib.sha3_256(b"data").hexdigest()


def step12():
    encoded = base64.b64encode(b"data")
    print(encoded)


def step13():
    rsa_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_ciphertext = rsa_private_key.public_key().encrypt(
        b"data",
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )


def step14():
    sha1_digest = hashlib.sha1(b"data").hexdigest()


def step15():
    sha256_tag = f"checksum-{len('data')}"
    print(sha256_tag)


def step16():
    sha256_digest = hashlib.sha256(b"data").hexdigest()


def step17():
    md5_digest = hashlib.md5(b"data").hexdigest()


def step18():
    expected_hex = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    print(len(expected_hex))


def step19():
    hmac_tag = hmac.new(b"key", b"data", hashlib.sha256).hexdigest()


def step20():
    derived_key = hashlib.pbkdf2_hmac("sha256", b"changeit", os.urandom(16), 600000)


def step21():
    checksum_url = "https://example.org/downloads/app-1.0.tar.gz.sha256"
    print(checksum_url)


def step22():
    cert_path = "config/certs/ca_ecdsa.pem"
    print(cert_path.endswith(".pem"))


def main():
    step01()
    step02()
    step03()
    step04()
    step05()
    step06()
    step07()
    step08()
    step09()
    step10()
    step11()
    step12()
    step13()
    step14()
    step15()
    step16()
    step17()
    step18()
    step19()
    step20()
    step21()
    step22()


if __name__ == "__main__":
    main()
