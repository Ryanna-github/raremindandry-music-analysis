"""Generate current hierarchy docs from the delivered manifest, retaining legacy notes."""
import json
from pathlib import Path

BASE=Path(__file__).resolve().parent;OUT=BASE.parent.parent/'outputs/instrument_demo';WORK=BASE/'hierarchy'
report=json.loads((OUT/'manifest.json').read_text());nodes={t['id']:t for t in report['tracks']}
backup=WORK/'README_before_hierarchy.md'
if not backup.exists():backup.write_text((OUT/'README.md').read_text())
rows=['|层级|声源|默认处理|原速试听|','|---|---|---|---|']
for t in report['tracks']:
    mode=t.get('default_mode','raw');rows.append(f"|{ {'primary':'基础','detail':'可选人声细分','reference':'原曲对照'}.get(t.get('ui_role'),'音轨')}|{t['label']}|{t['mode_labels'].get(mode,mode)}|[MP3]({t['audio'][mode]})|")
(OUT/'README.md').write_text('\n'.join(['# 全片层级分轨 demo','',
 '当前全部基础分轨覆盖 223.63957 秒（03:43.64），本地处理、默认原速。不再把 45 秒片段铺成全曲，也不再默认展示重复模型和诊断切片。','',
 '打开 index.html：默认六类基础轨；点「进一步细分人声」展开三类人声子节点，选中声部后在「处理方式」里选择原始、清理或连续保留。每类只占一条轨。','',
 '3D 图支持两轴完整 360° 旋转；每类别一个波形体，音频保留立体声，默认高度 1.5×。总览多轨勾选混听，未勾选变透明并停止音频输出；处理对照和原曲参考只播放当前版本。横向时间轴点击播放、拖动浏览。原速、同位置切处理、A/B 循环、下载与评分保留。','',
 '本 demo 的二级结果已本地预计算，网页展开缓存，不在浏览器跑模型。观众声为候选，钢琴弱信号不证明乐器存在；人声子轨可能重叠。','',
 '完整流程与新音频两阶段命令见 [层级工作流](HIERARCHICAL_WORKFLOW.md)；评分不是听测准确率，见 [评分公式](AUDIO_EVALUATION.md)。','',
 '此前便携包保留为旧版；当前网页以本文件所在运行目录为准。原始音频、旧诊断文件和此前版本均保留。混听分轨有独立试听电平和可能重叠，不是原曲的精确复原。','',*rows,'']))
(OUT/'UNIFIED_WAVEFORM.md').write_text('# 当前统一分轨图\n\n默认六类全片基础轨；展开人声后八轨，处理方式不增加同类轨数。两轴360°自由旋转，独立轨道间距 / 空间 / 时间缩放。\n\n详情见 [层级工作流](HIERARCHICAL_WORKFLOW.md)。此前44轨总览属于历史版本，旧包仍保留。\n')
(OUT/'CONTINUITY_DEFAULTS.md').write_text('# 后续新音频默认流程\n\n现在默认初始化只分离全片人声与各类配乐；需要时再做二级人声及连续恢复。\n\n```bash\nwork/separator-env/bin/python work/vocal-detail/initialize_audio.py --input "/完整路径/new_audio.mov" --out outputs/new_audio_demo\nwork/separator-env/bin/python work/vocal-detail/initialize_audio.py --out outputs/new_audio_demo --expand-vocals\n```\n\n完整说明见 [层级工作流](HIERARCHICAL_WORKFLOW.md)。直接主唱连续恢复的旧高级入口 continuous_vocals.py 保留，但不再是默认初始化路径。\n')
print('Generated hierarchy README, waveform and default workflow docs.')
