"""Add full-length vocal comparisons to the existing original-speed demo."""
import json
import math
import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf

from clean_vocals import read, model_source, block_levels, SR
from analyze import bve_paths

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent.parent
OUT=ROOT/"outputs/instrument_demo"
FFMPEG=imageio_ffmpeg.get_ffmpeg_exe()


def db(x):
    return float(20*np.log10(np.sqrt(np.mean(x.astype(np.float64)**2))+1e-12))


def export(track, waves, start=0):
    track.update({"video_start_seconds":start,"duration":len(next(iter(waves.values())))/SR,
                  "sample_rate":SR,"audio":{},"lossless":{},"levels":{}})
    raw=next(iter(waves.values()))
    powers,hop=block_levels(raw)
    levels=10*np.log10(powers+1e-12)
    active=levels>max(-55,float(np.percentile(levels,95))-25)
    sample_active=np.repeat(active,hop)[:len(raw)]
    if not np.any(sample_active):sample_active[:]=True
    active_db={mode:db(x[sample_active]) for mode,x in waves.items()}
    gains={mode:min(12,-20-active_db[mode]) if db(raw)>-48 else 0 for mode in waves}
    if track.get("scope") == "candidates":
        # Distinct source classes are not equally loud by design. Preserve ratios.
        base=gains["raw"]
        for mode in ("lead", "backing"):
            if mode in gains:gains[mode]=base
        if "lead_clean" in gains:
            gains["lead_clean"]=base+active_db["lead"]-active_db["lead_clean"]
    largest_peak=max(float(np.max(np.abs(x)))*10**(gains[mode]/20) for mode,x in waves.items())
    common_offset=min(0,20*math.log10(.82/max(largest_peak,1e-12)))
    for mode,x in waves.items():
        assert x.shape==raw.shape and np.isfinite(x).all()
        gain=gains[mode]+common_offset
        matched=(x*10**(gain/20)).astype(np.float32)
        audio_rel=f"audio/vocals/{track['id']}_{mode}.mp3"
        lossless_rel=f"lossless/vocals/{track['id']}_{mode}.flac"
        peak=float(np.max(np.abs(x)))
        archive_scale=min(1,.98/max(peak,1e-12))
        # Raw archives retain model estimates apart from documented peak protection.
        sf.write(OUT/lossless_rel,x*archive_scale,SR,format="FLAC",subtype="PCM_24")
        temporary=BASE/"exports"/f"{track['id']}_{mode}.wav"
        sf.write(temporary,matched,SR,subtype="PCM_24")
        subprocess.run([FFMPEG,"-y","-hide_banner","-loglevel","error","-i",str(temporary),
                        "-c:a","libmp3lame","-b:a","256k",str(OUT/audio_rel)],check=True)
        track["audio"][mode]=audio_rel;track["lossless"][mode]=lossless_rel
        track["levels"][mode]={"source_rms_dbfs":round(db(x),3),"active_rms_dbfs":round(active_db[mode],3),
            "listening_gain_db":round(gain,3),"listening_active_rms_dbfs":round(db(matched[sample_active]),3),
            "archive_scale":archive_scale,"source_peak":peak}
    track["weak"]=db(raw)<-48
    print(f"EXPORTED {track['id']}: {track['duration']:.2f}s, {len(waves)} versions",flush=True)
    return track


