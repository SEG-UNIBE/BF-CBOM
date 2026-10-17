package app;

import java.security.*;
import java.security.spec.ECGenParameterSpec;
import javax.crypto.*;
import javax.crypto.spec.*;

public class CryptoDemo {
    private static final String SIG_ALG = "SHA256withECDSA";

    public static void main(String[] args) throws Exception {
        KeyPairGenerator rsaGen = KeyPairGenerator.getInstance("RSA");
        rsaGen.initialize(2048);
        KeyPair rsa = rsaGen.generateKeyPair();

        KeyPairGenerator ecGen = KeyPairGenerator.getInstance("EC");
        ecGen.initialize(new ECGenParameterSpec("secp256r1"));
        KeyPair ec = ecGen.generateKeyPair();

        KeyGenerator aesGen = KeyGenerator.getInstance("AES");
        aesGen.init(256);
        SecretKey aesKey = aesGen.generateKey();

        Cipher gcm = Cipher.getInstance("AES/GCM/NoPadding");
        gcm.init(Cipher.ENCRYPT_MODE, aesKey, new GCMParameterSpec(128, new byte[12]));

        Cipher legacy = Cipher.getInstance("DES/CBC/PKCS5Padding");

        MessageDigest sha = MessageDigest.getInstance("SHA-256");
        MessageDigest md5 = MessageDigest.getInstance("MD5");

        Mac mac = Mac.getInstance("HmacSHA512");

        Signature sig = Signature.getInstance(SIG_ALG);
        sig.initSign(ec.getPrivate());

        Cipher oaep = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
        oaep.init(Cipher.ENCRYPT_MODE, rsa.getPublic());

        SecretKeyFactory pbkdf2 = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256");
        pbkdf2.generateSecret(new PBEKeySpec("pw".toCharArray(), new byte[16], 600000, 256));
    }
}
