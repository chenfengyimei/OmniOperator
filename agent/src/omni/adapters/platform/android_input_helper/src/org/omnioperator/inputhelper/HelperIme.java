package org.omnioperator.inputhelper;

import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.inputmethodservice.InputMethodService;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.InputType;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.ExtractedText;
import android.view.inputmethod.ExtractedTextRequest;
import android.view.inputmethod.InputBinding;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodInfo;
import android.view.inputmethod.InputMethodManager;
import android.view.inputmethod.InputMethodSubtype;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/**
 * A system-bound IME with one bounded shell-UID socket; no network, receiver or arbitrary commands.
 */
public final class HelperIme extends InputMethodService implements SessionCore.Port {
  private static final String COMPONENT = "org.omnioperator.inputhelper/.HelperIme";
  private final Handler main = new Handler(Looper.getMainLooper());
  private final SessionCore core = new SessionCore(this);
  private volatile boolean stopped = false;
  private volatile LocalServerSocket server;
  private volatile LocalSocket client;
  private long generation = 0;
  private long bindingEpoch = 0;
  private InputConnection readConnection;
  private EditorInfo readInfo;

  @Override
  public void onCreate() {
    super.onCreate();
    getWindow().getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
    Thread thread = new Thread(this::serve, "omni-input-socket");
    thread.setDaemon(true);
    thread.start();
  }

  @Override
  public void onStartInput(EditorInfo info, boolean restarting) {
    generation++;
    bindingEpoch++;
    super.onStartInput(info, restarting);
  }

  @Override
  public void onFinishInput() {
    generation++;
    bindingEpoch++;
    super.onFinishInput();
  }

  @Override
  public void onUpdateSelection(int a, int b, int c, int d, int e, int f) {
    generation++;
    super.onUpdateSelection(a, b, c, d, e, f);
  }

  @Override
  public boolean onKeyDown(int key, KeyEvent event) {
    generation++;
    core.revoke();
    return super.onKeyDown(key, event);
  }

  @Override
  public View onCreateInputView() {
    LinearLayout view = new LinearLayout(this);
    view.setOrientation(LinearLayout.VERTICAL);
    TextView label = new TextView(this);
    label.setText("OmniOperator：仅逐次审批的当前编辑器输入；触摸此区域会暂停。");
    Button pause = new Button(this);
    pause.setText("暂停/撤销允许");
    pause.setOnClickListener(
        v -> {
          Pairing.pause();
          core.revoke();
        });
    Button next = new Button(this);
    next.setText("切换到其他输入法");
    next.setOnClickListener(
        v -> {
          generation++;
          core.revoke();
          switchToNextInputMethod(false);
        });
    view.addView(label);
    view.addView(pause);
    view.addView(next);
    view.setOnTouchListener(
        (v, event) -> {
          generation++;
          core.revoke();
          return false;
        });
    return view;
  }

  @Override
  public boolean onEvaluateFullscreenMode() {
    return false;
  }

  @Override
  public boolean consent() {
    return Pairing.consent();
  }

  @Override
  public long now() {
    return SystemClock.elapsedRealtime();
  }

  @Override
  public SessionCore.Editor read() throws Exception {
    EditorInfo info = getCurrentInputEditorInfo();
    InputBinding binding = getCurrentInputBinding();
    InputConnection connection = getCurrentInputConnection();
    if (info == null
        || binding == null
        || connection == null
        || !getCurrentInputStarted()
        || !consent()) throw new IllegalStateException("editor_unavailable");
    int variation = info.inputType & InputType.TYPE_MASK_VARIATION,
        kind = info.inputType & InputType.TYPE_MASK_CLASS;
    boolean password =
        (kind == InputType.TYPE_CLASS_TEXT
                && (variation == InputType.TYPE_TEXT_VARIATION_PASSWORD
                    || variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
                    || variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD))
            || (kind == InputType.TYPE_CLASS_NUMBER
                && variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD);
    if (password
        || kind != InputType.TYPE_CLASS_TEXT
        || info.fieldId == 0
        || info.packageName == null) throw new IllegalStateException("editor_denied");
    if (getPackageManager().getApplicationInfo(info.packageName, 0).uid != binding.getUid())
      throw new IllegalStateException("editor_uid_changed");
    String resource =
        getPackageManager()
            .getResourcesForApplication(info.packageName)
            .getResourceName(info.fieldId);
    ExtractedTextRequest request = new ExtractedTextRequest();
    request.hintMaxChars = 4097;
    request.hintMaxLines = 64;
    ExtractedText text = connection.getExtractedText(request, 0);
    if (text == null
        || text.text == null
        || text.startOffset != 0
        || text.partialStartOffset != -1
        || text.partialEndOffset != -1
        || text.selectionStart != text.selectionEnd
        || text.text.length() > 4096) throw new IllegalStateException("editor_snapshot_incomplete");
    readConnection = connection;
    readInfo = info;
    return new SessionCore.Editor(
        info.packageName,
        resource,
        text.text.toString(),
        generation,
        Pairing.epoch,
        bindingEpoch,
        binding.getUid(),
        text.selectionEnd,
        false);
  }