def main():
    for folder in [OUT/"audio/vocals",OUT/"lossless/vocals",BASE/"exports"]:folder.mkdir(parents=True,exist_ok=True)
    old=json.loads((OUT/"manifest.json").read_text())
    instrument=[t for t in old["tracks"] if t.get("scope","instrument")=="instrument"]
    for t in instrument:t.update({"scope":"instrument","video_start_seconds":45})
    analysis=json.loads((BASE/"analysis.json").read_text())
    cleanup=json.loads((BASE/"cleaned/processing.json").read_text())
    all_v=read(BASE/"vocals_full/vocals.wav");lead=read(BASE/"karaoke_full/vocals.wav")
    backing=read(BASE/"karaoke_full/instrumental.wav")
    clean={key:read(BASE/"cleaned"/f"{key}.wav") for key in ["all_balanced","all_clarity","lead_balanced","lead_clarity","backing_balanced"]}
    new=[]
    def add(key,label,source,group,note,waves,mode_labels=None,processing_note=None):
        t={"id":key,"label":label,"source":source,"group":group,"note":note,"scope":"vocal"}
        if mode_labels:t["mode_labels"]=mode_labels
        if processing_note:t["processing_note"]=processing_note
        new.append(export(t,waves));return t
    add("vocal_all","全部人声 · 清理对照","Mel-Band RoFormer → UVR 去混响 / 伴奏参照的保守清理","主要人声",
        "推荐先用保守版。较强版更侧重清晰度，可能减少气声、空间感或特效细节。原始版本仅做试听电平匹配。",
        {"raw":all_v,"enhanced":clean["all_balanced"],"clarity":clean["all_clarity"]})
    add("vocal_lead","主唱 · 清理对照","Mel-RoFormer Karaoke → 共享人声清理掩码","主要人声",
        "主唱估计，不是已识别某位歌手。清理沿用全人声的降噪 / 去混响引导，避免多轮模型直接重写唱腔；仍可能含和声或少掉尾音。",
        {"raw":lead,"enhanced":clean["lead_balanced"],"clarity":clean["lead_clarity"]})
    add("vocal_backing","和声 / 叠唱 / 背景 · 估计","Mel-RoFormer Karaoke 人声残差","主要人声",
        "可能同时包含和声、同一人的叠唱、主唱尾音和混响。保守清理为这一轨保留了更多余量，不表示已逐个分开所有声部。",
        {"raw":backing,"enhanced":clean["backing_balanced"]})
    alt_lead,alt_backing=bve_paths()
    reference="只做原速电平匹配；这是另一模型的直接估计，未进行连续清理。用于比较误分和串音，不是更准确的保证。"
    add("vocal_bve_lead","主唱 · BVE 模型对照","UVR-BVE-4B_SN · is_bv_model=true 的语义映射","另一种人声拆法",
        "可与 Karaoke 主唱在同一位置对比；模型原生 Instrumental 在此映射为主唱，不是伴奏。",{"raw":read(alt_lead)},processing_note=reference)
    add("vocal_bve_backing","背景人声 · BVE 模型对照","UVR-BVE-4B_SN · 原生 Vocals=背景人声","另一种人声拆法",
        "BVE 的原生 Vocals 是背景人声目标。两种拆法分歧大的时段需回听原曲，不能根据一条轨判断歌手人数。",{"raw":read(alt_backing)},processing_note=reference)
    crowd_report=json.loads((BASE/"crowd_full/result.json").read_text())
    exact={name.lower().replace(" ",""):path for name,path in crowd_report["outputs"].items()}
    add("vocal_crowd","人群 / 观众声 · 候选估计","UVR-MDX-NET_Crowd_HQ_1 · Crowd 残差","人群声评估",
        "不是合唱的逐人分离。可能误收鼓、伴奏、和声和混响；存在输出不证明录音中有人群。",{"raw":read(exact["crowd"])},processing_note=reference)
    add("vocal_no_crowd","去人群估计 · 全曲对照","UVR-MDX-NET_Crowd_HQ_1 · 原生 No Crowd","人群声评估",
        "保留歌曲和伴奏的去人群候选，不是纯人声。与原曲同位置对比，以检查是否误删歌曲细节。",{"raw":read(exact["nocrowd"])},processing_note=reference)
    dry=read(model_source("dereverb","No Reverb"));denoised=read(model_source("denoise","No Noise"))
    diagnostic="此项为诊断对照，只匹配试听电平。直接模型输出可能改变尾音、相位或特效；推荐清理版保留原始相位并限制了抑制强度。"
    add("vocal_dry","直接去混响 · 诊断对照","BS-RoFormer De-Reverb","清理诊断",
        "BS 模型在本片相对输入人声降低约 14.7 dB RMS，可能误删大量唱腔 / 特效；未作为推荐版的去混响引导。此项只用于显示这个风险。",{"raw":dry},processing_note=diagnostic)
    vr_dry=read(model_source("vr_deverb","Dry"))
    add("vocal_vr_dry","去混响 · UVR 模型对照","UVR-De-Reverb-aufr33-jarredou","清理诊断",
        "该模型在本片保留了更多人声能量，推荐清理版采用它作为有限的频谱抑制引导。能量保留更多不等于主观音质一定更好。",{"raw":vr_dry},processing_note=diagnostic)
    add("vocal_denoised","直接降噪 · 诊断对照","Mel-Band RoFormer Denoise · 原生 dry 目标","清理诊断",
        "专用降噪模型的直接结果，可能比保守版更干，也可能移走气声、辅音和特效。需要与原始人声确认。",{"raw":denoised},processing_note=diagnostic)
    add("vocal_noise_residual","降噪模型残差 · 诊断","原始人声 − Mel-RoFormer Denoise 输出","清理诊断",
        "残差中可能有杂音，也可能有气声、辅音、伴奏或特效。它不是确认的纯噪声轨。",{"raw":all_v-denoised},processing_note=diagnostic)
    add("vocal_reverb_residual","空间 / 混响残差 · 诊断","原始人声 − UVR 去混响输出","清理诊断",
        "用于检查被移走的空间成分；仍可能包含干声和串音，不能当成单独真实特效轨。",{"raw":all_v-vr_dry},processing_note=diagnostic)
    add("vocal_original_mix","原曲 · 全片参考","原视频音轨","原曲参考",
        "完整 03:43.64 音频，仅做试听电平匹配。",{"raw":read(BASE/"original_full.wav")},mode_labels={"raw":"原曲 · 原速"},processing_note="原视频音频在本地提取，没有上传第三方分离 API。")
    for c in analysis["candidates"]:
        start,end=round(c["start_seconds"]*SR),round(c["end_seconds"]*SR)
        t={"id":c["id"],"label":f"{int(c['start_seconds']//60):02d}:{int(c['start_seconds']%60):02d} · {c['label']}",
           "source":"全片自动变化候选 · 原速时间切片","group":"需要回听确认的时段","scope":"candidates","note":c["note"],
           "processing_note":"这些是时间切片，不是恢复特效前的原声。相同声源的原始 / 清理匹配电平；主唱 / 和声保留相对能量，不把弱和声抬到与主唱一样大。未做人工听辨确认。",
           "mode_labels":{"raw":"全人声 · 原始","enhanced":"全人声 · 清理","lead":"主唱估计","lead_clean":"主唱 · 清理","backing":"和声估计"}}
        new.append(export(t,{"raw":all_v[start:end],"enhanced":clean["all_balanced"][start:end],
                             "lead":lead[start:end],"lead_clean":clean["lead_balanced"][start:end],"backing":backing[start:end]},c["start_seconds"]))
    report={**old,"tracks":new+instrument,"default_track":"vocal_all","playback_speed_default":1,
        "intro":"全片人声 03:43.64；乐器片段 00:45–01:30。切换对齐原视频时间，默认原速；可手动降速或循环。",
        "vocal_evaluation":{"analysis":"vocal_analysis.json","processing":"vocal_processing.json","report":"VOCAL_REPORT.md"},
        "enhancement":"乐器：EQ 与电平匹配；人声：伴奏估计和模型引导的平滑频谱抑噪、有限去混响与温和动态稳定。未修音、未补造音节。",
        "slow":"默认原速，只在播放器中手动调速；不预生成降速文件。"}
    additions=BASE/"repair/additions.json"
    if additions.exists():
        from repair_continuity import inject_tracks
        extra=json.loads(additions.read_text())
        report["tracks"]=inject_tracks(report["tracks"],extra["tracks"])
        report["default_track"]=extra["default_track"]
    from build_audio_insights import enrich_report
    report=enrich_report(report)
    (OUT/"manifest.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (OUT/"index.html").write_text((BASE/"player_template.html").read_text().replace("__MANIFEST__",json.dumps(report,ensure_ascii=False)))
    (OUT/"vocal_analysis.json").write_text(json.dumps(analysis,ensure_ascii=False,indent=2))
    (OUT/"vocal_processing.json").write_text(json.dumps(cleanup,ensure_ascii=False,indent=2))
    model_reports={task:json.loads((BASE/f"{task}_full/result.json").read_text()) for task in ["vocals","karaoke","crowd","bve","dereverb","denoise","vr_deverb"]}
    (OUT/"vocal_models.json").write_text(json.dumps(model_reports,ensure_ascii=False,indent=2))
    write_docs(report,analysis,cleanup)
    print(f"BUILT {len(new)} vocal/candidate items + {len(instrument)} instrument items",flush=True)


def write_docs(report,analysis,cleanup):
    weights=cleanup
    balanced,clarity=weights["balanced"],weights["clarity"]
    text=["# 人声细分与连续清理评估","","## 范围与推荐试听","",
        "源视频实际时长 223.63957 秒（03:43.64）。本轮人声覆盖全片；乐器仍保留 00:45–01:30 的 45 秒 demo。全部推理本地执行，音频未上传分离 API。",
        "默认原速；未预生成 0.75 倍速结果。旧版已交付的 slow 文件保留，但不用于本轮播放器或新版压缩包。","",
        "先听「全部人声 · 清理对照」的保守版，再切原始。同一句想更清楚时切较强版；扒主旋律用「主唱 · 清理对照」。比较和声时同时参考 Karaoke 与 BVE，两者不是严格逐声部分轨。","",
        "## 增强具体做什么","",
        "1. 比较两种去混响模型，推荐版采用 UVR-De-Reverb-aufr33-jarredou 的频谱引导；BS-RoFormer 在本片移走太多人声，只保留为诊断对照。也运行专用 Mel-Band RoFormer 降噪评估。",
        "2. 对原始人声的频谱做平滑、有非零下限的衰减，保留原始相位与立体声布局；不直接用降噪模型合成的波形替换演唱。另用伴奏估计作为干扰参照，在伴奏远强于人声的时频位置保守降低串音；这也可能减弱重叠的辅音 / 气声，故始终保留原始对照。",
        f"3. 保守版噪声引导混合 {balanced['noise_mix']:.0%}、去混响引导混合 {balanced['dereverb_mix']:.0%}；较强版分别为 {clarity['noise_mix']:.0%}、{clarity['dereverb_mix']:.0%}。这些是软掩码混合权重，不是实测噪声移除百分比。",
        "4. 55 Hz 高通处理低频杂声，轻微、缓慢的动态稳定改善忽大忽小。无硬门限，无逐句音色替换，无变声、自动修音或生成式补唱。和声轨额外减弱了抑制。",
        "5. 试听版本在同一轨的活跃人声时间尽量匹配电平，并统一留峰值余量，避免单靠变响造成清晰度错觉。无损 raw 保留模型估计，只有必要的防溢出缩放，数值见 manifest。","",
        "连续性指避免清理过程切断弱音 / 尾音并平滑增益；如果初次分离已把字或音符丢掉，本方法不能真正补回。音色稳定指减少杂音和轻微稳定动态，不把不同歌手、唱法或原有电音强行统一。","",
        f"保留率检查：直接去混响相对输入人声的 RMS 变化为 {cleanup['retention_guard']['dereverb_rms_change_db']:.2f} dB，直接降噪为 {cleanup['retention_guard']['denoise_rms_change_db']:.2f} dB。全局下降超过 10 dB 时，降低该模型的引导权重，避免把大量被移走的人声直接当作杂音。能量下降不等于降噪效果或 SNR 提升。","",
        f"BS-RoFormer 去混响的 RMS 变化为 {cleanup['retention_guard']['bs_roformer_dereverb_rms_change_db']:.2f} dB；因此没有将它作为推荐清理引导。最终去混响引导是 UVR 模型，其输出只是估计，不是恢复出的原始干声。","",
        "本片专用降噪输出与输入几乎相同，其残差非常微弱，不能宣称它显著改善了音质。本轮清理的实际变化主要来自伴奏参照的软抑制、有限空间残留抑制、低频清理和动态稳定。","",
        "## 细分边界","",
        "|目标|本次交付|不能保证的部分|","|---|---|---|",
        "|主唱 / 单个人声|Karaoke 主唱估计、保守 / 较强清理、BVE 对照|不识别具体歌手；不是每个唱歌的人分别一轨|",
        "|和声 / 叠唱|Karaoke 残差、轻度清理、BVE 背景人声对照|同一人的 double-track、和声、混响可能混在一起；不自动确认人数|",
        "|人群 / 观众|Crowd 估计、No Crowd 全曲对照|模型可能误收鼓 / 伴奏 / 和声；不等于合唱逐人分离|",
        "|混响 / 空间|直接干声估计、空间残差|不是原录音的纯干声和纯混响真实轨|",
        "|电音 / 声码器 / 失真等|变化候选时段的多轨原速切片|未确认特效类型；不能可靠逆转已混入的人声处理|","",
        "BVE 模型元数据 is_bv_model=true；其原生 Vocals 映射为背景人声，原生 Instrumental 映射为主唱。Crowd 模型原生主轨为 No Crowd，另轨才是 Crowd。","",
        "## 自动寻找的变化候选","",
        "以下仅供定位回听：基于 1 秒步长的相对能量、谱形变化和模型残差，未进行人工听辨确认。谱形变化也可能来自歌词元音、换唱法和伴奏，不应当作特效检测准确率。","",
        "|原视频时段|候选类型|需要确认|","|---|---|---|"]
    def ts(x):return f"{int(x//60):02d}:{x%60:04.1f}"
    for c in analysis["candidates"]:text.append(f"|{ts(c['start_seconds'])}–{ts(c['end_seconds'])}|{c['label']}|{c['note']}|")
    text += ["","每个候选均可分别下载全人声原始、全人声清理、主唱估计、主唱清理、和声估计的 12 秒原速切片。",
        "","## 验证和局限","",
        "没有真实原始分轨，无法测本曲 SDR/SIR 或报告分离准确率。各模型文件名中的 SDR 是模型作者测试命名，不是本曲测量。未声称试听确认人群或特效。",
        "文件完整性、原速时长、时间对齐、立体声、非有限数和峰值验证见 verification.json；这类技术验证不等于主观音质已经通过。",
        "参数与增益见 manifest.json / vocal_processing.json，时间指标见 vocal_analysis.json，模型原生目标与运行数据见 vocal_models.json。"]
    (OUT/"VOCAL_REPORT.md").write_text("\n".join(text))
    rows=["|音轨|原速试听|无损文件|","|---|---|---|"]
    for t in report["tracks"]:
        m="enhanced" if "enhanced" in t["audio"] else "raw"
        lossless=t.get("lossless",{}).get(m,t.get("enhanced_wav" if m=="enhanced" else "raw_wav",""))
        rows.append(f"|{t['label']}|[MP3]({t['audio'][m]})|[无损]({lossless})|")
    (OUT/"README.md").write_text("\n".join(["# 人声清理与乐器扒谱 demo","",
        "全片人声 03:43.64；乐器片段 00:45–01:30。全部本地处理，默认原速，不预生成降速文件。","",
        "解压后打开 index.html。全片人声、乐器片段、变化候选分组试听；切版本保持定位，切轨在范围重叠时对齐原视频绝对时间。支持手动保音高调速与 A/B 循环。",
        "默认统一 3D 图展示全部轨道；每轨显示当前选择或推荐试听版。轨道间距、空间缩放和时间放大独立，范围外留空。左侧分类仅筛选列表。模型对照 / 残差 / 切片会重叠，统一展示不表示可以无重叠叠加，详见 UNIFIED_WAVEFORM.md。",
        "播放器的原始版本只做电平匹配。人声清理与乐器 EQ 不同；详见 [人声评估](VOCAL_REPORT.md)。主唱、和声、人群均为估计，不表示人数确定或特效已识别。",
        "只下载试听包时不含无损文件；需要无损请下载无损包。各组互相重叠，勿将所有轨一起叠加。","",*rows]))


if __name__=="__main__":main()
