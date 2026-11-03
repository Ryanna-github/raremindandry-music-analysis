"""Refresh UI/config only; never re-encode, replace or re-score existing audio."""
import json
import shutil
from pathlib import Path

BASE=Path(__file__).resolve().parent
OUT=BASE.parent.parent/'outputs/instrument_demo'
report=json.loads((OUT/'manifest.json').read_text())
report['processing_defaults']=json.loads((BASE/'processing_defaults.json').read_text())
if report.get('hierarchy'):
    for track in report['tracks']:
        if track['id']=='vocal_all':
            track['source']='Mel-Band RoFormer · 全片人声分离'
            track['note']='默认只分离人声。需要进一步区分主唱 / 和声 / 观众声候选时再展开；清理为同一轨的可选处理方式。'
        elif track['id']=='vocal_lead':track['source']='Mel-Band RoFormer Karaoke · 全片主唱估计'
        elif track['id']=='vocal_backing':track['label']='合声（和声）'
    report['hierarchy']['split_warning']='主唱 / 合声（和声） / 观众声候选为估计，可能重叠；观众候选不表示已确认人群。'
report['waveform_view']={'default':'unified','track_policy':'hierarchical_one_node_per_type' if report.get('hierarchy') else 'one_current_or_recommended_version_per_track',
    'time_alignment':'absolute_video_seconds','outside_track_coverage':'blank',
    'spacing_zoom_independent_of_camera':True,'single_track_audio_playback':False,'rotation':'360_free_orbit',
    'geometry':'single_body_stereo_envelope','source_colors':'stable_high_saturation',
    'stereo_audio_preserved':True,'playback':'selected_stems_mix_with_fixed_headroom',
    'time_axis':'drag_pan_click_seek_and_play','deselected_opacity':0.32,
    'muted_outline_visible':True,'all_track_labels_persistent':True,'transport_cancels_pending_play':True,
    'visual_height_control':True,'shared_amplitude_power':0.78,'per_track_visual_normalization':False,
    'selected_track_preserves_source_color':True,'audio_and_scores_modified':False}
report['waveform_view'].update({'color_policy':'source_hue_locked_with_label_leaders','overview_policy':'enabled_stereo_envelopes_overlaid_not_phase_sum','overview_window_highlight':'visible_3d_time_window','muted_materials_immutable':True,'deselected_opacity':.08})
(OUT/'assets').mkdir(exist_ok=True)
for path in (BASE/'web').iterdir():shutil.copy2(path,OUT/'assets'/path.name)
shutil.copy2(BASE/'COMBINATION_EXPORT.md',OUT/'COMBINATION_EXPORT.md')
(OUT/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
(OUT/'processing_defaults.json').write_text(json.dumps(report['processing_defaults'],ensure_ascii=False,indent=2))
(OUT/'index.html').write_text((BASE/'player_template.html').read_text().replace('__MANIFEST__',json.dumps(report,ensure_ascii=False)))
print(f'UI refreshed: {len(report["tracks"])} tracks, original audio and scores untouched.')
