"""Construct instance seeds exclusively from existing source-bound mask geometry."""
import cv2
import numpy as np
from meta_sam_parser import build_object_prompt,PromptObject,FramePrompt,PromptBox,PromptPoint
from .local_grading import raster
from .errors import Problem


def seed_from_masks(current,anchors,first,width,height,object_id):
    chosen=sorted({anchors[0],anchors[len(anchors)//2],anchors[-1]})
    prompts=[];evidence=[]
    for frame in chosen:
        binary,ids=raster(current[frame],width,height)
        if ids!={str(object_id)}:
            raise Problem('LOCAL_SUBJECT_AMBIGUOUS','Anchor masks do not identify exactly the requested object.',
                          'Inspect the selected subject before instance tracking.',422)
        yy,xx=np.where(binary>0)
        # The center of a box may fall in background. Use the deepest pixel
        # inside the actual mask, including for thin/concave objects.
        padded=np.pad(binary,1);distance=cv2.distanceTransform(padded,cv2.DIST_L2,5)[1:-1,1:-1]
        y,x=np.unravel_index(int(distance.argmax()),distance.shape)
        box=PromptBox(int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1)
        point=PromptPoint(int(x),int(y))
        prompts.append(FramePrompt(frame=frame-first,box=box,points=[point]))
        evidence.append({'source_frame':frame,'excerpt_frame':frame-first,'box_half_open':[box.left,box.top,box.right,box.bottom],'positive_point':[point.x,point.y]})
    text=build_object_prompt(size=(width,height),objects=[PromptObject(id=object_id,prompts=prompts)])
    return text,evidence
