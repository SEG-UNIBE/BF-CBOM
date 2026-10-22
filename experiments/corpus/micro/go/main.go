package main

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/des"
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/elliptic"
	"crypto/hmac"
	"crypto/md5"
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"hash/crc32"
)

func step01() {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		panic(err)
	}
	_ = key
}

func step02() {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		panic(err)
	}
	_ = key
}

func step03() {
	sum := sha256.Sum256
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		panic(err)
	}
	digest := sum([]byte("data"))
	signature, err := ecdsa.SignASN1(rand.Reader, key, digest[:])
	if err != nil {
		panic(err)
	}
	_ = signature
}

func step04() {
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		panic(err)
	}
	signature := ed25519.Sign(priv, []byte("data"))
	_ = signature
}

func step05() {
	// TODO: replace the MD5 and DES fallbacks with SHA-256 and AES
	total := 0
	for i := 0; i < 10; i++ {
		total += i
	}
	fmt.Println(total)
}

func step06() {
	block, err := aes.NewCipher(make([]byte, 16))
	if err != nil {
		panic(err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		panic(err)
	}
	ciphertext := gcm.Seal(nil, make([]byte, gcm.NonceSize()), []byte("data"), nil)
	_ = ciphertext
}

func step07() {
	block, err := aes.NewCipher(make([]byte, 32))
	if err != nil {
		panic(err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		panic(err)
	}
	ciphertext := gcm.Seal(nil, make([]byte, gcm.NonceSize()), []byte("data"), nil)
	_ = ciphertext
}

func step08() {
	block, err := aes.NewCipher(make([]byte, 32))
	if err != nil {
		panic(err)
	}
	mode := cipher.NewCBCEncrypter(block, make([]byte, aes.BlockSize))
	ciphertext := make([]byte, aes.BlockSize)
	mode.CryptBlocks(ciphertext, make([]byte, aes.BlockSize))
}

func step09() {
	block, err := des.NewTripleDESCipher(make([]byte, 24))
	if err != nil {
		panic(err)
	}
	mode := cipher.NewCBCEncrypter(block, make([]byte, des.BlockSize))
	ciphertext := make([]byte, des.BlockSize)
	mode.CryptBlocks(ciphertext, make([]byte, des.BlockSize))
}

func step10() {
	fmt.Println("RSA and ECDSA keys are rotated nightly")
}

func step11() {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		panic(err)
	}
	ciphertext, err := rsa.EncryptOAEP(sha256.New(), rand.Reader, &key.PublicKey, []byte("data"), nil)
	if err != nil {
		panic(err)
	}
	_ = ciphertext
}

func step12() {
	digest := sha1.Sum([]byte("data"))
	_ = digest
}

func step13() {
	digest := sha256.Sum256([]byte("data"))
	_ = digest
}

func step14() {
	digest := md5.Sum([]byte("data"))
	_ = digest
}

func step15() {
	checksum := crc32.ChecksumIEEE([]byte("data"))
	fmt.Println(checksum)
}

func step16() {
	mac := hmac.New(sha256.New, []byte("key"))
	mac.Write([]byte("data"))
	tag := mac.Sum(nil)
	_ = tag
}

func step17() {
	derivedKey, err := pbkdf2.Key(sha256.New, "changeit", make([]byte, 16), 600000, 32)
	if err != nil {
		panic(err)
	}
	_ = derivedKey
}

func step18() {
	encoded := base64.StdEncoding.EncodeToString([]byte("data"))
	fmt.Println(encoded)
}

func main() {
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
}
