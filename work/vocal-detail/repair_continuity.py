"""Targeted original-signal recovery; never synthesizes missing notes."""
import argparse
import hashlib
import json
import math
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal, ndimage
from PIL import Image, ImageDraw
import imageio_ffmpeg

from clean_vocals import read, SR, stft, istft

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent.parent
REPAIR = BASE / "repair"
OUT = ROOT / "outputs/instrument_demo"
START, END = 68.0, 75.0
CONTEXT_START, CONTEXT_END = 64.0, 78.0
FFT, HOP = 4096, 256


def transform(x):
    return signal.stft(x,fs=SR,nperseg=FFT,noverlap=FFT-HOP,axis=0,
                       boundary="zeros",padded=True)[2]


def inverse(z,length):
    return signal.istft(z,fs=SR,nperseg=FFT,noverlap=FFT-HOP,
                        freq_axis=0,time_axis=-1,boundary=True)[1].T[:length].astype("float32")


def checksum(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()


def track_tonal_ridge(mag, guide_ratio, freq, midi_bounds=(65.0,84.5), midi_step=.2):
    """A smooth pitch path is only a mask guide; no pitch or waveform synthesis."""
    midi=np.arange(*midi_bounds,midi_step)  # The old repair uses a phrase-specific range; new audio passes broad bounds.
    candidates=440*2**((midi-69)/12)
    whiten=np.clip(np.log(mag+1e-9)-ndimage.gaussian_filter1d(np.log(mag+1e-9),8,axis=0),0,4)
    relative=mag/(np.max(mag,axis=0,keepdims=True)+1e-8)
    score=np.zeros((len(candidates),mag.shape[1]),dtype="float32")
    for k,weight in [(1,1.4),(2,.8),(3,.5),(4,.35),(5,.2),(6,.15)]:
        q=candidates*k/(SR/FFT)
        lo=np.floor(q).astype(int);hi=np.minimum(lo+1,len(freq)-1);mix=(q-lo)[:,None]
        values=whiten*(.35+.65*guide_ratio)*np.sqrt(relative)
        score+=weight*(values[lo]*(1-mix)+values[hi]*mix)
    # Prefer continuous paths, but allow note changes. These are selection priors.
    cost=.13*np.abs(midi[:,None]-midi[None,:])
    previous=score[:,0].copy();back=np.zeros_like(score,dtype="int16")
    for t in range(1,score.shape[1]):
        paths=previous[:,None]-cost
        best=np.argmax(paths,axis=0)
        back[:,t]=best;previous=score[:,t]+paths[best,np.arange(len(best))]
    indices=np.zeros(score.shape[1],dtype=int);indices[-1]=int(np.argmax(previous))
    for t in range(len(indices)-1,0,-1):indices[t-1]=back[indices[t],t]
    f0=candidates[indices]
    harmonic=np.zeros_like(mag)
    for k in range(1,11):
        center=k*f0[None,:];width=np.maximum(18,.025*center)
        harmonic=np.maximum(harmonic,np.exp(-.5*((freq[:,None]-center)/width)**2))
    tonal=np.clip((whiten-.25)/1.25,0,1)
    harmonic_energy=np.sum((mag*harmonic)**2,axis=0)+1e-15
    guide_energy=np.sum((mag*guide_ratio*harmonic)**2,axis=0)
    confidence=np.clip((np.sqrt(guide_energy/harmonic_energy)-.06)/.25,0,1)
    confidence=ndimage.gaussian_filter1d(confidence,SR/HOP*.06)
    return f0,harmonic*tonal*confidence[None,:],confidence


def recover():
    REPAIR.mkdir(exist_ok=True)
    original_paths=[BASE/"karaoke_full/vocals.wav",BASE/"cleaned/lead_balanced.wav",
                    OUT/"audio/vocals/vocal_lead_enhanced.mp3",OUT/"lossless/vocals/vocal_lead_enhanced.flac"]
    before={str(p):checksum(p) for p in original_paths}
    all_v=excerpt(BASE/"vocals_full/vocals.wav")
    lead=excerpt(BASE/"karaoke_full/vocals.wav")
    guide=excerpt(BASE/"bve_full/instrumental.wav")
    cleaned_all=excerpt(BASE/"cleaned/all_balanced.wav")
    zv,zl,zg,zc=(transform(x) for x in (all_v,lead,guide,cleaned_all))
    freq=np.fft.rfftfreq(FFT,1/SR)
    times=CONTEXT_START+np.arange(zv.shape[-1])*HOP/SR
    magnitude=lambda z:np.sqrt(np.mean(np.abs(z)**2,axis=1))
    mag=magnitude(zv)
    ratio=np.clip(magnitude(zg)/(mag+1e-8),0,1)
    missing=np.clip((1-magnitude(zl)/(mag+1e-8)-.1)/.6,0,1)
    guide_mask=np.clip((ratio-.06)/.60,0,1)
    f0,ridge,confidence=track_tonal_ridge(mag,ratio,freq)
    # Select original residual components; the alternate model supplies no audio.
    mask=np.maximum(guide_mask,.90*ridge)*missing
    # Stop recovering when the alternate lead disappears into the following bed.
    global_ratio=np.sqrt(np.mean(magnitude(zg)**2,axis=0)/(np.mean(mag**2,axis=0)+1e-15))
    admission=np.clip((global_ratio-.04)/.20,0,1)
    admission=ndimage.gaussian_filter1d(admission,SR/HOP*.04)
    mask*=admission[None,:]
    band=np.clip((freq-100)/50,0,1)*np.clip((15500-freq)/1500,0,1)
    mask*=band[:,None]
    mask=ndimage.gaussian_filter(mask,sigma=(.8,3.0),mode="nearest")
    delta_z=(zv-zl)*mask[:,None,:]
    # Weaker cleanup for the recovered effect: never rerun the missing lead mask.
    soft_clean=.8+.2*np.clip(magnitude(zc)/(mag+1e-8),0,1)
    delta=inverse(delta_z,len(all_v))
    delta_clean=inverse(delta_z*soft_clean[:,None,:],len(all_v))
    delta_clean=signal.sosfilt(signal.butter(2,55,"highpass",fs=SR,output="sos"),delta_clean,axis=0).astype("float32")
    sample_times=CONTEXT_START+np.arange(len(delta))/SR
    ramp=.18
    window=np.zeros(len(delta),dtype="float32")
    middle=(sample_times>=START+ramp)&(sample_times<=END-ramp)
    window[middle]=1
    rise=(sample_times>=START)&(sample_times<START+ramp)
    fall=(sample_times>END-ramp)&(sample_times<END)
    window[rise]=.5-.5*np.cos(np.pi*(sample_times[rise]-START)/ramp)
    window[fall]=.5-.5*np.cos(np.pi*(END-sample_times[fall])/ramp)
    delta*=window[:,None];delta_clean*=window[:,None]
    full_raw=read(BASE/"karaoke_full/vocals.wav")
    full_clean=read(BASE/"cleaned/lead_balanced.wav")
    repaired_raw=full_raw.copy();repaired_clean=full_clean.copy()
    lo,hi=round(CONTEXT_START*SR),round(CONTEXT_END*SR)
    repaired_raw[lo:hi]+=delta;repaired_clean[lo:hi]+=delta_clean
    start,end=round(START*SR),round(END*SR)
    assert np.array_equal(repaired_clean[:start],full_clean[:start])
    assert np.array_equal(repaired_clean[end:],full_clean[end:])
    assert np.array_equal(repaired_raw[:start],full_raw[:start])
    assert np.array_equal(repaired_raw[end:],full_raw[end:])
    assert np.isfinite(repaired_clean).all() and repaired_clean.shape==full_clean.shape
    sf.write(REPAIR/"lead_continuous_raw.wav",repaired_raw,SR,subtype="FLOAT")
    sf.write(REPAIR/"lead_continuous_clean.wav",repaired_clean,SR,subtype="FLOAT")
    sf.write(REPAIR/"recovered_component.wav",delta_clean,SR,subtype="FLOAT")
    report={"patch_seconds":[START,END],"context_seconds":[CONTEXT_START,CONTEXT_END],
            "sample_rate":SR,"frames":len(full_clean),"crossfade_seconds":ramp,
            "old_files_sha256":before,"original_files_unchanged":all(checksum(p)==s for p,s in before.items()),
            "outside_patch_sample_identical":True,"generative_filling":False,"pitch_correction":False,
            "audio_source":"Original RoFormer all-vocals minus original Karaoke lead",
            "guide":"BVE lead magnitude + temporally regularized harmonic-ridge selection; guide audio is not spliced in",
            "cleanup":"Recovered component only: 55 Hz highpass and 80% minimum retention of old all-vocal cleanup magnitude ratio",
            "pitch_tracking_is_estimate":True,"subjective_quality_verified":False,
            "ridge_hz_by_100ms":[],"rms_by_second":[]}
    rmsdb=lambda x:float(20*np.log10(np.sqrt(np.mean(x.astype(float)**2))+1e-12))
    for sec in np.arange(68,73.1,.1):
        i=int(np.argmin(np.abs(times-sec)))
        report["ridge_hz_by_100ms"].append({"time":round(float(sec),2),"hz":round(float(f0[i]),2),"guide_confidence":round(float(confidence[i]),3)})
    for sec in range(66,77):
        q=slice(sec*SR,(sec+1)*SR)
        report["rms_by_second"].append({"time":sec,"old_clean_dbfs":rmsdb(full_clean[q]),"repaired_dbfs":rmsdb(repaired_clean[q])})
    assert report["original_files_unchanged"]
    (REPAIR/"processing.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report["rms_by_second"],indent=2),flush=True)
    return repaired_clean,full_clean,report


def save_media(key, mode, wave, scale):
    """Use the SAME original listening gain for old / recovered comparisons."""
    assert np.isfinite(wave).all()
    wav=REPAIR/f"{key}_{mode}_listen.wav"
    mp3=f"audio/vocals/{key}_{mode}.mp3"
    flac=f"lossless/vocals/{key}_{mode}.flac"
    sf.write(wav,wave*scale,SR,subtype="PCM_24")
    sf.write(OUT/flac,wave,SR,format="FLAC",subtype="PCM_24")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),"-y","-hide_banner","-loglevel","error",
                    "-i",str(wav),"-c:a","libmp3lame","-b:a","256k",str(OUT/mp3)],check=True)
    return mp3,flac


