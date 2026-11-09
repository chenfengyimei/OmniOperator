package org.omnioperator.inputhelper;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.json.JSONObject;

/** HMAC authenticates literal UTF-8 frames, not a language-specific JSON serialization. */
final class Wire {
  static byte[] mac(byte[] key, byte[] bytes) throws Exception {
    Mac mac = Mac.getInstance("HmacSHA256");
    mac.init(new SecretKeySpec(key, "HmacSHA256"));
    return mac.doFinal(bytes);
  }

  static byte[] prefixed(String prefix, byte[] body) {
    byte[] head = prefix.getBytes(StandardCharsets.UTF_8),
        result = new byte[head.length + body.length];
    System.arraycopy(head, 0, result, 0, head.length);
    System.arraycopy(body, 0, result, head.length, body.length);
    return result;
  }

  static JSONObject read(DataInputStream input, byte[] key) throws Exception {
    int length = input.readInt();
    if (length < 1 || length > 16384) throw new IllegalArgumentException("frame_limit");
    byte[] body = new byte[length], signature = new byte[32];
    input.readFully(body);
    input.readFully(signature);
    if (!MessageDigest.isEqual(signature, mac(key, prefixed("omni-input-request-1\n", body))))
      throw new SecurityException("authentication_failed");
    JSONObject value = new JSONObject(new String(body, StandardCharsets.UTF_8));
    if (value.getInt("version") != 1 || !value.getString("nonce").matches("[0-9a-f]{32}"))
      throw new IllegalArgumentException("frame_identity");
    return value;
  }

  static void write(DataOutputStream output, JSONObject value, byte[] key, String nonce)
      throws Exception {
    value.put("version", 1);
    value.put("nonce", nonce);
    byte[] body = value.toString().getBytes(StandardCharsets.UTF_8);
    if (body.length > 16384) throw new IllegalArgumentException("frame_limit");
    output.writeInt(body.length);
    output.write(body);
    output.write(mac(key, prefixed("omni-input-response-1\n" + nonce + "\n", body)));
    output.flush();
  }
}
