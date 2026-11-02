"""Versioned display-Rec.709 luminance tone and OCIO ASC-CDL colour operations.

Tone order: input levels -> gamma -> bounded sigmoid contrast -> brightness gain ->
highlight shoulder -> common-gain peak limiting -> OCIO CDL colour. Every stage but
the shoulder keeps white at white; the shoulder t-(1-c)*t^2 is the only way to darken
near-white pixels (sky, cloud, glare). It is monotone with slope 2c-1 >= 0.2 at white,
so bright texture is compressed, never inverted, and shadows are nearly untouched.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import PyOpenColorIO as ocio


def apply_rgb(values,parameters,protected=False):
    """Encoded-domain effect, not a camera exposure or Kelvin calibration."""
    rgb=np.asarray(values,np.float32).copy();weights=np.array([.2126,.7152,.0722],np.float32)
    luma=rgb@weights
    level=np.clip((luma-parameters.black_point)/(parameters.white_point-parameters.black_point),0,1)
    x=np.power(level,1/parameters.gamma)
    a=np.power(x,parameters.contrast);b=np.power(1-x,parameters.contrast)
    tone=a/np.maximum(a+b,np.finfo(np.float32).tiny)
    gain=parameters.brightness_gain
    tone=gain*tone/(1+(gain-1)*tone)
    ceiling=parameters.highlight_ceiling
    tone=tone-(1-ceiling)*tone*tone # Exact no-op at 1.0, so pre-shoulder v3 recipes render identically.
    ratio=np.divide(tone,luma,out=np.ones_like(tone),where=luma>0)
    ratio=np.minimum(ratio,1/np.maximum(rgb.max(axis=-1),np.finfo(np.float32).tiny))
    rgb*=ratio[...,None] # Same channel gain preserves RGB ratios, including peak limiting.
    if not protected:
        c=parameters.cooling
        normalization=1/(.2126*(1-c)+.7152+.0722*(1+c))
        transform=ocio.CDLTransform();transform.setSlope([(1-c)*normalization,normalization,(1+c)*normalization])
        transform.setSat(parameters.saturation)
        ocio.Config.CreateRaw().getProcessor(transform).getDefaultCPUProcessor().applyRGB(rgb)
    return np.clip(rgb,0,1)


def lut(directory,parameters,protected=False):
    if all(abs(getattr(parameters,k)-v)<1e-10 for k,v in [('contrast',1),('gamma',1),('saturation',1),
        ('brightness_gain',1),('cooling',0),('black_point',0),('white_point',1),('highlight_ceiling',1)]):return 'null'
    identity={'parameters':parameters.model_dump(),'protected':protected,'ocio':ocio.__version__,'numpy':np.__version__,'version':4,'grid':65}
    name='grade-'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:24]+'.cube'
    path=Path(directory)/name
    receipt=path.with_suffix('.sha256')
    def file_hash():
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    valid=path.is_file() and receipt.is_file() and receipt.read_text().strip()==file_hash()
    if not valid:
        grid=np.linspace(0,1,65,dtype=np.float32)
        blue,green,red=np.meshgrid(grid,grid,grid,indexing='ij')
        values=np.stack([red,green,blue],axis=-1).reshape(-1,3)
        result=apply_rgb(values,parameters,protected)
        temporary=path.with_suffix('.tmp')
        with temporary.open('w') as stream:
            stream.write('TITLE "Take One display Rec709 luminance/CDL v4 highlight shoulder"\nLUT_3D_SIZE 65\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
            np.savetxt(stream,result,fmt='%.8f')
            stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
        stamp=receipt.with_suffix('.tmp');stamp.write_text(file_hash());stamp.replace(receipt)
    # Generated safe basename; render subprocess runs in the candidate directory.
    return f'lut3d=file={name}:interp=tetrahedral'