def inject_tracks(tracks, additions):
    ids={t["id"] for t in additions}
    tracks=[t for t in tracks if t["id"] not in ids]
    full,clip=additions
    anchor=next(i for i,t in enumerate(tracks) if t["id"]=="vocal_lead")
    tracks.insert(anchor+1,full)
    anchor=next((i for i,t in enumerate(tracks) if t.get("scope")=="candidates"),len(tracks))
    tracks.insert(anchor,clip)
    return tracks


def publish(repaired,old,report):
    manifest_path=OUT/"manifest.json"
    manifest=json.loads(manifest_path.read_text())
    backup=REPAIR/"manifest_before_repair.json"
    if not backup.exists():backup.write_text(manifest_path.read_text())
    previous=next(t for t in manifest["tracks"] if t["id"]=="vocal_lead")
    matched=read(BASE/"exports/vocal_lead_enhanced.wav")
    # Least-squares scale recovers the unrounded gain used by the earlier demo.
    scale=float(np.sum(matched.astype(float)*old)/np.sum(old.astype(float)**2))
    assert float(np.max(np.abs(repaired)))*scale < .82
    assert float(np.max(np.abs(repaired))) < .98
    mp3,flac=save_media("vocal_lead_continuous","enhanced",repaired,scale)
    gain=20*math.log10(scale)
    full={"id":"vocal_lead_continuous","label":"主唱 · 连续保留","scope":"vocal","group":"主要人声",
          "source":"01:08–01:15 局部回填 · BVE 引导 + 谐波路径选择 · 原始残差信号",
          "video_start_seconds":0,"duration":len(repaired)/SR,"sample_rate":SR,"weak":False,
          "audio":{"raw":previous["audio"]["enhanced"],"enhanced":mp3},
          "lossless":{"raw":previous["lossless"]["enhanced"],"enhanced":flac},
          "mode_labels":{"raw":"原主唱 · 保守清理","enhanced":"连续保留 · 局部修复"},
          "comparison_level_policy":"shared_gain_original_reference","listening_gain_db":gain,
          "note":"仅修复 01:08–01:15；原版本未覆盖。回填来自已有全部人声残差，不是生成式补唱。尽量保住仿电吉他的人声谐波，仍可能带回少量背景成分。",
          "processing_note":"原主唱 / 修复采用同一试听增益，不通过把整首变响制造效果。未归一化的无损样本在修复范围外与原保守清理版完全相同。"}
    start,end=66,77
    a,b=start*SR,end*SR
    reference=excerpt(BASE/"vocals_full/vocals.wav",start,end)
    clip={"id":"vocal_continuity_patch","label":"01:06 · 主唱连续修复对照","scope":"candidates","group":"指定时段修复",
          "source":"01:06–01:17 原速试听 · 修复点在 01:08–01:15",
          "video_start_seconds":start,"duration":end-start,"sample_rate":SR,"weak":False,
          "audio":{},"lossless":{},"comparison_level_policy":"shared_gain_original_reference","listening_gain_db":gain,
          "mode_labels":{"raw":"原主唱 · 保守清理","enhanced":"连续保留 · 局部修复","source":"全部人声 · 来源参考"},
          "note":"重点听 01:10–01:12：原主唱中的漏分谐波已用原有信号局部找回。来源参考含其他人声 / 背景，不能把它全部视为主唱。",
          "processing_note":"三个版本使用同一增益。修复不拉伸、不变调、不生成音符；回填强度随引导和谐波连续性变化，可能残留背景。"}
    for mode,wave in {"raw":old[a:b],"enhanced":repaired[a:b],"source":reference}.items():
        p,w=save_media(clip["id"],mode,wave,scale)
        clip["audio"][mode]=p;clip["lossless"][mode]=w
    additions=[full,clip]
    meta={"tracks":additions,"default_track":clip["id"],"report":"CONTINUITY_REPAIR.md"}
    (REPAIR/"additions.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    manifest["tracks"]=inject_tracks(manifest["tracks"],additions)
    manifest["default_track"]=clip["id"]
    manifest["continuity_repair"]={"report":"CONTINUITY_REPAIR.md","processing":"continuity_processing.json",
                                   "patch_seconds":[START,END],"old_versions_preserved":True}
    from build_audio_insights import enrich_report
    manifest=enrich_report(manifest)
    template=(BASE/"player_template.html").read_text()
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    (OUT/"index.html").write_text(template.replace("__MANIFEST__",json.dumps(manifest,ensure_ascii=False)))
    report["listening_scale"]=scale;report["listening_gain_db"]=gain
    report["old_files_still_unchanged_after_export"]=all(checksum(p)==s for p,s in report["old_files_sha256"].items())
    assert report["old_files_still_unchanged_after_export"]
    (OUT/"continuity_processing.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    docs=["# 01:12 附近的主唱连续性修复","",
          "试听窗口 01:06–01:17；允许修复范围 01:08–01:15。回填依赖已有旋律成分，检测到其结束后停止，不把整个背景段拼回主唱。全部本地、原速处理。","",
          "## 做了什么","",
          "从 RoFormer 全部人声减去原 Karaoke 主唱，得到原有的漏分 / 背景残差。以 BVE 主唱的幅度和时间连续的谐波路径作为软选择引导，仅回填被原主唱漏掉的时频成分。BVE 的音频本身没有拼接进来；回填保留原始残差相位，不变调、不拉伸、不生成音符。",
          "修复成分只做轻微低频清理与很弱的频谱抑制；窗口两侧用 180 ms 余弦淡化衔接。整首其他位置沿用原保守清理样本。",
          "原主唱 / 修复采用原 demo 的同一试听增益，而不是分别归一化响度。声音变多是找回了被漏分成分，不是把整首主唱放大。","",
          "## 验证","",
          "原主唱源文件、原主唱 MP3 和 FLAC 的 SHA256 未变。未归一化的修复无损样本在 01:08–01:15 之外逐样本等于原保守清理版。",
          "|原视频秒数|原主唱清理 RMS dBFS|修复 RMS dBFS|","|---|---:|---:|"]
    for row in report["rms_by_second"]:
        docs.append(f"|{row['time']}|{row['old_clean_dbfs']:.2f}|{row['repaired_dbfs']:.2f}|")
    docs += ["","上述能量变化不是 SNR 或音质提升分数，没有真实独立原轨作标准。谐波路径与引导均为估计，可能带回和声 / 伴奏或仍漏掉部分特效细节。技术检查不能代替主观 A/B 听辨。",
             "","## 文件","",
             f"- [修复片段 MP3]({clip['audio']['enhanced']})",
             f"- [原主唱片段 MP3]({clip['audio']['raw']})",
             f"- [全片连续保留 MP3]({full['audio']['enhanced']})",
             f"- [全片修复 FLAC]({full['lossless']['enhanced']})",
             "- 参数和未改动文件校验见 continuity_processing.json。",
             "","新修复包为 vocal_continuity_repair.zip；上一版大试听包和无损包没有覆盖，仍保留原版本。"]
    (OUT/"CONTINUITY_REPAIR.md").write_text("\n".join(docs))
    marker="## 01:12 主唱连续性修复"
    readme=OUT/"README.md"
    if marker not in readme.read_text():
        readme.write_text(readme.read_text()+"\n\n"+marker+"\n\n新增「主唱 · 连续保留」与「01:06 · 主唱连续修复对照」。原版保留。详见 [修复说明](CONTINUITY_REPAIR.md)；新增文件在独立修复包，上一版大包未覆盖。\n")
    package_repair(manifest,[t for t in manifest['tracks'] if t['id'] in {x['id'] for x in additions}],template)
    print(f"PUBLISHED {clip['id']}; same listening gain {gain:.5f} dB; originals preserved",flush=True)


def package_repair(manifest,tracks,template):
    portable={**manifest,"tracks":tracks,"default_track":"vocal_continuity_patch",
              "intro":"01:06–01:17 修复对照；可切换全片连续保留。默认原速。",
              "vocal_evaluation":{"report":"CONTINUITY_REPAIR.md"}}
    archive=ROOT/"outputs/vocal_continuity_repair.zip"
    temp=archive.with_suffix(".zip.tmp")
    paths={p for t in tracks for p in [*t["audio"].values(),*t["lossless"].values(),*t.get('waveforms',{}).values()]}
    paths.update('assets/'+p.name for p in (OUT/'assets').iterdir())
    with zipfile.ZipFile(temp,"w",zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        z.writestr("instrument_demo/index.html",template.replace("__MANIFEST__",json.dumps(portable,ensure_ascii=False)))
        z.writestr("instrument_demo/manifest.json",json.dumps(portable,ensure_ascii=False,indent=2))
        z.writestr("instrument_demo/README.md","# 主唱连续修复试听\n\n解压后打开 index.html，默认原速。原主唱、局部修复、全部人声来源参考可同位置切换。详情见 CONTINUITY_REPAIR.md。\n")
        for name in ["CONTINUITY_REPAIR.md","continuity_processing.json","AUDIO_EVALUATION.md","audio_evaluation.json","processing_defaults.json",*sorted(paths)]:
            z.write(OUT/name,"instrument_demo/"+name)
    with zipfile.ZipFile(temp) as z:assert z.testzip() is None
    temp.replace(archive)
    print(f"ARCHIVE {archive.name}: {archive.stat().st_size/1024**2:.1f} MiB",flush=True)


def excerpt(path, start=64, end=78, offset=0):
    with sf.SoundFile(path) as f:
        assert f.samplerate == SR and f.channels == 2
        f.seek(round((start-offset)*SR))
        x = f.read(round((end-start)*SR), dtype="float32", always_2d=True)
    assert len(x) == round((end-start)*SR)
    return x


def diagnostic(sources=None,filename="diagnostic.png"):
    REPAIR.mkdir(exist_ok=True)
    six = ROOT / "work/demucs-detailed/separated/htdemucs_6s/original_demo"
    ft = ROOT / "work/demucs-detailed/separated/htdemucs_ft/original_demo"
    sources = sources or [
        ("All vocals / RoFormer", BASE/"vocals_full/vocals.wav", 0),
        ("Lead / Karaoke", BASE/"karaoke_full/vocals.wav", 0),
        ("Backing / Karaoke", BASE/"karaoke_full/instrumental.wav", 0),
        ("Lead / BVE", BASE/"bve_full/instrumental.wav", 0),
        ("Vocals / Demucs FT", ft/"vocals.wav", 45),
        ("Guitar / Demucs 6s", six/"guitar.wav", 45),
    ]
    width, height, left = 1000, 190, 36
    canvas = Image.new("RGB", (1080, len(sources)*(height+45)+25), "#f5f4f0")
    draw = ImageDraw.Draw(canvas)
    colors=np.array([[13,18,40],[38,44,103],[99,51,121],[186,73,92],[238,133,53],[248,219,103]],dtype=float)
    levels={}
    for index,(label,path,offset) in enumerate(sources):
        x=excerpt(path,offset=offset)
        small=signal.resample_poly(x,1,4,axis=0)
        f,t,z=signal.stft(small,fs=SR/4,nperseg=2048,noverlap=1920,axis=0)
        mag=np.sqrt(np.mean(np.abs(z)**2,axis=1))
        db=20*np.log10(mag+1e-10)
        target_f=np.geomspace(80,4000,height)[::-1]
        sampled=np.array([np.interp(target_f,f,db[:,i]) for i in range(len(t))]).T
        intensity=np.clip((sampled+75)/55,0,1)
        coord=intensity*(len(colors)-1)
        low=np.floor(coord).astype(int);high=np.minimum(low+1,len(colors)-1)
        rgb=colors[low]*(1-(coord-low))[...,None]+colors[high]*(coord-low)[...,None]
        panel=Image.fromarray(rgb.astype("uint8")).resize((width,height))
        top=25+index*(height+45)
        draw.text((left,top),label,fill="#23332d")
        canvas.paste(panel,(left,top+18))
        for sec in range(64,79):
            xpos=left+int((sec-64)/14*width)
            draw.line((xpos,top+18,xpos,top+18+height),fill="#8c8b8b",width=1)
            draw.text((xpos,top+height+20),str(sec),fill="#23332d")
        for sec in (70,71,72):
            xpos=left+int((sec-64)/14*width)
            draw.line((xpos,top+18,xpos,top+18+height),fill="#eeeeee",width=1)
        blocks=x.reshape(14,SR,2)
        levels[label]=(20*np.log10(np.sqrt(np.mean(blocks.astype(float)**2,axis=(1,2)))+1e-12)).tolist()
    canvas.save(REPAIR/filename)
    (REPAIR/(Path(filename).stem+"_levels.json")).write_text(json.dumps({"start_seconds":64,"levels":levels},indent=2))
    print(f"Saved {REPAIR/filename}",flush=True)


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--analyze-only",action="store_true")
    parser.add_argument("--refresh-player",action="store_true")
    args=parser.parse_args()
    if args.analyze_only:
        diagnostic()
    elif args.refresh_player:
        manifest=json.loads((OUT/"manifest.json").read_text())
        extra={"tracks":[t for t in manifest['tracks'] if t['id'] in ['vocal_lead_continuous','vocal_continuity_patch']]}
        template=(BASE/"player_template.html").read_text()
        (OUT/"index.html").write_text(template.replace("__MANIFEST__",json.dumps(manifest,ensure_ascii=False)))
        package_repair(manifest,extra["tracks"],template)
        diagnostic([( "Original cleaned lead",BASE/"cleaned/lead_balanced.wav",0),
                    ( "Continuity recovery (original signal)",REPAIR/"lead_continuous_clean.wav",0),
                    ( "All vocals source / reference",BASE/"vocals_full/vocals.wav",0)],"comparison.png")
    else:
        result=recover()
        publish(*result)
