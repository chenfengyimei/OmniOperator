package org.omnioperator.inputhelper;

import android.content.Context;
import android.os.SystemClock;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.security.KeyStore;
import java.security.SecureRandom;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** App-private pairing key encrypted by AndroidKeyStore. Consent lives only in this process. */
final class Pairing {
  static volatile long consentUntil = 0, epoch = 0;

  static synchronized void pause() {
    consentUntil = 0;
    epoch++;
  }

  static synchronized void allow() {
    consentUntil = SystemClock.elapsedRealtime() + 30 * 60 * 1000;
    epoch++;
  }

  static boolean consent() {
    return SystemClock.elapsedRealtime() < consentUntil;
  }

  private static SecretKey wrapping() throws Exception {
    String alias = "omni-input-pairing-1";
    KeyStore store = KeyStore.getInstance("AndroidKeyStore");
    store.load(null);
    if (!store.containsAlias(alias)) {
      KeyGenerator generator =
          KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
      generator.init(
          new KeyGenParameterSpec.Builder(
                  alias, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
              .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
              .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
              .build());
      generator.generateKey();
    }
    return (SecretKey) store.getKey(alias, null);
  }

  static byte[] get(Context context) throws Exception {
    String stored =
        context.getSharedPreferences("pairing", Context.MODE_PRIVATE).getString("encrypted", null);
    if (stored == null) throw new IllegalStateException("pairing_required");
    byte[] value = Base64.decode(stored, Base64.NO_WRAP);
    if (value.length != 12 + 32 + 16) throw new IllegalStateException("pairing_invalid");
    Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
    cipher.init(Cipher.DECRYPT_MODE, wrapping(), new GCMParameterSpec(128, value, 0, 12));
    byte[] result = cipher.doFinal(value, 12, value.length - 12);
    if (result.length != 32) throw new IllegalStateException("pairing_invalid");
    return result;
  }

  static String rotate(Context context) throws Exception {
    pause();
    byte[] key = new byte[32];
    new SecureRandom().nextBytes(key);
    Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
    cipher.init(Cipher.ENCRYPT_MODE, wrapping());
    byte[] encrypted = cipher.doFinal(key), value = new byte[12 + encrypted.length];
    System.arraycopy(cipher.getIV(), 0, value, 0, 12);
    System.arraycopy(encrypted, 0, value, 12, encrypted.length);
    if (!context
        .getSharedPreferences("pairing", Context.MODE_PRIVATE)
        .edit()
        .putString("encrypted", Base64.encodeToString(value, Base64.NO_WRAP))
        .commit()) throw new IllegalStateException("pairing_write_failed");
    return Base64.encodeToString(key, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
  }
}
