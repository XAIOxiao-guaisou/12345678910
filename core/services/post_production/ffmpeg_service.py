"""
core/services/post_production/ffmpeg_service.py
v3.0.0: FFmpeg 多轨混流器（含环境预检 + 修正版时空对齐）

⚠️  系统级依赖说明
════════════════════════════════════════════════════════════
  ffmpeg-python 只是 Python 薄包装层，必须满足：
    宿主机已安装 FFmpeg 可执行文件 且 已加入系统 PATH

  Windows 安装：
    winget install ffmpeg
    (重启终端后生效)

  Linux/macOS:
    sudo apt install ffmpeg   # Ubuntu/Debian
    brew install ffmpeg       # macOS Homebrew
════════════════════════════════════════════════════════════

轨道布局（默认）：
  视频轨（原始静音画面）
  TTS 人声轨  → volume=-4dB
  SFX 音效轨  → volume=-6dB
  BGM 背景音轨 → volume=-12dB

时空对齐策略（sync_strategy）：
  freeze_frame  - TTS > 视频时，最后一帧定格延长（推荐，无丢帧）
  loop_video    - 循环视频画面填满音频（-stream_loop -1 -shortest）
  trim_audio    - 截断音频适配视频（会丢台词，慎用）
"""
import subprocess
import logging
import os
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)