  @Override
  public boolean append(SessionCore.Editor expected, String text) {
    if (!consent()
        || expected.generation != generation
        || expected.consentEpoch != Pairing.epoch
        || readConnection == null
        || readConnection != getCurrentInputConnection()
        || readInfo != getCurrentInputEditorInfo()) return false;
    return readConnection.commitText(text, 1);
  }

  private String certificate() throws Exception {
    PackageInfo info =
        getPackageManager()
            .getPackageInfo(getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
    byte[] bytes = info.signingInfo.getApkContentsSigners()[0].toByteArray();
    StringBuilder out = new StringBuilder();
    for (byte b : MessageDigest.getInstance("SHA-256").digest(bytes))
      out.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
    return out.toString();
  }

  private JSONObject mainCall(JSONObject value) throws Exception {
    String method = value.getString("method");
    JSONObject data = value.getJSONObject("data");
    JSONObject result = new JSONObject();
    result.put("certificate_sha256", certificate());
    result.put("user", Process.myUid() / 100000);
    if (method.equals("prepare")) {
      String token =
          core.prepare(
              data.getString("application"),
              data.getString("resource"),
              data.getString("before_hash"),
              data.getInt("user"));
      result.put("status", "ready");
      result.put("token", token);
    } else if (method.equals("commit")) {
      String status = core.commit(data.getString("token"), data.getString("text"));
      result.put("status", status);
      if (status.equals("verified")) {
        result.put("after_hash", core.verifiedHash);
        result.put("utf16_length", core.verifiedLength);
      }
    } else if (method.equals("restore")) {
      core.revoke();
      result.put("status", "closing");
    } else throw new IllegalArgumentException("unknown_method");
    return result;
  }

  private void restore(String original, int subtype) {
    core.revoke();
    if (original != null
        && !original.equals(COMPONENT)
        && COMPONENT.equals(
            Settings.Secure.getString(
                getContentResolver(), Settings.Secure.DEFAULT_INPUT_METHOD))) {
      try {
        if (subtype == -1) switchInputMethod(original);
        else {
          InputMethodManager manager = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
          for (InputMethodInfo info : manager.getEnabledInputMethodList())
            if (info.getId().equals(original))
              for (int i = 0; i < info.getSubtypeCount(); i++) {
                InputMethodSubtype candidate = info.getSubtypeAt(i);
                if (candidate.hashCode() == subtype) {
                  switchInputMethod(original, candidate);
                  return;
                }
              }
        }
      } catch (RuntimeException ignored) {
        /* Host retains the persistent restoration obligation. */
      }
    }
  }

  private void serve() {
    try {
      server =
          new LocalServerSocket("org.omnioperator.inputhelper.v1.u" + (Process.myUid() / 100000));
      while (!stopped) {
        LocalSocket socket = server.accept();
        client = socket;
        String original = null;
        int subtype = -1;
        try {
          if (socket.getPeerCredentials().getUid() != 2000)
            throw new SecurityException("shell_peer_required");
          socket.setSoTimeout(5000);
          byte[] key = Pairing.get(this);
          DataInputStream input = new DataInputStream(socket.getInputStream());
          DataOutputStream output = new DataOutputStream(socket.getOutputStream());
          HashSet<String> nonces = new HashSet<>();
          String binding = null;
          for (int frames = 0; frames < 4 && !stopped; frames++) {
            JSONObject value = Wire.read(input, key);
            String nonce = value.getString("nonce");
            if (!nonces.add(nonce)) throw new SecurityException("nonce_reused");
            String current = value.getString("binding");
            if (!current.matches("[0-9a-f]{64}") || (binding != null && !binding.equals(current)))
              throw new SecurityException("binding_changed");
            binding = current;
            if (frames == 0 && !value.getString("method").equals("prepare"))
              throw new SecurityException("prepare_required");
            if (value.getString("method").equals("prepare")) {
              if (frames != 0) throw new SecurityException("prepare_reused");
              original = value.getJSONObject("data").getString("original_ime");
              subtype = value.getJSONObject("data").getInt("original_subtype");
              if (!original.matches("[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+"))
                throw new SecurityException("restore_identity_invalid");
            }
            final JSONObject request = value;
            FutureTask<JSONObject> task = new FutureTask<>(() -> mainCall(request));
            main.post(task);
            JSONObject result;
            try {
              result = task.get(2500, TimeUnit.MILLISECONDS);
            } catch (Exception ignored) {
              task.cancel(false);
              result = new JSONObject();
              result.put(
                  "status", value.getString("method").equals("commit") ? "unknown" : "denied");
            }
            Wire.write(output, result, key, nonce);
            if (value.getString("method").equals("restore")) break;
          }
        } catch (Exception ignored) {
          /* Never log editor text, pairing key or exception payloads. */
        } finally {
          try {
            socket.close();
          } catch (Exception ignored) {
          }
          client = null;
          final String previous = original;
          final int previousSubtype = subtype;
          main.post(() -> restore(previous, previousSubtype));
        }
      }
    } catch (Exception ignored) {
      /* Explicit host probe reports unavailable; no fallback endpoint. */
    }
  }

  @Override
  public void onDestroy() {
    stopped = true;
    generation++;
    core.revoke();
    try {
      if (client != null) client.close();
    } catch (Exception ignored) {
    }
    try {
      if (server != null) server.close();
    } catch (Exception ignored) {
    }
    super.onDestroy();
  }
}
