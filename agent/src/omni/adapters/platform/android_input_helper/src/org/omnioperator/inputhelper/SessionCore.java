package org.omnioperator.inputhelper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;

/** One main-thread editor transaction. The Android wrapper owns actual UI/connection identity. */
public final class SessionCore {
  public static final class Editor {
    public final String application, resource, text;
    public final long generation, consentEpoch, bindingEpoch;
    public final int uid, selection;
    public final boolean password;

    public Editor(
        String application,
        String resource,
        String text,
        long generation,
        long consentEpoch,
        long bindingEpoch,
        int uid,
        int selection,
        boolean password) {
      this.application = application;
      this.resource = resource;
      this.text = text;
      this.generation = generation;
      this.consentEpoch = consentEpoch;
      this.uid = uid;
      this.bindingEpoch = bindingEpoch;
      this.selection = selection;
      this.password = password;
    }
  }

  public interface Port {
    Editor read() throws Exception;

    boolean append(Editor expected, String text) throws Exception;

    boolean consent();

    long now();
  }

  private final Port port;
  private Editor original;
  private String token;
  private long deadline;
  private boolean dispatched;
  public String verifiedHash;
  public int verifiedLength;

  public SessionCore(Port port) {
    this.port = port;
  }

  public static String sha(String value) throws Exception {
    byte[] bytes =
        MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
    StringBuilder result = new StringBuilder();
    for (byte b : bytes) result.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
    return result.toString();
  }

  public static void text(String value) {
    if (value == null
        || value.codePointCount(0, value.length()) < 1
        || value.codePointCount(0, value.length()) > 256)
      throw new IllegalArgumentException("text_limit");
    for (int i = 0; i < value.length(); ) {
      int point = value.codePointAt(i);
      if (point < 32 || (point >= 127 && point <= 159) || (point >= 0xd800 && point <= 0xdfff))
        throw new IllegalArgumentException("text_invalid");
      i += Character.charCount(point);
    }
  }

  private boolean same(Editor a, Editor b) {
    return a.application.equals(b.application)
        && a.resource.equals(b.resource)
        && a.uid == b.uid
        && a.bindingEpoch == b.bindingEpoch
        && a.consentEpoch == b.consentEpoch
        && !b.password;
  }

  public String prepare(String application, String resource, String beforeHash, int user)
      throws Exception {
    revoke();
    if (!port.consent()) throw new IllegalStateException("consent_required");
    Editor e = port.read();
    if (e.password
        || e.uid / 100000 != user
        || !e.application.equals(application)
        || resource.isEmpty()
        || !e.resource.equals(resource)
        || e.text.length() > 4096
        || e.selection != e.text.length()
        || !sha(e.text).equals(beforeHash)) throw new IllegalStateException("editor_changed");
    original = e;
    token = UUID.randomUUID().toString();
    deadline = port.now() + 3000;
    return token;
  }

  public String commit(String permit, String value) {
    String current = token;
    token = null;
    dispatched = false;
    verifiedHash = null;
    verifiedLength = 0;
    try {
      text(value);
      if (current == null || !current.equals(permit) || port.now() > deadline || !port.consent())
        return "denied";
      Editor e = port.read();
      if (!same(original, e)
          || e.generation != original.generation
          || e.consentEpoch != original.consentEpoch
          || !e.text.equals(original.text)
          || e.selection != e.text.length()
          || port.now() > deadline
          || !port.consent()) return "denied";
      dispatched = true;
      if (!port.append(e, value)) return "unknown";
      Editor after = port.read();
      if (same(original, after)
          && after.text.equals(original.text + value)
          && after.selection == after.text.length()) {
        verifiedHash = sha(after.text);
        verifiedLength = after.text.length();
        return "verified";
      }
      return "unknown";
    } catch (Exception ignored) {
      return dispatched ? "unknown" : "denied";
    }
  }

  public void revoke() {
    original = null;
    token = null;
    dispatched = false;
    deadline = 0;
    verifiedHash = null;
    verifiedLength = 0;
  }
}
