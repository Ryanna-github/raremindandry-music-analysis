"""Actual stereo wave envelopes + explicitly heuristic, no-reference scores.

Re-run after any demo build. Cached by file SHA256, not by track label.
No third-party APIs; scores are NOT MOS, SNR, PESQ or separation accuracy.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal, ndimage

BASE=Path(__file__).resolve().parent
OUT=BASE.parent.parent/'outputs/instrument_demo'
CACHE=BASE/'insights_cache'
VERSION='audio_proxies_v1.1'


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def frames_rms(x,sr,step=.1):
    hop=round(sr*step);pad=(-len(x))%hop
    x=np.pad(x,((0,pad),(0,0)))
    return np.sqrt(np.mean(x.reshape(-1,hop,2).astype(float)**2,axis=(1,2)))


def measure(x,sr):
    rms=frames_rms(x,sr);db=20*np.log10(rms+1e-12)
    threshold=max(-60,float(np.percentile(db,95))-30)
    active=db>threshold
    step=np.abs(np.diff(db));steady=active[1:]&active[:-1]
    # Only abrupt envelope changes in active interiors, not note attacks or pauses.
    jerk=np.abs(np.diff(db,n=2));interior=active[2:]&active[1:-1]&active[:-2]
    pumping=float(np.mean(jerk[interior]>9)) if interior.any() else 0
    short=signal.resample_poly(x,1,6,axis=0)
    f,t,z=signal.stft(short.mean(axis=1),fs=sr/6,nperseg=1024,noverlap=512)
    p=np.abs(z)**2+1e-18;band=(f>150)&(f<min(3500,sr/12))
    flat=np.exp(np.mean(np.log(p[band]),axis=0))/np.mean(p[band],axis=0)
    spec_power=np.sum(p,axis=0); spec_active=spec_power>np.percentile(spec_power,95)*.001
    flatness=float(np.median(flat[spec_active])) if spec_active.any() else 0
    crest=float(20*np.log10((np.max(np.abs(x))+1e-12)/(np.sqrt(np.mean(x.astype(float)**2))+1e-12)))
    # Lowest decile relative to active median: an ambiguous noise-floor cue, not SNR.
    floor_gap=float(np.median(db[active])-np.percentile(db,10)) if active.any() else 0
    quiet=db<float(np.median(db[active])-22) if active.any() else np.ones(len(db),bool)
    labels,n=ndimage.label(quiet);holes=0
    for k in range(1,n+1):
        idx=np.flatnonzero(labels==k)
        if 1<len(idx)<=8 and idx[0]>0 and idx[-1]<len(db)-1:
            if db[idx[0]-1]>threshold+6 and db[idx[-1]+1]>threshold+6: holes+=len(idx)
    return {'rms_dbfs':round(float(20*np.log10(np.sqrt(np.mean(x.astype(float)**2))+1e-12)),3),
        'peak':round(float(np.max(np.abs(x))),6),'near_full_scale_fraction':float(np.mean(np.abs(x)>=.999)),
        'active_fraction':round(float(np.mean(active)),4),'spectral_flatness':round(flatness,5),
        'floor_to_active_gap_db':round(floor_gap,3),'active_envelope_pumping_fraction':round(pumping,5),
        'short_hole_fraction':round(holes/max(int(active.sum()),1),5),'crest_db':round(crest,3),
        'levels_100ms_dbfs':np.round(db,2).tolist()}


def wave(path,out=OUT):
    sha=digest(path);cache=CACHE/f'{sha}.json';asset=f'waveforms/{sha[:20]}.js'
    if cache.exists(): result=json.loads(cache.read_text())
    else:
        x,sr=sf.read(path,dtype='float32',always_2d=True)
        bins=min(2048,max(512,round(len(x)/sr*12)));hop=int(np.ceil(len(x)/bins))
        padded=np.pad(x,((0,hop*bins-len(x)),(0,0))).reshape(bins,hop,2)
        result={'schema_version':1,'sha256':sha,'duration_seconds':len(x)/sr,'sample_rate':sr,
          'hop_samples':hop,'bins':bins,'min':np.round(padded.min(axis=1).T,5).tolist(),
          'max':np.round(padded.max(axis=1).T,5).tolist(),
          'rms':np.round(np.sqrt(np.mean(padded.astype(float)**2,axis=1)).T,5).tolist(),
          'source':'Decoded original-speed listening MP3; real stereo min/max/RMS, no random waveforms',
          'measurements':measure(x,sr)}
        cache.write_text(json.dumps(result,separators=(',',':')))
    (out/asset).write_text('window.AUDIO_WAVES=window.AUDIO_WAVES||{};window.AUDIO_WAVES['+
        json.dumps(asset)+']='+json.dumps(result,separators=(',',':'))+';\n')
    return asset,result


def classify(t,mode):
    key=t['id']
    if key=='vocal_continuity_patch' and mode=='source':return 'vocal'
    if 'drum' in key or key=='other_percussive':return 'percussive'
    if key in ['original','vocal_original_mix','vocal_no_crowd']:return 'mix'
    if ('residual' in key) or key=='vocal_crowd':return 'residual'
    if key in ['vocal_lead','vocal_lead_continuous','lead','vocal_bve_lead','vocal_continuity_patch'] or mode in ['lead','lead_clean']:return 'lead'
    if 'vocal' in key or key in ['lead','backing']:return 'vocal'
    return 'tonal'


def scores(t,mode,measurement,expected):
    m=measurement;category=classify(t,mode)
    warnings=[];confidence='低' if category in ['residual','mix'] else '中低'
    weak=bool(t.get('weak')) or m['rms_dbfs']<-48
    # Broadband percussion and residuals must not be labeled noise by flatness.
    flat_penalty=0 if category in ['percussive','mix','residual'] else min(2.5,5*m['spectral_flatness'])
    floor_penalty=2*max(0,1-max(m['floor_to_active_gap_db'],0)/30)
    clean=9.2-floor_penalty-flat_penalty
    stability=9.4-min(4,16*m['active_envelope_pumping_fraction'])
    if category=='percussive': stability=9.4;warnings.append('鼓 / 打击乐：自然瞬态不作为抽动或杂音扣分，稳定性主要看数值完整性。')
    continuity=9.2-min(3,12*m['short_hole_fraction']);dropout=None
    if category=='lead':
        lv=np.array(m['levels_100ms_dbfs'])
        start=t.get('video_start_seconds',45); times=start+np.arange(len(lv))*.1
        ref=np.interp(times,expected['times'],expected['all_db'])
        mask=np.zeros(len(lv),bool)
        for a,b in expected['windows']:mask|=(times>=a)&(times<b)
        # Undo the documented listening gain; do not punish equal-gain recovery for becoming louder.
        gain=t.get('listening_gain_db',t.get('levels',{}).get(mode,{}).get('listening_gain_db',0))
        loss=ref-(lv-gain)
        if mask.any():
            dropout=float(np.mean(np.clip((loss[mask]-8)/22,0,1)))
            continuity=9.2-7*dropout
        warnings.append('连续性使用两模型分歧窗口内的主唱 / 全人声相对衰减；不是主唱真实分轨，和声仍可能影响得分。')
    integrity=9.8-min(9,500*m['near_full_scale_fraction'])
    quality=.35*clean+.30*continuity+.20*stability+.15*integrity
    if category=='residual':
        warnings.append('残差 / 人群候选的身份未确认，音质与杂音分只是信号特征，不用于证明有观众或纯噪声。')
        confidence='低'
    if weak:
        confidence='很低 · 信号不足';quality=min(quality,4.9)
        warnings.append('信号过弱或原模型标为弱轨：综合音质上限 4.9 为保守展示规则；不能把安静误判为高质量，其他分项也不可靠。')
    if t['id']=='vocal_dry':
        warnings.append('此去混响模型相对输入损失约 14.7 dB；音质综合额外扣 2 分，安静不代表修复成功。')
        quality=max(0,quality-2);confidence='低'
    values={'quality':quality,'cleanliness':clean,'stability':stability,'continuity':continuity,'integrity':integrity}
    return {'method':VERSION,'scores':{k:round(float(np.clip(v,0,10)),1) for k,v in values.items()},
        'confidence':confidence,'higher_is_better':True,'category':category,'weak_signal':weak,
        'estimated_not_subjective':True,'dropout_risk_in_candidate_windows':None if dropout is None else round(dropout,5),
        'measurements':{k:v for k,v in m.items() if k!='levels_100ms_dbfs'},'warnings':warnings}


def expected_lead(paths=None):
    from continuous_vocals import find_candidates
    from clean_vocals import read
    paths=paths or [BASE/p for p in ['vocals_full/vocals.wav','karaoke_full/vocals.wav','bve_full/instrumental.wav']]
    a,l,g=(read(p) for p in paths)
    cfg=json.loads((BASE/'processing_defaults.json').read_text())['continuity']
    windows=find_candidates(a,l,g,cfg)
    av=frames_rms(a,44100)
    return {'times':np.arange(len(av))*.1,'all_db':20*np.log10(av+1e-12),'windows':windows}


def enrich_report(report,out=OUT,expected=None):
    (out/'waveforms').mkdir(exist_ok=True);CACHE.mkdir(exist_ok=True)
    expected=expected or expected_lead(); rows=[]
    for t in report['tracks']:
        t['waveforms']={};t['evaluation']={}
        for mode,path in t['audio'].items():
            asset,result=wave(out/path,out);t['waveforms'][mode]=asset
            t['evaluation'][mode]=scores(t,mode,result['measurements'],expected)
            rows.append({'id':t['id'],'label':t['label'],'mode':mode,**t['evaluation'][mode]})
        print('INSIGHTS',t['id'],t['evaluation'][next(iter(t['audio']))]['scores'],flush=True)
    report['audio_insights']={'schema_version':1,'report':'AUDIO_EVALUATION.md','data':'audio_evaluation.json',
        'method':VERSION,'higher_is_better':True,'reference_ground_truth':False,'waveform_source':'real_decoded_listening_audio',
        'dimensions':{'quality':'音质 · 代理综合','cleanliness':'洁净度 · 杂音越少分越高','stability':'稳定性','continuity':'连续性','integrity':'数值完整性'}}
    report['processing_defaults']=json.loads((BASE/'processing_defaults.json').read_text())
    report['waveform_view']={'default':'unified','track_policy':'hierarchical_one_node_per_type' if report.get('hierarchy') else 'one_current_or_recommended_version_per_track',
        'time_alignment':'absolute_video_seconds','outside_track_coverage':'blank',
        'spacing_zoom_independent_of_camera':True,'single_track_audio_playback':False,'rotation':'360_free_orbit',
        'geometry':'single_body_stereo_envelope','stereo_audio_preserved':True,
        'playback':'selected_stems_mix_with_fixed_headroom','time_axis':'drag_pan_click_seek_and_play'}
    (out/'processing_defaults.json').write_text(json.dumps(report['processing_defaults'],ensure_ascii=False,indent=2))
    (out/'audio_evaluation.json').write_text(json.dumps({'method':VERSION,'candidate_windows':expected['windows'],
        'subjective_listening_test':False,'reference_ground_truth':False,'evaluations':rows},ensure_ascii=False,indent=2))
    write_docs(rows,expected['windows'],out)
    (out/'assets').mkdir(exist_ok=True)
    for path in (BASE/'web').iterdir():shutil.copy2(path,out/'assets'/path.name)
    return report


def write_docs(rows,windows,out=OUT):
    content=['# 音轨评价：无参考代理评分 v1.1','',
        '所有分数为 0–10 分、保留一位小数，越高越好。它们是本地信号特征映射，不是人类听测 MOS，不是本曲 SNR / SDR / PESQ，也不是分离准确率。没有真实原始分轨，无法确证某个声音是杂音还是乐器 / 和声。',
        '','## 可复算规则','',
        '- 洁净度 C = 9.2 − 2×max(0,1−max(G,0)/30) − min(2.5,5×F)。G 为最低十分位包络与活跃中位包络的差值（dB），F 为 150–3500Hz 活跃频谱平坦度中位数。G 不是 SNR，F 也可能是气声或失真特效。鼓、混音、残差不使用 F 扣分。',
        '- 稳定性 S = 9.4 − min(4,16×P)。P 为 100ms 活跃内部包络二阶差分超过 9dB 的比例。跳过停顿；仍可能将真实唱法变化当成不稳定。鼓轨不按瞬态扣分，S=9.4 只表示这项检查未发现可用的抽动证据，不表示鼓声主观稳定。',
        '- 连续性 T = 9.2 − min(3,12×H)。H 为 0.2–0.8 秒内部深凹洞占活跃帧的比例；自然停顿仍可能触发误报。主唱改用两模型分歧窗口内平均 D：T=9.2−7D，D=clip((相对全人声衰减−8dB)/22dB,0,1)。主唱相对衰减先撤销已记录试听增益。只有分歧候选窗口参与，不把全曲和声独唱段全部算作主唱漏分。',
        '- 数值完整性 I = 9.8 − min(9,500×近满幅样本比例)。只检测当前编码文件数值溢出风险，不会评估所有失真；原有电音不按创作风格扣分。',
        '- 音质 Q = .35C + .30T + .20S + .15I。弱轨 Q 上限 4.9 是保守展示规则而非校准量表；本曲严重去混响诊断轨因已知 14.7dB 信号移除风险另扣 2 分。',
        '- 指标从实际试听 MP3 解码计算（44100Hz 双声道），波形同源。无损文件可能保留不同电平；评分不代表无损信号的听测结论。',
        '',f'本曲自动主唱分歧候选窗口（秒）：{windows}。候选不是已确证的特效或歌手身份。',
        '', '## 使用方式与局限','',
        '主要用于同一轨、同一片段的原始 / 清理版本回听定位，不能按 0.1 分之差排名不同乐器。信号不足、残差、人群候选标低可信度；“没有检测到风险”不等于完美音质。增强不保证每项分数上升。全片分数可能掩盖局部缺口，可切到短片段查看。真实旋律、尾音是否保留仍需 A/B 回听。',
        '', '## 每条音轨 / 版本','', '|音轨|版本|音质|洁净度|稳定性|连续性|数值完整性|可信度|','|---|---|---:|---:|---:|---:|---:|---|']
    for r in rows:
        s=r['scores'];content.append(f"|{r['label']}|{r['mode']}|{s['quality']:.1f}|{s['cleanliness']:.1f}|{s['stability']:.1f}|{s['continuity']:.1f}|{s['integrity']:.1f}|{r['confidence']}|")
    (out/'AUDIO_EVALUATION.md').write_text('\n'.join(content)+'\n')


def main():
    report=enrich_report(json.loads((OUT/'manifest.json').read_text()))
    if report.get('hierarchy'):report['default_track']=report['hierarchy']['vocal_root_id']
    elif any(t['id']=='vocal_continuity_patch' for t in report['tracks']):report['default_track']='vocal_continuity_patch'
    (OUT/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (OUT/'index.html').write_text((BASE/'player_template.html').read_text().replace('__MANIFEST__',json.dumps(report,ensure_ascii=False)))
    print('DONE',len(report['tracks']),'tracks',sum(len(t['audio']) for t in report['tracks']),'versions',flush=True)


if __name__=='__main__':main()
