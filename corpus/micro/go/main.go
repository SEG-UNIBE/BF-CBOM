package main

import (
	"crypto"
	"crypto/aes"
	"crypto/cipher"
	"crypto/des"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/hmac"
	"crypto/md5"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/sha512"
	"fmt"

	"golang.org/x/crypto/pbkdf2"
)

const SigHash = crypto.SHA256

func main() {
	data := []byte("data")

	rsaKey, _ := rsa.GenerateKey(rand.Reader, 2048)
	ecKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)

	aesKey := make([]byte, 32)
	rand.Read(aesKey)
	block, _ := aes.NewCipher(aesKey)
	gcm, _ := cipher.NewGCM(block)
	nonce := make([]byte, gcm.NonceSize())
	ct := gcm.Seal(nil, nonce, data, nil)

	desBlock, _ := des.NewTripleDESCipher(make([]byte, 24))
	cbc := cipher.NewCBCEncrypter(desBlock, make([]byte, 8))
	legacy := make([]byte, 8)
	cbc.CryptBlocks(legacy, []byte("8bytes!!"))

	digest := sha256.Sum256(data)
	weak := md5.Sum(data)

	mac := hmac.New(sha512.New, []byte("key"))
	mac.Write(data)
	tag := mac.Sum(nil)

	h := SigHash.New()
	h.Write(data)
	sig, _ := ecdsa.SignASN1(rand.Reader, ecKey, h.Sum(nil))

	enc, _ := rsa.EncryptOAEP(sha256.New(), rand.Reader, &rsaKey.PublicKey, data, nil)

	salt := make([]byte, 16)
	rand.Read(salt)
	derived := pbkdf2.Key([]byte("pw"), salt, 600000, 32, sha256.New)

	fmt.Println(ct, legacy, digest, weak, tag, sig, enc, derived)
}
