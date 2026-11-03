"""Local specialist separation; input media never leaves the machine."""
import argparse
import gc
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import imageio_ffmpeg
import numpy as np
import soundfile as sf
import torch

BASE = Path(__file__).resolve().parent
FFMPEG = Path(imageio_ffmpeg.get_ffmpeg_exe())
os.environ["PATH"] = str(FFMPEG.parent) + os.pathsep + os.environ["PATH"]
# The library checks the command name instead of the imageio binary name.
local_bin = BASE / "bin"
local_bin.mkdir(exist_ok=True)
fflink = local_bin / "ffmpeg"
try:
    fflink.symlink_to(FFMPEG)
except FileExistsError:
    if fflink.resolve() != FFMPEG.resolve():
        raise
os.environ["PATH"] = str(local_bin) + os.pathsep + os.environ["PATH"]

from audio_separator.separator import Separator

MODELS = {
    "vocals": "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt",
    "karaoke": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
    "drums": "MDX23C-DrumSep-aufr33-jarredou.ckpt",
    "crowd": "UVR-MDX-NET_Crowd_HQ_1.onnx",
    "dereverb": "deverb_bs_roformer_8_384dim_10depth.ckpt",
    "bve": "UVR-BVE-4B_SN-44100-1.pth",
    "denoise": "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
    "vr_deverb": "UVR-De-Reverb-aufr33-jarredou.pth",
}
HASHES = {
    "vocals": "21b9d0958e35b8ebfbe2afe69bbd5444e5ffe2f5d80ae0d583b833d2f3c0d139",
    "karaoke": "1de20d459332fe8869aeb01327a31df0032262706e1365114e852dc271779813",
    "drums": "d2a4aa53eb584d21eead358a4e66d1882ad182911be018f052b5da73be9096d0",
    "crowd": "313b7bf869c411fdafe005cf0d5a635c405cb3d0df137178a64091952d75225c",
    "dereverb": "9c38653aaa5e49f2f7b84dd3be2b6b679e0cbea23978e6b48389ee6f0a914768",
    "bve": "56165bdfa0dd5df7930ff76652200dc287e1bfef622bba5c812c736dc22067fe",
    "denoise": "7c1c39191edc34e942ca7f2346ce6b6c0e1208a5f76349ffce6f696bd12910de",
    "vr_deverb": "fff726b0adf09f2eadc2151645557db81e1a01fe15d91f9bbbfa9b50a007f1fd",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=MODELS)
    parser.add_argument("--input", required=True)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--wait-for-model", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()
    model_path = BASE / "models" / MODELS[args.task]
    if args.wait_for_model:
        deadline = time.monotonic() + 900
        print(f"Waiting for {model_path.name}", flush=True)
        while not model_path.exists():
            if time.monotonic() > deadline:
                raise TimeoutError("Model download has not completed")
            time.sleep(3)
    if model_path.exists():
        with model_path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != HASHES[args.task]:
            raise ValueError(f"Model checksum mismatch: {digest}")
        print(f"Verified SHA256: {digest}", flush=True)
    out = Path(args.out).resolve() if args.out else BASE / "specialists" / args.task
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(6)
    separator = Separator(
        log_level=logging.INFO,
        model_file_dir=str(BASE / "models"),
        output_dir=str(out),
        output_format="WAV",
        normalization_threshold=1.0,
        amplification_threshold=0.0,
        use_soundfile=True,
        mdx_params={"segment_size": 256, "hop_length": 1024, "overlap": 0.5,
                    "batch_size": 1, "enable_denoise": True},
        vr_params={"batch_size": 4, "window_size": 512, "aggression": 5,
                   "enable_tta": True, "enable_post_process": False, "high_end_process": False},
        mdxc_params={"segment_size": 256, "override_model_segment_size": False,
                     "batch_size": 1, "overlap": 4, "pitch_shift": 0},
    )
    if args.cpu or args.task == "crowd":
        separator.torch_device = torch.device("cpu")
        separator.torch_device_mps = None
        separator.onnx_execution_provider = ["CPUExecutionProvider"]
    if args.task == "crowd":
        data = separator.load_model_data_using_hash(str(model_path))
        separator.arch_specific_params["MDX"]["segment_size"] = 2 ** data["mdx_dim_t_set"]
    if args.download_only:
        separator.download_model_and_data(MODELS[args.task])
        return
    separator.load_model(MODELS[args.task])
    instance = separator.model_instance
    x, sr = sf.read(args.input, dtype="float32", always_2d=True)
    if sr != 44100 or x.shape[1] != 2:
        raise ValueError("Expected stereo, 44100 Hz input")
    print(f"TASK={args.task} device={separator.torch_device} samples={len(x)}", flush=True)
    start = time.perf_counter()
    # demix returns ALL model targets, including the six DrumSep targets.
    # Avoid the CLI's two-stem writer, independent level normalization and PCM16.
    if args.task in {"bve", "vr_deverb"}:
        # Capture float results from VR before its PCM16 file writer.
        instance.final_process = lambda *a, **k: None
        instance.separate(args.input)
        sources = {instance.primary_stem_name: instance.primary_source,
                   instance.secondary_stem_name: instance.secondary_source}
    else:
        sources = instance.demix(x.T.copy())
        if not isinstance(sources, dict):
            key = instance.primary_stem_name
            sources = {key: sources}
            source = np.asarray(sources[key], dtype=np.float32)
            sources[instance.secondary_stem_name] = x.T - source
    report = {"task": args.task, "model": MODELS[args.task], "device": str(separator.torch_device),
              "input": str(Path(args.input).resolve()), "outputs": {},
              "model_data": instance.model_data, "source_frame_adjustments": {}}
    for name, wave in sources.items():
        wave = np.asarray(wave, dtype=np.float32)
        if wave.ndim != 2:
            raise ValueError(f"Unexpected source shape: {name}: {wave.shape}")
        if wave.shape[0] == 2:
            wave = wave.T
        frame_delta = len(wave) - len(x)
        if abs(frame_delta) > int(sr * .2):
            raise ValueError(f"Source duration mismatch: {name}: {frame_delta} frames")
        report["source_frame_adjustments"][name] = frame_delta
        wave = wave[:len(x)]
        if len(wave) < len(x):
            wave = np.pad(wave, ((0,len(x)-len(wave)),(0,0)))
        if wave.shape != x.shape or not np.isfinite(wave).all():
            raise ValueError(f"Invalid source: {name}: {wave.shape}")
        safe = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        filename = out / f"{safe}.wav"
        sf.write(filename, wave, sr, subtype="FLOAT")
        report["outputs"][name] = str(filename)
        print(f"SAVED {name}: {filename}", flush=True)
    if args.task in {"vocals", "karaoke"} and len(sources) == 1:
        name, wave = next(iter(sources.items()))
        wave = np.asarray(wave, dtype=np.float32)
        if wave.shape[0] == 2:
            wave = wave.T
        filename = out / "residual.wav"
        sf.write(filename, x - wave[:len(x)], sr, subtype="FLOAT")
        report["outputs"]["Residual"] = str(filename)
    report["seconds"] = time.perf_counter() - start
    (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    del separator, instance, sources
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


if __name__ == "__main__":
    main()
