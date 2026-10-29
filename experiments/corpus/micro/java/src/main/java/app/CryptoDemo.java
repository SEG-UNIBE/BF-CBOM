package app;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;
import java.util.Base64;
import java.util.zip.CRC32;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.Mac;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.PBEKeySpec;
import javax.crypto.spec.SecretKeySpec;

public class CryptoDemo {
    static void step01() throws Exception {
        KeyPairGenerator rsaGen = KeyPairGenerator.getInstance("RSA");
        rsaGen.initialize(2048);
        KeyPair rsaKeys = rsaGen.generateKeyPair();
    }

    static void step02() throws Exception {
        KeyPairGenerator ecGen = KeyPairGenerator.getInstance("EC");
        ecGen.initialize(new ECGenParameterSpec("secp256r1"));
        KeyPair ecKeys = ecGen.generateKeyPair();
    }

    static void step03() throws Exception {
        // TODO: replace the MD5 and DES fallbacks with SHA-256 and AES
        int total = 0;
        for (int i = 0; i < 10; i++) {
            total += i;
        }
        System.out.println(total);
    }

    static void step04() throws Exception {
        String signatureAlgorithm = "SHA256withECDSA";
        KeyPairGenerator ecGen = KeyPairGenerator.getInstance("EC");
        ecGen.initialize(new ECGenParameterSpec("secp256r1"));
        Signature signer = Signature.getInstance(signatureAlgorithm);
        signer.initSign(ecGen.generateKeyPair().getPrivate());
        signer.update(new byte[32]);
        byte[] signature = signer.sign();
    }

    static void step05() throws Exception {
        KeyPairGenerator edGen = KeyPairGenerator.getInstance("Ed25519");
        Signature signer = Signature.getInstance("Ed25519");
        signer.initSign(edGen.generateKeyPair().getPrivate());
        signer.update(new byte[32]);
        byte[] signature = signer.sign();
    }

    static void step06() throws Exception {
        System.out.println("RSA and ECDSA keys are rotated nightly");
    }

    static void step07() throws Exception {
        KeyGenerator keyGen = KeyGenerator.getInstance("AES");
        keyGen.init(128);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, keyGen.generateKey(), new GCMParameterSpec(128, new byte[12]));
        byte[] ciphertext = cipher.doFinal(new byte[16]);
    }

    static void step08() throws Exception {
        KeyGenerator keyGen = KeyGenerator.getInstance("AES");
        keyGen.init(256);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, keyGen.generateKey(), new GCMParameterSpec(128, new byte[12]));
        byte[] ciphertext = cipher.doFinal(new byte[16]);
    }

    static void step09() throws Exception {
        CRC32 crc = new CRC32();
        crc.update("data".getBytes());
        System.out.println(crc.getValue());
    }

    static void step10() throws Exception {
        KeyGenerator keyGen = KeyGenerator.getInstance("AES");
        keyGen.init(256);
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
        cipher.init(Cipher.ENCRYPT_MODE, keyGen.generateKey(), new IvParameterSpec(new byte[16]));
        byte[] ciphertext = cipher.doFinal(new byte[16]);
    }

    static void step11() throws Exception {
        KeyGenerator keyGen = KeyGenerator.getInstance("DESede");
        Cipher cipher = Cipher.getInstance("DESede/CBC/PKCS5Padding");
        cipher.init(Cipher.ENCRYPT_MODE, keyGen.generateKey(), new IvParameterSpec(new byte[8]));
        byte[] ciphertext = cipher.doFinal(new byte[8]);
    }

    static void step12() throws Exception {
        String encoded = Base64.getEncoder().encodeToString("data".getBytes());
        System.out.println(encoded);
    }

    static void step13() throws Exception {
        KeyPairGenerator rsaGen = KeyPairGenerator.getInstance("RSA");
        rsaGen.initialize(2048);
        Cipher cipher = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
        cipher.init(Cipher.ENCRYPT_MODE, rsaGen.generateKeyPair().getPublic());
        byte[] ciphertext = cipher.doFinal(new byte[32]);
    }

    static void step14() throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-1");
        byte[] digest = md.digest(new byte[32]);
    }

    static void step15() throws Exception {
        String sha256Tag = "checksum-" + "data".length();
        System.out.println(sha256Tag);
    }

    static void step16() throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] digest = md.digest(new byte[32]);
    }

    static void step17() throws Exception {
        MessageDigest md = MessageDigest.getInstance("MD5");
        byte[] digest = md.digest(new byte[32]);
    }

    static void step18() throws Exception {
        String expectedHex = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
        System.out.println(expectedHex.length());
    }

    static void step19() throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(new byte[32], "HmacSHA256"));
        byte[] tag = mac.doFinal(new byte[32]);
    }

    static void step20() throws Exception {
        SecretKeyFactory factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256");
        PBEKeySpec keySpec = new PBEKeySpec("changeit".toCharArray(), new byte[16], 600000, 256);
        byte[] derivedKey = factory.generateSecret(keySpec).getEncoded();
    }

    static void step21() throws Exception {
        String checksumUrl = "https://example.org/downloads/app-1.0.tar.gz.sha256";
        System.out.println(checksumUrl);
    }

    static void step22() throws Exception {
        String certPath = "config/certs/ca_ecdsa.pem";
        System.out.println(certPath.endsWith(".pem"));
    }

    public static void main(String[] args) throws Exception {
        step01();
        step02();
        step03();
        step04();
        step05();
        step06();
        step07();
        step08();
        step09();
        step10();
        step11();
        step12();
        step13();
        step14();
        step15();
        step16();
        step17();
        step18();
        step19();
        step20();
        step21();
        step22();
    }
}