def check_ffmpeg_env(binary: str = "ffmpeg") -> None:
    """
    FFmpeg 环境预检。调用方应在任何 mux() 之前调用一次。
    预检失败立即抛出 EnvironmentError（详细安装指引包含在消息中）。
    """
    try:
        result = subprocess.run(
            [binary, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        version_line = (
            result.stdout.decode(errors="replace").splitlines()[0]
            if result.stdout else ""
        )
        logger.info(f"[FFmpeg] 环境预检成功 ✅ {version_line}")
    except FileNotFoundError:
        raise EnvironmentError(
            f"FFmpeg 未安装或未加入系统 PATH（binary='{binary}'）。\n"
            "Windows: winget install ffmpeg\n"
            "Ubuntu:  sudo apt install ffmpeg\n"
            "macOS:   brew install ffmpeg"
        )
    except subprocess.CalledProcessError as e:
        raise EnvironmentError(
            f"FFmpeg 存在但运行异常: {e.stderr.decode(errors='replace')[:300]}"
        )


async def probe_duration(file_path: str, ffmpeg_binary: str = "ffmpeg") -> float:
    """
    使用 ffprobe 提取媒体文件时长（秒）。
    失败返回 0.0（调用方应兜底处理）。
    """
    ffprobe = ffmpeg_binary.replace("ffmpeg", "ffprobe")
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        raw = stdout.decode(errors="replace").strip()
        return float(raw) if raw else 0.0
    except Exception as e:
        logger.warning(f"[FFmpeg] probe_duration 失败: {e}")
        return 0.0


async def mux(
    video_path: str,
    tts_path: str | None = None,
    sfx_path: str | None = None,
    bgm_path: str | None = None,
    output_path: str = "",
    sync_strategy: str = "freeze_frame",
    ffmpeg_binary: str = "ffmpeg",
) -> str:
    """
    多轨混流，将视频轨与各音频轨合并为最终 MP4。

    Args:
        video_path    : 原始静音视频文件路径（.mp4）
        tts_path      : TTS 人声干声路径（.mp3/.wav），None 则跳过
        sfx_path      : SFX 音效路径（.wav/.mp3），None 则跳过
        bgm_path      : BGM 背景音轨路径（.mp3/.wav），None 则跳过
        output_path   : 输出 MP4 路径，默认在 video_path 同目录生成 _muxed.mp4
        sync_strategy : 时空对齐策略（freeze_frame / loop_video / trim_audio）
        ffmpeg_binary : FFmpeg 可执行文件名或完整路径

    Returns:
        output_path (str): 混流完成的成片绝对路径

    Raises:
        EnvironmentError  : FFmpeg 未安装
        FileNotFoundError : 输入视频文件不存在
        RuntimeError      : FFmpeg 进程执行失败
    """
    check_ffmpeg_env(ffmpeg_binary)

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    if not output_path:
        base = Path(video_path)
        output_path = str(base.parent / (base.stem + "_muxed.mp4"))

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # ── 过滤出真实存在的音轨 ───────────────────────────────────────────
    tts = tts_path if (tts_path and os.path.exists(tts_path)) else None
    sfx = sfx_path if (sfx_path and os.path.exists(sfx_path)) else None
    bgm = bgm_path if (bgm_path and os.path.exists(bgm_path)) else None
    audio_inputs = [a for a in [tts, sfx, bgm] if a]
    audio_count  = len(audio_inputs)

    # ── 构建 FFmpeg 命令 ───────────────────────────────────────────────
    cmd = [ffmpeg_binary, "-y"]

    if sync_strategy == "loop_video" and audio_count > 0:
        # 先以 -stream_loop -1 挂载视频（无限循环），之后 -shortest 截断
        cmd += ["-stream_loop", "-1", "-i", video_path]
    elif sync_strategy == "freeze_frame" and audio_count > 0:
        # freeze_frame: 探测音频最长时长，用 tpad 补足视频
        max_audio_dur = 0.0
        for a in audio_inputs:
            d = await probe_duration(a, ffmpeg_binary)
            if d > max_audio_dur:
                max_audio_dur = d
        video_dur = await probe_duration(video_path, ffmpeg_binary)

        if max_audio_dur > video_dur > 0:
            freeze_extra = round(max_audio_dur - video_dur + 0.1, 3)
            # tpad 在视频末尾以最后一帧定格
            cmd += ["-i", video_path,
                    "-vf", f"tpad=stop_mode=clone:stop_duration={freeze_extra}"]
            logger.info(
                f"[FFmpeg] freeze_frame: 视频 {video_dur:.1f}s, "
                f"最长音频 {max_audio_dur:.1f}s, "
                f"定格补帧 {freeze_extra}s"
            )
        else:
            cmd += ["-i", video_path]
    else:
        cmd += ["-i", video_path]

    # 追加所有音频输入
    for a in audio_inputs:
        cmd += ["-i", a]

    if audio_count == 0:
        # 无音轨：直接 stream copy
        cmd += ["-c", "copy", output_path]
    elif audio_count == 1:
        # 单音轨：调整音量后直接合并
        vol_map = {tts: "1.0", sfx: "0.316", bgm: "0.251"}
        vol = vol_map.get(audio_inputs[0], "1.0")
        cmd += [
            "-filter_complex", f"[1:a]volume={vol}[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "h264_nvenc", "-preset", "p6", "-c:a", "aac",
        ]
        if sync_strategy in ("trim_audio", "loop_video"):
            cmd += ["-shortest"]
        cmd.append(output_path)
    else:
        # 多音轨：amix 混合
        fc = _build_amix_filter(tts, sfx, bgm)
        cmd += [
            "-filter_complex", fc,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "h264_nvenc", "-preset", "p6", "-c:a", "aac",
        ]
        if sync_strategy in ("trim_audio", "loop_video"):
            cmd += ["-shortest"]
        cmd.append(output_path)

    logger.info(
        f"[FFmpeg] 开始混流 [{sync_strategy}]: "
        f"{os.path.basename(video_path)} → {os.path.basename(output_path)}"
    )
    logger.debug(f"[FFmpeg] cmd: {' '.join(cmd)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace")[-600:]
        raise RuntimeError(
            f"FFmpeg 混流失败 (returncode={proc.returncode}):\n{err_msg}"
        )

    logger.info(f"[FFmpeg] ✅ 混流完成: {output_path}")
    return output_path


def _build_amix_filter(
    tts: str | None,
    sfx: str | None,
    bgm: str | None,
) -> str:
    """
    构建多音轨 amix filter_complex 字符串。
    轨道序号从 1 开始（0 是视频轨）。

    音量级别（参照混音工程惯例）：
      TTS 人声：0dB  → volume=1.0
      SFX 音效：-10dB  → volume=0.316
      BGM 背景：-12dB → volume=0.251
    """
    parts = []
    labels = []
    idx = 1

    if tts:
        parts.append(f"[{idx}:a]volume=1.000[tts_v]")
        labels.append("[tts_v]")
        idx += 1
    if sfx:
        parts.append(f"[{idx}:a]volume=0.316[sfx_v]")
        labels.append("[sfx_v]")
        idx += 1
    if bgm:
        parts.append(f"[{idx}:a]volume=0.251[bgm_v]")
        labels.append("[bgm_v]")
        idx += 1

    n = len(labels)
    if n == 0:
        return ""
    if n == 1:
        # 单轨无需 amix，直接重命名标签
        return parts[0].rsplit("[", 1)[0] + "[" + parts[0].rsplit("[", 1)[1].rstrip("]") + "];[" + \
               parts[0].rsplit("[", 1)[1].rstrip("]") + "]anull[aout]"

    mix_part = f"{''.join(labels)}amix=inputs={n}:duration=longest:dropout_transition=3[aout]"
    parts.append(mix_part)
    return ";".join(parts)
