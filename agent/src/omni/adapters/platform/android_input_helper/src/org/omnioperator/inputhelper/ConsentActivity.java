package org.omnioperator.inputhelper;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.provider.Settings;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Local explicit consent; no intents, broadcasts or clipboard can supply an input command. */
public final class ConsentActivity extends Activity {
  @Override
  public void onCreate(Bundle state) {
    super.onCreate(state);
    getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
    LinearLayout layout = new LinearLayout(this);
    layout.setOrientation(LinearLayout.VERTICAL);
    layout.setPadding(24, 24, 24, 24);
    TextView explanation = new TextView(this);
    explanation.setText(
        "OmniOperator 受控输入\n"
            + "先在系统输入法设置手动启用，再回工作台固定 APK/证书指纹及配对凭据。每次输入仍需宿主审批。本机允许最多30分钟，进程重启后失效。不会记录输入正文。");
    TextView status = new TextView(this), secret = new TextView(this);
    secret.setTextIsSelectable(true);
    Button rotate = new Button(this);
    rotate.setText("生成/轮换配对密钥（会撤销当前允许）");
    rotate.setOnClickListener(
        v -> {
          try {
            secret.setText(Pairing.rotate(this));
            status.setText("将密钥保存到宿主凭据库；不会自动复制或上传。");
          } catch (Exception ignored) {
            status.setText("密钥存储不可用");
          }
        });
    Button allow = new Button(this);
    allow.setText("允许本进程受控输入30分钟");
    allow.setOnClickListener(
        v -> {
          try {
            Pairing.get(this);
            Pairing.allow();
            secret.setText("");
            status.setText("已允许；返回目标应用，宿主仍需逐次审批。");
          } catch (Exception ignored) {
            status.setText("先生成配对密钥");
          }
        });
    Button pause = new Button(this);
    pause.setText("暂停并撤销允许");
    pause.setOnClickListener(
        v -> {
          Pairing.pause();
          secret.setText("");
          status.setText("已暂停");
        });
    Button settings = new Button(this);
    settings.setText("打开系统输入法设置");
    settings.setOnClickListener(
        v -> startActivity(new Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)));
    layout.addView(explanation);
    layout.addView(rotate);
    layout.addView(secret);
    layout.addView(allow);
    layout.addView(pause);
    layout.addView(settings);
    layout.addView(status);
    setContentView(layout);
  }
}
