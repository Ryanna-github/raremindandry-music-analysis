"""One node per source type: full-song roots, optional vocal children, per-node processing."""
import copy
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf
from scipy import ndimage,signal

from clean_vocals import read,stft,istft,SR
from build_audio_insights import enrich_report,frames_rms

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent.parent
OUT=ROOT/'outputs/instrument_demo'
WORK=BASE/'hierarchy'
MODEL=ROOT/'work/demucs-detailed/full_instruments'
FFMPEG=imageio_ffmpeg.get_ffmpeg_exe()
PROFILES={'guitar':'highpass=f=65,lowpass=f=12000,equalizer=f=1800:t=q:w=0.7:g=2',
          'bass':'highpass=f=25,lowpass=f=4500,equalizer=f=750:t=q:w=0.8:g=2',
          'drums':'highpass=f=30,lowpass=f=17000,equalizer=f=2500:t=q:w=0.8:g=1.5',
          'piano':'anull','other':'highpass=f=35,lowpass=f=16000'}


def db(x):return float(20*np.log10(np.sqrt(np.mean(x.astype(float)**2))+1e-12))
def gain_for(x):
    level=db(x);gain=min(14,-20-level) if level>=-48 else 0
    gain=min(gain,float(20*np.log10(.84/max(float(np.max(np.abs(x))),1e-12))))
    return gain


def export_instrument(key,x,out=OUT,work=WORK):
    track_id='instrument_'+key
    src=work/f'{track_id}_source.wav';sf.write(src,x,SR,subtype='FLOAT')
    filtered=work/f'{track_id}_eq.wav'
    profile=PROFILES[key] if db(x)>=-48 else 'anull'
    subprocess.run([FFMPEG,'-y','-hide_banner','-loglevel','error','-i',str(src),'-af',profile,'-c:a','pcm_f32le',str(filtered)],check=True)
    enhanced=read(filtered);t={'id':track_id,'label':{'guitar':'吉他','bass':'贝斯','drums':'鼓组','piano':'钢琴','other':'其他配乐'}[key],
      'scope':'instrument','ui_role':'primary','source_type':key,'group':'全片配乐','video_start_seconds':0,'duration':len(x)/SR,
      'sample_rate':SR,'default_mode':'raw','audio':{},'lossless':{},'levels':{},'source_rms_db':db(x),'weak':db(x)<-48,
      'source':'全片 RoFormer 伴奏 → htdemucs_6s · shifts=2 · overlap=0.5',
      'mode_labels':{'raw':'仅分离 · 原始','enhanced':'EQ 辅助听辨'},
      'note':'覆盖全片；轨名是模型估计，不代表该乐器必然存在。弱轨不强行抬升，仍可能有串音。',
      'processing_note':'原始 / EQ 为同一声源的两种试听处理，不作为两条独立类型展示；默认原速。'}
    if key=='other':t['source']='原曲 − 全人声 − 吉他 / 贝斯 / 鼓 / 钢琴估计';t['note']='剩余配乐与未归类成分，可能含分离误差和少量人声；不是一种已确认乐器。'
    for mode,y in [('raw',x),('enhanced',enhanced)]:
        gain=gain_for(y);peak=float(np.max(np.abs(y)));scale=min(1,.98/max(peak,1e-12))
        wav=work/f'{track_id}_{mode}_listen.wav';sf.write(wav,y*10**(gain/20),SR,subtype='PCM_24')
        mp3=f'audio/hierarchy/{track_id}_{mode}.mp3';flac=f'lossless/hierarchy/{track_id}_{mode}.flac'
        sf.write(out/flac,y*scale,SR,format='FLAC',subtype='PCM_24')
        subprocess.run([FFMPEG,'-y','-hide_banner','-loglevel','error','-i',str(wav),'-c:a','libmp3lame','-b:a','256k',str(out/mp3)],check=True)
        t['audio'][mode]=mp3;t['lossless'][mode]=flac;t['levels'][mode]={'listening_gain_db':gain,'archive_scale':scale}
    print('FULL EXPORTED',key,round(db(x),2),flush=True);return t


