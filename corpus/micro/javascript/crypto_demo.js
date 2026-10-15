'use strict';
const crypto = require('node:crypto');

const SIG_ALG = 'sha256';

function main() {
  const data = Buffer.from('data');

  const rsa = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  const ec = crypto.generateKeyPairSync('ec', { namedCurve: 'prime256v1' });

  const aesKey = crypto.randomBytes(32);
  const iv = crypto.randomBytes(12);
  const gcm = crypto.createCipheriv('aes-256-gcm', aesKey, iv);
  const ct = Buffer.concat([gcm.update(data), gcm.final()]);

  const des = crypto.createCipheriv('des-ede3-cbc', crypto.randomBytes(24), crypto.randomBytes(8));
  const legacy = Buffer.concat([des.update(data), des.final()]);

  const digest = crypto.createHash('sha256').update(data).digest('hex');
  const weak = crypto.createHash('md5').update(data).digest('hex');

  const tag = crypto.createHmac('sha512', 'key').update(data).digest();

  const sig = crypto.sign(SIG_ALG, data, ec.privateKey);

  const enc = crypto.publicEncrypt(
    { key: rsa.publicKey, padding: crypto.constants.RSA_PKCS1_OAEP_PADDING, oaepHash: 'sha256' },
    data,
  );

  const salt = crypto.randomBytes(16);
  const derived = crypto.pbkdf2Sync('pw', salt, 600000, 32, 'sha256');

  console.log(ct, legacy, digest, weak, tag, sig, enc, derived);
}

main();
