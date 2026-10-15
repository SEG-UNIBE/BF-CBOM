#include <stdio.h>
#include <string.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/rand.h>
#include <openssl/rsa.h>
#include <openssl/ec.h>

#define SIG_MD EVP_sha256

int main(void) {
    const unsigned char data[] = "data";
    unsigned char aes_key[32], iv[12], ct[64], legacy[64], md[EVP_MAX_MD_SIZE], tag[EVP_MAX_MD_SIZE];
    unsigned char sig[256], enc[256], derived[32], salt[16];
    unsigned int mdlen, taglen;
    size_t siglen = sizeof(sig), enclen = sizeof(enc);
    int len;
    EVP_PKEY *rsa_key = EVP_RSA_gen(2048);
    EVP_PKEY *ec_key = EVP_EC_gen("P-256");

    RAND_bytes(aes_key, sizeof(aes_key)); RAND_bytes(iv, sizeof(iv));
    EVP_CIPHER_CTX *gcm = EVP_CIPHER_CTX_new();
    EVP_EncryptInit_ex(gcm, EVP_aes_256_gcm(), NULL, aes_key, iv);
    EVP_EncryptUpdate(gcm, ct, &len, data, sizeof(data));

    EVP_CIPHER_CTX *des = EVP_CIPHER_CTX_new();
    EVP_EncryptInit_ex(des, EVP_des_ede3_cbc(), NULL, aes_key, iv);
    EVP_EncryptUpdate(des, legacy, &len, data, sizeof(data));

    EVP_Digest(data, sizeof(data), md, &mdlen, EVP_sha256(), NULL);
    EVP_Digest(data, sizeof(data), md, &mdlen, EVP_md5(), NULL);

    HMAC(EVP_sha512(), "key", 3, data, sizeof(data), tag, &taglen);

    EVP_MD_CTX *mdctx = EVP_MD_CTX_new();
    EVP_DigestSignInit(mdctx, NULL, SIG_MD(), NULL, ec_key);
    EVP_DigestSign(mdctx, sig, &siglen, data, sizeof(data));

    EVP_PKEY_CTX *pctx = EVP_PKEY_CTX_new(rsa_key, NULL);
    EVP_PKEY_encrypt_init(pctx);
    EVP_PKEY_CTX_set_rsa_padding(pctx, RSA_PKCS1_OAEP_PADDING);
    EVP_PKEY_CTX_set_rsa_oaep_md(pctx, EVP_sha256());
    EVP_PKEY_encrypt(pctx, enc, &enclen, data, sizeof(data));

    RAND_bytes(salt, sizeof(salt));
    PKCS5_PBKDF2_HMAC("pw", 2, salt, sizeof(salt), 600000, EVP_sha256(), sizeof(derived), derived);

    printf("%d %u %u %zu %zu %d\n", len, mdlen, taglen, siglen, enclen, derived[0]);
    return 0;
}
