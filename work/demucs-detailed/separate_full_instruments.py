"""Full-length instrument separation from the existing local accompaniment.

Run in demucs-env. No APIs, no independent stem clipping/normalization.
"""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
import numpy as np
import soundfile as sf
os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK','1')
import torch
from demucs.pretrained import get_model
from demucs.apply import apply_model

BASE=Path(__file__).resolve().parent


def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--shifts',type=int,default=2);p.add_argument('--device',default='mps');args=p.parse_args()
    if (args.out/'result.json').exists():print('Completed outputs already exist; reusing.',flush=True);return
    args.out.mkdir(parents=True,exist_ok=True);torch.set_num_threads(6);torch.manual_seed(37)
    x,sr=sf.read(args.input,dtype='float32',always_2d=True)
    model=get_model('htdemucs_6s');model.eval();model.cpu()
    assert sr==model.samplerate==44100 and x.shape[1]==2 and np.isfinite(x).all()
    device=args.device if args.device!='mps' or torch.backends.mps.is_available() else 'cpu'
    wav=torch.from_numpy(x.T.copy());mean=wav.mean();std=wav.mean(0).std();normalized=(wav-mean)/(std+1e-12)
    print(f'FULL INSTRUMENTS: {len(x)/sr:.2f}s; model=htdemucs_6s; device={device}; shifts={args.shifts}',flush=True)
    started=time.monotonic()
    with torch.no_grad():result=apply_model(model,normalized[None],device=device,shifts=args.shifts,overlap=.5,split=True,progress=True)[0].cpu()
    result=result*std+mean
    outputs={}
    for name,wave in zip(model.sources,result):
        y=wave.T.numpy();assert y.shape==x.shape and np.isfinite(y).all()
        dest=args.out/f'{name}.wav';sf.write(dest,y,sr,subtype='FLOAT');outputs[name]=str(dest.resolve())
        print(f'SAVED {name}: {dest}',flush=True)
    with args.input.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest() if hasattr(hashlib,'file_digest') else None
    report={'model':'htdemucs_6s','device':device,'input':str(args.input.resolve()),'input_sha256':sha,
        'seconds':time.monotonic()-started,'frames':len(x),'sample_rate':sr,'outputs':outputs,'shifts':args.shifts,
        'overlap':.5,'pitch_shift':False,'local_only':True,'independent_normalization':False}
    (args.out/'result.json').write_text(json.dumps(report,indent=2));print('FULL INSTRUMENTS COMPLETE',flush=True)


if __name__=='__main__':main()