def crowd_from_vocals():
    """Magnitude intersection, not a claim of an identified audience or singer."""
    target=WORK/'crowd_vocal_candidate.wav'
    if target.exists():return read(target)
    vocal=read(BASE/'vocals_full/vocals.wav');guide=read(BASE/'crowd_full/crowd.wav');mix=read(BASE/'original_full.wav')
    result=np.zeros_like(vocal);core=20*SR;context=SR
    for start in range(0,len(vocal),core):
        end=min(len(vocal),start+core);left=max(0,start-context);right=min(len(vocal),end+context)
        zv,zg,zm=(stft(x[left:right]) for x in (vocal,guide,mix));mag=lambda z:np.sqrt(np.mean(np.abs(z)**2,axis=1))
        ratio=np.clip(mag(zg)/(mag(zm)+1e-8),0,1)
        mask=ndimage.gaussian_filter(ratio,(1.5,3))
        y=istft(zv*mask[:,None,:],right-left);result[start:end]=y[start-left:end-left]
    sf.write(target,result,SR,subtype='FLOAT');return result


def export_crowd(x):
    src=WORK/'crowd_vocal_candidate.wav';gain=gain_for(x);mp3='audio/hierarchy/vocal_crowd_candidate_raw.mp3';flac='lossless/hierarchy/vocal_crowd_candidate_raw.flac'
    scale=min(1,.98/max(float(np.max(np.abs(x))),1e-12));sf.write(OUT/flac,x*scale,SR,subtype='PCM_24')
    matched=WORK/'crowd_vocal_candidate_listen.wav';sf.write(matched,x*10**(gain/20),SR,subtype='PCM_24')
    subprocess.run([FFMPEG,'-y','-hide_banner','-loglevel','error','-i',str(matched),'-c:a','libmp3lame','-b:a','256k',str(OUT/mp3)],check=True)
    return {'id':'vocal_crowd','label':'观众声 · 候选','scope':'vocal','group':'人声细分','ui_role':'detail','parent_id':'vocal_all','source_type':'crowd_candidate',
      'video_start_seconds':0,'duration':len(x)/SR,'audio':{'raw':mp3},'lossless':{'raw':flac},'default_mode':'raw','weak':db(x)<-48,
      'levels':{'raw':{'listening_gain_db':gain}},'sample_rate':SR,'mode_labels':{'raw':'观众声候选 · 保守估计'},
      'source':'全人声原相位 × 原曲 Crowd 模型的平滑幅度引导',
      'note':'从全人声中选择 Crowd 模型支持的成分，不沿用原曲含鼓的 Crowd 残差。仍可能是和声、主唱尾音或串音，不能证明存在观众。',
      'processing_note':'当前只提供保守候选，暂不提供强化清理或连续回填，以免把误分类声音增强为确定的观众声。人声子轨仍可能重叠。'}


def attach_hierarchy(report,root_ids,detail_ids,reference_id):
    report['hierarchy']={'schema_version':1,'primary_ids':root_ids,'vocal_root_id':'vocal_all','vocal_children':detail_ids,
        'reference_id':reference_id,'vocal_expanded_default':False,'one_track_per_type':True,
        'split_execution':'cached_local_results' if detail_ids else 'not_requested',
        'processing_per_track':True,'split_warning':'主唱 / 和声 / 观众为估计，子轨可能重叠，观众候选不表示已识别人群。'}
    report['default_track']='vocal_all';report['playback_speed_default']=1
    report['waveform_view']={'default':'unified','track_policy':'hierarchical_one_node_per_type','time_alignment':'absolute_video_seconds',
        'outside_track_coverage':'blank','spacing_zoom_independent_of_camera':True,'rotation':'360_free_orbit','single_track_audio_playback':True}
    duration=max(t.get('video_start_seconds',0)+t['duration'] for t in report['tracks'])
    report['intro']=f'全片 {duration:.2f} 秒：先分离人声与配乐，每类一轨；人声可按需展开主唱 / 和声 / 观众候选。选中声部后选择处理方式，默认原速。'
    return report


