// Media sources are verified Bilibili videos or owner-uploaded immutable objects.
import { ask } from '../dialog.js?v=20261103v7';

export function mountMedia({ api, wrap, getAgent, getGeneration }) {
  const $ = (id) => document.getElementById(id);
  let epoch = 0,
    sourceCursor = null,
    selectedSource = '',
    jobCursor = null,
    selectedJob = '',
    transcript = null,
    frameURL = null,
    displayedAgent = '',
    selectedKind = '';
  const path = (agent = getAgent()) => `/agents/${agent}/media`;
  const capture = () => ({ agent: getAgent(), generation: getGeneration(), epoch });
  const current = (t) => t.agent === getAgent() && t.generation === getGeneration() && t.epoch === epoch;

  const ASR_FIELDS = ['media-asr-model', 'media-asr-device', 'media-asr-compute'];
  const FRAME_FIELDS = ['media-frame-count', 'media-frame-width'];

  function renderOperations() {
    for (const option of $('media-operation').options)
      option.disabled =
        (selectedKind.startsWith('object_') && option.value === 'subtitle') ||
        (selectedKind === 'object_audio' && option.value === 'frames');
    if ($('media-operation').selectedOptions[0]?.disabled) $('media-operation').value = 'transcribe';
    const operation = $('media-operation').value;
    for (const id of ASR_FIELDS) $(id).disabled = operation !== 'transcribe';
    for (const id of FRAME_FIELDS) $(id).disabled = operation !== 'frames';
  }
  $('media-operation').addEventListener('change', renderOperations);
  renderOperations();

  function renderSources(items, appended) {
    const list = $('media-source-list');
    if (!appended) list.replaceChildren();
    for (const row of items)
      list.append(
        new Option(
          `${row.title || row.bvid} · ${row.bvid || (row.kind === 'object_audio' ? '上传音频' : '上传视频')}`,
          row.id,
        ),
      );
    $('media-more').disabled = !sourceCursor;
    if (selectedSource && [...list.options].some((o) => o.value === selectedSource)) list.value = selectedSource;
  }

  function renderSourceDetail(row) {
    selectedKind = row.kind || '';
    renderOperations();
    const lines = [
      `来源 ${row.id}`,
      `标题 ${row.title}`,
      row.object_id
        ? `上传${row.kind === 'object_audio' ? '音频' : '视频'} · 对象 ${row.object_id}`
        : `BV ${row.bvid} · aid ${row.aid} · cid ${row.cid}`,
      `时长 ${row.duration_s == null ? '解码时核验' : row.duration_s + 's'} · 活跃作业 ${row.active_jobs}`,
      `最新转写 ${row.latest_transcript_id || '尚无'}`,
      `最新帧拼图 ${row.latest_frames_id || '尚无'}`,
    ];
    $('media-source-detail').textContent = lines.join('\n');
    const bound = Boolean(selectedSource);
    for (const id of ['media-jobs-refresh', 'media-transcript-open', 'media-frames-open', 'media-source-delete'])
      $(id).disabled = !bound;
  }

  function renderJobs(items, appended) {
    const list = $('media-job-list');
    if (!appended) list.replaceChildren();
    for (const row of items)
      list.append(new Option(`${row.created_at.slice(0, 19)} · ${row.operation} · ${row.state}`, row.id));
    $('media-jobs-more').disabled = !jobCursor;
    if (selectedJob && [...list.options].some((o) => o.value === selectedJob)) list.value = selectedJob;
    $('media-job-cancel').disabled = !selectedJob;
  }

  function renderJobDetail(row) {
    $('media-job-detail').textContent = [
      `作业 ${row.id || '—'}`,
      `操作 ${row.operation} · 状态 ${row.state}${row.error_code ? ` · ${row.error_code}` : ''}`,
      `参数 ${JSON.stringify(row.params || {})}`,
      `完成时间 ${row.finished_at || '—'}`,
      '产物见来源详情中的最新转写 / 帧拼图',
    ].join('\n');
    $('media-job-cancel').disabled = !row.id || !['queued', 'running'].includes(row.state);
  }

  function renderWindow(data) {
    transcript = { id: data.transcript_id, start: data.start, end: data.end, total: data.total_chars };
    $('media-transcript-window').textContent =
      `${data.kind === 'asr' ? 'ASR 转写' : '在线字幕'} · ${data.language || '未知语言'} · ` +
      `窗口 ${data.start}-${data.end} / 共 ${data.total_chars} 字符\n\n${data.text}`;
    $('media-window-prev').disabled = data.start <= 0;
    $('media-window-next').disabled = data.end >= data.total_chars;
  }

  function clearTranscript() {
    transcript = null;
    $('media-transcript-window').textContent = '';
    $('media-window-prev').disabled = true;
    $('media-window-next').disabled = true;
  }

  function releaseFrame() {
    if (frameURL) URL.revokeObjectURL(frameURL);
    frameURL = null;
    $('media-frames-image').hidden = true;
  }

  const EMPTY_SOURCE = {
    id: '',
    title: '—',
    bvid: '—',
    aid: '—',
    cid: '—',
    duration_s: '—',
    active_jobs: '—',
    latest_transcript_id: '',
    latest_frames_id: '',
  };
  const EMPTY_JOB = { id: '', operation: '—', state: '—', params: {}, finished_at: '' };

  async function loadSources(t, append) {
    const query = append && sourceCursor ? `?after=${sourceCursor}&limit=50` : '?limit=50';
    const data = await api(`${path(t.agent)}/sources${query}`);
    if (!current(t)) return;
    sourceCursor = data.next_after;
    renderSources(data.items, append);
    // Keep the current selection across refreshes; only a vanished source clears it.
    if (![...$('media-source-list').options].some((option) => option.value === selectedSource)) {
      selectedSource = '';
      selectedJob = '';
      jobCursor = null;
      clearTranscript();
      releaseFrame();
      renderSourceDetail(EMPTY_SOURCE);
      renderJobs([], false);
      renderJobDetail(EMPTY_JOB);
    }
  }

  async function loadSourceDetail(t) {
    const row = await api(`${path(t.agent)}/sources/${selectedSource}`);
    if (!current(t)) return;
    renderSourceDetail(row);
  }

  async function loadJobs(t, append) {
    if (!selectedSource) return;
    const base = `${path(t.agent)}/sources/${selectedSource}/jobs`;
    const query = append && jobCursor ? `?after=${jobCursor}&limit=50` : '?limit=50';
    const data = await api(`${base}${query}`);
    if (!current(t)) return;
    jobCursor = data.next_after;
    renderJobs(data.items, append);
    if (![...$('media-job-list').options].some((option) => option.value === selectedJob)) {
      selectedJob = '';
      renderJobDetail(EMPTY_JOB);
    }
  }

  async function loadProcessor(t) {
    const health = await api(`${path(t.agent)}/processor`);
    if (!current(t)) return;
    $('media-processor-status').textContent =
      `处理进程 ${health.processor.running ? '运行中' : '待启动'} · 待收尾 ${health.processor.cleanup_pending}` +
      (health.processor.probed ? ` · 组件 ${JSON.stringify(health.processor.components)}` : ' · 组件未探测');
    $('media-processor-probe').disabled = health.closing;
  }

  $('media-refresh').addEventListener(
    'click',
    wrap(async () => await loadSources(capture(), false)),
  );
  $('media-more').addEventListener(
    'click',
    wrap(async () => await loadSources(capture(), true)),
  );
  $('media-source-list').addEventListener(
    'change',
    wrap(async () => {
      selectedSource = $('media-source-list').value;
      selectedJob = '';
      jobCursor = null;
      const t = capture();
      await loadSourceDetail(t);
      await loadJobs(t, false);
    }),
  );
  $('media-register-form').addEventListener(
    'submit',
    wrap(async () => {
      const config = $('media-config').value.trim(),
        target = $('media-target').value.trim();
      if (!/^[0-9a-f-]{36}$/.test(config)) throw new Error('通道配置 UUID 无效');
      if (!/^BV[0-9A-Za-z]{10}$/.test(target)) throw new Error('BV 号格式应为 BV + 10 位字符');
      const t = capture();
      const row = await api(`${path(t.agent)}/sources`, { method: 'POST', body: { config_id: config, target } });
      if (!current(t)) return;
      $('media-register-status').textContent = `已登记来源 ${row.id}`;
      selectedSource = row.id;
      await loadSources(t, false);
      await loadSourceDetail(t);
      await loadJobs(t, false);
    }),
  );
  $('media-upload-form').addEventListener(
    'submit',
    wrap(async () => {
      const file = $('media-upload-file').files[0],
        title = $('media-upload-title').value.trim();
      if (!file || file.size < 1 || file.size > 16 * 1024 * 1024) throw new Error('请选择不超过 16 MiB 的音视频文件');
      const types = new Set([
        'audio/wav',
        'audio/x-wav',
        'audio/mpeg',
        'audio/mp4',
        'audio/aac',
        'audio/ogg',
        'audio/flac',
        'audio/webm',
        'video/mp4',
        'video/webm',
        'video/ogg',
        'video/x-matroska',
      ]);
      if (!types.has(file.type)) throw new Error('请选择 WAV、MP3、MP4、WebM、Ogg、FLAC 或 AAC 音视频文件');
      if (!title || title.length > 300) throw new Error('请填写媒体来源标题');
      const t = capture();
      const object = await api(`/agents/${t.agent}/objects?pinned=false`, { method: 'POST', body: file, raw: true });
      if (!current(t)) return;
      $('media-upload-status').textContent = `文件已保存为对象 ${object.id}，正在登记来源`;
      const row = await api(`${path(t.agent)}/uploads`, { method: 'POST', body: { object_id: object.id, title } });
      if (!current(t)) return;
      $('media-upload-status').textContent = `已登记上传来源 ${row.id}`;
      $('media-upload-file').value = '';
      $('media-upload-file').dispatchEvent(new Event('change', { bubbles: true }));
      selectedSource = row.id;
      selectedJob = '';
      clearTranscript();
      releaseFrame();
      await loadSources(t, false);
      await loadSourceDetail(t);
      await loadJobs(t, false);
    }),
  );
  $('media-job-form').addEventListener(
    'submit',
    wrap(async () => {
      if (!selectedSource) throw new Error('请先选择媒体来源');
      const operation = $('media-operation').value;
      const body = { operation };
      if (operation === 'transcribe') {
        body.model = $('media-asr-model').value.trim();
        body.device = $('media-asr-device').value;
        body.compute_type = $('media-asr-compute').value;
      }
      if (operation === 'frames') {
        body.count = Number($('media-frame-count').value);
        body.width = Number($('media-frame-width').value);
      }
      const t = capture();
      const job = await api(`${path(t.agent)}/sources/${selectedSource}/jobs`, { method: 'POST', body });
      if (!current(t)) return;
      $('media-job-status').textContent = `作业 ${job.id} 已进入队列（${job.state}）`;
      selectedJob = job.id;
      await loadJobs(t, false);
    }),
  );
  $('media-jobs-refresh').addEventListener(
    'click',
    wrap(async () => await loadJobs(capture(), false)),
  );
  $('media-jobs-more').addEventListener(
    'click',
    wrap(async () => await loadJobs(capture(), true)),
  );
  $('media-job-list').addEventListener(
    'change',
    wrap(async () => {
      selectedJob = $('media-job-list').value;
      if (!selectedJob) return;
      const t = capture();
      const row = await api(`${path(t.agent)}/jobs/${selectedJob}`);
      if (current(t)) renderJobDetail(row);
    }),
  );
  $('media-job-cancel').addEventListener(
    'click',
    wrap(async () => {
      if (!selectedJob) return;
      const t = capture();
      const row = await api(`${path(t.agent)}/jobs/${selectedJob}/cancel`, { method: 'POST' });
      if (!current(t)) return;
      renderJobDetail(row);
      await loadJobs(t, false);
    }),
  );
  async function openWindow(t, start) {
    const length = Math.min(12000, Math.max(1, Number($('media-window-length').value) || 4000));
    const data = await api(`${path(t.agent)}/transcripts/${transcript.id}/window?start=${start}&length=${length}`);
    if (current(t)) renderWindow(data);
  }
  $('media-transcript-open').addEventListener(
    'click',
    wrap(async () => {
      if (!selectedSource) return;
      const t = capture();
      const source = await api(`${path(t.agent)}/sources/${selectedSource}`);
      if (!current(t)) return;
      const id = source.latest_transcript_id;
      if (!id) {
        $('media-transcript-window').textContent = '此来源尚无已完成的转写；请先提交字幕或 ASR 作业。';
        return;
      }
      transcript = { id };
      await openWindow(t, 0);
    }),
  );
  $('media-window-prev').addEventListener(
    'click',
    wrap(async () => {
      if (!transcript) return;
      const length = Math.min(12000, Math.max(1, Number($('media-window-length').value) || 4000));
      await openWindow(capture(), Math.max(0, (transcript.start || 0) - length));
    }),
  );
  $('media-window-next').addEventListener(
    'click',
    wrap(async () => {
      if (!transcript) return;
      await openWindow(capture(), transcript.end);
    }),
  );
  $('media-frames-open').addEventListener(
    'click',
    wrap(async () => {
      if (!selectedSource) return;
      const t = capture();
      const source = await api(`${path(t.agent)}/sources/${selectedSource}`);
      if (!current(t)) return;
      const id = source.latest_frames_id;
      if (!id) {
        $('media-frames-status').textContent = '此来源尚无已完成的抽帧作业。';
        return;
      }
      const row = await api(`${path(t.agent)}/frames/${id}`);
      if (!current(t)) return;
      $('media-frames-detail').textContent =
        `帧拼图 ${row.id} · ${row.frame_count} 帧 · ${row.width}×${row.height}\n` +
        `抽取时间点 ${row.timestamps.map((s) => `${Math.round(s)}s`).join(', ')}\n` +
        `对象 ${row.object_id}`;
      const blob = await api(`/agents/${t.agent}/objects/${row.object_id}/content`, { binary: true });
      if (!current(t)) return;
      releaseFrame();
      frameURL = URL.createObjectURL(blob);
      $('media-frames-image').src = frameURL;
      $('media-frames-image').hidden = false;
      $('media-frames-status').textContent = '';
    }),
  );
  $('media-processor-probe').addEventListener(
    'click',
    wrap(async () => {
      const t = capture();
      $('media-processor-status').textContent = '正在启动本地处理进程并检查组件…';
      const probe = await api(`${path(t.agent)}/processor/probe`, { method: 'POST' });
      if (!current(t)) return;
      const missing = Object.entries(probe.components || {})
        .filter(([, ok]) => !ok)
        .map(([name]) => name);
      $('media-processor-status').textContent = missing.length
        ? `缺少组件：${missing.join(', ')}。安装 asr 扩展依赖后重试（安装包：pip install "omnioperator[asr]"；源码环境：uv sync --extra asr）。`
        : `组件齐备：${JSON.stringify(probe.components)}`;
    }),
  );
  $('media-source-delete').addEventListener(
    'click',
    wrap(async () => {
      if (!selectedSource) return;
      const accepted = await ask({
        title: '删除媒体来源',
        message: '将删除该来源及其全部作业、转写与帧拼图记录；登记过的对象文件按引用回收。确定删除？',
        confirmLabel: '删除',
        danger: true,
      });
      if (!accepted) return;
      const t = capture();
      await api(`${path(t.agent)}/sources/${selectedSource}`, { method: 'DELETE' });
      if (!current(t)) return;
      selectedSource = '';
      selectedJob = '';
      clearTranscript();
      releaseFrame();
      $('media-register-status').textContent = '来源已删除';
      await loadSources(t, false);
      await loadJobs(t, false);
    }),
  );
  return {
    async load() {
      if (displayedAgent !== getAgent()) {
        displayedAgent = getAgent();
        selectedSource = '';
        selectedJob = '';
        sourceCursor = null;
        jobCursor = null;
        selectedKind = '';
        $('media-upload-file').value = '';
        $('media-upload-file').dispatchEvent(new Event('change', { bubbles: true }));
        $('media-upload-title').value = '';
        $('media-upload-status').textContent = '';
        clearTranscript();
        releaseFrame();
      }
      epoch++;
      const t = capture();
      await Promise.all([loadSources(t, false), loadProcessor(t)]);
      if (selectedSource) {
        await loadSourceDetail(t);
        await loadJobs(t, false);
      }
    },
  };
}
