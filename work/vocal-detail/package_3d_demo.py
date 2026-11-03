"""Portable offline demo, preserving previously delivered archive versions."""
import argparse
import json
import zipfile
from pathlib import Path

BASE=Path(__file__).resolve().parent
OUT=BASE.parent.parent/'outputs/instrument_demo'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lossless',action='store_true');parser.add_argument('--name');args=parser.parse_args()
    report=json.loads((OUT/'manifest.json').read_text())
    # Listening-only archive must not present broken lossless download controls.
    if not args.lossless:
        for t in report['tracks']:
            for key in ['lossless','raw_wav','enhanced_wav']:t.pop(key,None)
    paths={path for t in report['tracks'] for path in [*t['audio'].values(),*t['waveforms'].values()]}
    if args.lossless:
        for t in report['tracks']:
            paths.update(t.get('lossless',{}).values())
            paths.update(t[k] for k in ['raw_wav','enhanced_wav'] if k in t)
    paths.update('assets/'+p.name for p in (OUT/'assets').iterdir())
    paths.update(p.name for p in OUT.iterdir() if p.suffix in ['.md','.json'] and p.name!='manifest.json')
    name=args.name or ('instrument_demo_3d_'+('lossless' if args.lossless else 'listening')+'.zip')
    if Path(name).name!=name or not name.endswith('.zip'):parser.error('--name must be a simple .zip filename')
    archive=OUT.parent/name;tmp=archive.with_suffix('.zip.tmp')
    with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        z.writestr('instrument_demo/manifest.json',json.dumps(report,ensure_ascii=False,indent=2))
        z.writestr('instrument_demo/index.html',(BASE/'player_template.html').read_text().replace('__MANIFEST__',json.dumps(report,ensure_ascii=False)))
        for path in sorted(paths):z.write(OUT/path,'instrument_demo/'+path)
    with zipfile.ZipFile(tmp) as z:
        assert z.testzip() is None
        names=set(z.namelist())
        assert not any('_slow' in p for p in names)
        for t in report['tracks']:
            for path in [*t['audio'].values(),*t['waveforms'].values()]:assert 'instrument_demo/'+path in names
    tmp.replace(archive);print(f'{archive.name}: {archive.stat().st_size/1024**2:.1f} MiB / {len(paths)+2} files',flush=True)


if __name__=='__main__':main()
