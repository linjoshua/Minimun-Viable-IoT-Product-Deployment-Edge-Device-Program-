from __future__ import annotations

import os
import wave
import shutil
from datetime import datetime


def ensure_writable_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    test_path = os.path.join(path, ".__write_test__.tmp")
    with open(test_path, "wb") as f:
        f.write(b"ok")
        f.flush()
        os.fsync(f.fileno())
    os.remove(test_path)


class WavRollWriter:
    """
    Sample-accurate rolling WAV writer.

    - Rolls exactly every `segment_sec` seconds in frames:
        frames_per_segment = rate * segment_sec
    - If a chunk crosses the boundary, it is split:
        part A -> current file (finalize)
        part B -> next file
    - Each finalized WAV in final_dir is exactly segment_sec long (in frames),
      except the last partial file on close().
    """

    def __init__(
        self,
        work_dir: str,
        final_dir: str,
        segment_sec: int,
        rate: int = 48000,
        channels: int = 1,
        sampwidth: int = 2,  # bytes per sample per channel; S16LE mono => 2
        move_to_final: bool = True,  # False => copy2
        debug_print_open_close: bool = False,
    ):
        self.work_dir = work_dir
        self.final_dir = final_dir
        self.rate = int(rate)
        self.channels = int(channels)
        self.sampwidth = int(sampwidth)
        self.segment_sec = int(segment_sec)
        self.move_to_final = bool(move_to_final)
        self.debug_print_open_close = bool(debug_print_open_close)

        ensure_writable_dir(self.work_dir)
        ensure_writable_dir(self.final_dir)

        self.frames_per_segment = self.rate * self.segment_sec
        self.frames_written_in_segment = 0

        self.wav_fp: wave.Wave_write | None = None
        self.current_work_path: str | None = None

    def _make_filename(self) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S.%f")
        return f"{ts}.wav"

    def _open_new_file(self) -> None:
        filename = self._make_filename()
        self.current_work_path = os.path.join(self.work_dir, filename)

        wf = wave.open(self.current_work_path, "wb")
        wf.setnchannels(self.channels)
        wf.setsampwidth(self.sampwidth)
        wf.setframerate(self.rate)

        self.wav_fp = wf
        self.frames_written_in_segment = 0

        if self.debug_print_open_close:
            print(f"[WAV] Opened(work): {self.current_work_path}", flush=True)

    def _finalize_current(self) -> None:
        """
        Close current work file and transfer to final_dir.

        Requirement:
          Only notify when the file is successfully placed in final_dir.
        """
        if self.wav_fp is None or self.current_work_path is None:
            return

        src = self.current_work_path
        dst = os.path.join(self.final_dir, os.path.basename(src))

        self.wav_fp.close()
        self.wav_fp = None

        try:
            if self.move_to_final:
                shutil.move(src, dst)
            else:
                shutil.copy2(src, dst)

            # Notify only if final file exists
            if os.path.exists(dst):
                print(f"[ROLL] File ready: {dst}", flush=True)

        except Exception as e:
            # No "File ready" message if transfer fails
            print(f"[ROLL][WARN] Transfer failed. Local kept: {src}", flush=True)
            print(f"[ROLL][WARN] {type(e).__name__}: {e}", flush=True)

        self.current_work_path = None

    def write_pcm_bytes(self, pcm_bytes: bytes) -> None:
        """
        pcm_bytes must match:
          channels, sampwidth, rate
        For S16LE mono: sampwidth=2, channels=1
        """
        if not pcm_bytes:
            return

        if self.wav_fp is None:
            self._open_new_file()

        bytes_per_frame = self.sampwidth * self.channels

        # Drop trailing partial frame if any (safety)
        n_frames = len(pcm_bytes) // bytes_per_frame
        if n_frames <= 0:
            return

        total_bytes = n_frames * bytes_per_frame
        data = pcm_bytes[:total_bytes]

        offset = 0  # bytes
        frames_left = n_frames

        while frames_left > 0:
            remaining_frames = self.frames_per_segment - self.frames_written_in_segment
            take_frames = min(frames_left, remaining_frames)

            take_bytes = take_frames * bytes_per_frame
            self.wav_fp.writeframesraw(data[offset: offset + take_bytes])

            self.frames_written_in_segment += take_frames
            frames_left -= take_frames
            offset += take_bytes

            if self.frames_written_in_segment >= self.frames_per_segment:
                self._finalize_current()
                self._open_new_file()

    def close(self) -> None:
        """
        Finalize current file even if the segment is not full.
        """
        if self.wav_fp is not None:
            self._finalize_current()
        if self.debug_print_open_close:
            print("[WAV] Closed.", flush=True)