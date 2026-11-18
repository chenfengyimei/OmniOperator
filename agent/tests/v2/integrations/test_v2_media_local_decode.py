"""Real PyAV custom input I/O and nested protocol refusal; isolated generated media only."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import av
import pytest
from omni_test_support.v2.media_audio import write_audio

from omni.adapters.workers import media as worker


def test_local_decoder_reads_generated_wav_with_empty_protocol_whitelist(tmp_path):
    path = write_audio(tmp_path / "actual.wav", 16000)
    samples = worker._decode_audio_16k_mono(path)
    assert len(samples) == 16000 and samples.flags.owndata


def test_upload_stream_refuses_nested_http_that_explicit_native_control_can_open(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    playlist = tmp_path / "owned.m3u8"
    playlist.write_text(
        f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1,\nhttp://127.0.0.1:{server.server_port}/segment.ts\n#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    try:
        with pytest.raises(av.error.FFmpegError):
            av.open(str(playlist), options={"protocol_whitelist": "file,http,tcp"})
        assert requests == ["/segment.ts"]
        requests.clear()
        with pytest.raises(av.error.FFmpegError):
            worker._decode_audio_16k_mono(playlist)
        assert requests == []
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
        assert not thread.is_alive()
