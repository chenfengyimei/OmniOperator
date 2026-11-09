package org.omnioperator.inputhelper;

/**
 * JVM checks for the exact core compiled into the APK. Android binding still requires device
 * acceptance.
 */
public final class SessionCoreChecks {
  private static int checks = 0;

  private static void check(boolean condition) {
    checks++;
    if (!condition) throw new AssertionError("core_contract_" + checks);
  }

  private static final class EditorPort implements SessionCore.Port {
    String text = "hello", resource = "org.fixture:id/input";
    int uid = 12345, writes = 0, cursor = 5;
    long time = 0, generation = 1, consentEpoch = 1, bindingEpoch = 1;
    boolean allowed = true,
        password = false,
        refuse = false,
        unreadableAfter = false,
        rebindAfter = false;

    public SessionCore.Editor read() throws Exception {
      if (writes > 0 && unreadableAfter) throw new IllegalStateException("offline");
      return new SessionCore.Editor(
          "org.fixture",
          resource,
          text,
          generation,
          consentEpoch,
          bindingEpoch,
          uid,
          cursor,
          password);
    }

    public boolean append(SessionCore.Editor expected, String value) {
      writes++;
      if (refuse) return false;
      text += value;
      cursor = text.length();
      generation++;
      if (rebindAfter) bindingEpoch++;
      return true;
    }

    public boolean consent() {
      return allowed;
    }

    public long now() {
      return time;
    }
  }

  private static String prepare(SessionCore core, EditorPort port) throws Exception {
    return core.prepare("org.fixture", "org.fixture:id/input", SessionCore.sha(port.text), 0);
  }

  private static void deniedPrepare(EditorPort port) throws Exception {
    SessionCore core = new SessionCore(port);
    boolean denied = false;
    try {
      prepare(core, port);
    } catch (IllegalStateException expected) {
      denied = true;
    }
    check(denied && port.writes == 0);
  }

  public static void main(String[] args) throws Exception {
    EditorPort port = new EditorPort();
    SessionCore core = new SessionCore(port);
    String token = prepare(core, port);
    check(core.commit(token, "中文🙂").equals("verified"));
    check(port.writes == 1 && port.text.equals("hello中文🙂"));
    check(
        core.verifiedHash.equals(SessionCore.sha(port.text))
            && core.verifiedLength == port.text.length());
    check(core.commit(token, "again").equals("denied") && port.writes == 1);
    for (int kind = 0; kind < 4; kind++) {
      port = new EditorPort();
      if (kind == 0) port.password = true;
      if (kind == 1) port.cursor = 0;
      if (kind == 2) port.uid = 112345;
      if (kind == 3) port.allowed = false;
      deniedPrepare(port);
    }
    for (int kind = 0; kind < 8; kind++) {
      port = new EditorPort();
      core = new SessionCore(port);
      token = prepare(core, port);
      if (kind == 0) port.generation++;
      if (kind == 1) port.consentEpoch++;
      if (kind == 2) port.bindingEpoch++;
      if (kind == 3) port.resource = "org.fixture:id/other";
      if (kind == 4) port.allowed = false;
      if (kind == 5) port.time = 3001;
      if (kind == 6) {
        port.text = "manual";
        port.cursor = 6;
      }
      if (kind == 7) core.revoke();
      check(core.commit(token, "中文").equals("denied") && port.writes == 0);
    }
    for (int kind = 0; kind < 3; kind++) {
      port = new EditorPort();
      core = new SessionCore(port);
      token = prepare(core, port);
      if (kind == 0) port.refuse = true;
      if (kind == 1) port.unreadableAfter = true;
      if (kind == 2) port.rebindAfter = true;
      check(core.commit(token, "中文").equals("unknown") && port.writes == 1);
    }
    for (String value : new String[] {"", "a\n", "\u0000", "\ud800", "a".repeat(257)}) {
      port = new EditorPort();
      core = new SessionCore(port);
      token = prepare(core, port);
      check(core.commit(token, value).equals("denied") && port.writes == 0);
    }
    System.out.println("{\"checks\":" + checks + ",\"failed\":0,\"native_android_tested\":false}");
  }
}