def main():
    if not (MODEL/'result.json').exists():raise RuntimeError('Full instrument inference must finish first')
    WORK.mkdir(exist_ok=True)
    for folder in ['audio/hierarchy','lossless/hierarchy']:(OUT/folder).mkdir(parents=True,exist_ok=True)
    backup=WORK/'manifest_before_hierarchy.json'
    if not backup.exists():backup.write_text((OUT/'manifest.json').read_text())
    before=json.loads(backup.read_text());old={t['id']:t for t in before['tracks']};report=copy.deepcopy(before)
    all_v=copy.deepcopy(old['vocal_all']);all_v.update({'label':'人声','group':'基础分离','ui_role':'primary','source_type':'vocals','default_mode':'raw',
        'source':'Mel-Band RoFormer · 全片人声分离','note':'默认只分离人声。需要进一步区分主唱 / 和声 / 观众声候选时再展开；清理为同一轨的可选处理方式。',
        'mode_labels':{'raw':'仅分离 · 原始','enhanced':'保守清理','clarity':'强化清理（可能损失细节）'}})
    lead=copy.deepcopy(old['vocal_lead']);lead.update({'label':'主唱','group':'人声细分','ui_role':'detail','parent_id':'vocal_all','source_type':'lead','default_mode':'continuous'})
    lead['source']='Mel-Band RoFormer Karaoke · 全片主唱估计'
    continuous=old['vocal_lead_continuous']
    lead['audio']['continuous']=continuous['audio']['enhanced'];lead['lossless']['continuous']=continuous['lossless']['enhanced']
    lead['levels']['continuous']={'listening_gain_db':continuous['listening_gain_db']}
    lead['mode_labels']={'raw':'仅分离 · 原始','enhanced':'保守清理','clarity':'强化清理（可能损失细节）','continuous':'连续保留（推荐）'}
    lead['note']='一条主唱声源，处理方式可切换；连续保留在 01:08–01:15 找回原残差中的漏分谐波，其他时段沿用保守版。不是已识别某位歌手。'
    backing=copy.deepcopy(old['vocal_backing']);backing.update({'label':'和声','group':'人声细分','ui_role':'detail','parent_id':'vocal_all','source_type':'backing','default_mode':'enhanced',
        'mode_labels':{'raw':'仅分离 · 原始','enhanced':'保守清理'}})
    raw_sources={key:read(MODEL/f'{key}.wav') for key in ['guitar','bass','drums','piano']}
    original=read(BASE/'original_full.wav');v=read(BASE/'vocals_full/vocals.wav')
    raw_sources['other']=original-v-sum(raw_sources.values())
    instruments=[export_instrument(key,raw_sources[key]) for key in ['guitar','bass','drums','piano','other']]
    crowd=export_crowd(crowd_from_vocals())
    reference=copy.deepcopy(old['vocal_original_mix']);reference.update({'label':'原曲参考','ui_role':'reference','default_mode':'raw','source_type':'reference'})
    report['tracks']=[all_v,*instruments,lead,backing,crowd,reference]
    report=enrich_report(report)
    report=attach_hierarchy(report,[all_v['id'],*[t['id'] for t in instruments]],[lead['id'],backing['id'],crowd['id']],reference['id'])
    (WORK/'hierarchy_tracks.json').write_text(json.dumps({'tracks':report['tracks'],'hierarchy':report['hierarchy']},ensure_ascii=False,indent=2))
    (OUT/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (OUT/'index.html').write_text((BASE/'player_template.html').read_text().replace('__MANIFEST__',json.dumps(report,ensure_ascii=False)))
    residual=original-v-sum(raw_sources.values())
    (OUT/'hierarchy_processing.json').write_text(json.dumps({'full_frames':len(original),'full_duration_seconds':len(original)/SR,
        'instrument_model':json.loads((MODEL/'result.json').read_text()),'source_raw_reconstruction_residual_rms_dbfs':db(residual),
        'reference_reconstruction_uses_work_float_sources_not_normalized_listening_exports':True,
        'old_audio_files_preserved':True,'crowd_derived_from_vocals_not_mix':True,'vocal_children_may_overlap':True,
        'initial_default_roots':6,'expanded_visual_lanes':8,'processing_choices_for_lead':list(lead['audio'])},ensure_ascii=False,indent=2))
    print('HIERARCHICAL DEMO: 6 full-song roots / 3 optional vocal children / 1 reference; default raw roots.',flush=True)


if __name__=='__main__':main()
