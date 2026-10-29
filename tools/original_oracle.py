"""Regenerate golden vectors by emulating the supplied Windows encoder.

Development-only dependencies: pefile, unicorn. The original executable is an
explicit input and never runs as a Windows process or accesses a device.
"""
import argparse
import functools
import json
from pathlib import Path
import struct, pefile, unicorn
from unicorn.x86_const import *
BASE=0x400000; OBJ=0x2000000; STACK=0x4000000; STOP=0x5000000
@functools.lru_cache(maxsize=1)
def load_pe(executable):
 return pefile.PE(str(executable))

class Oracle:
 def __init__(self, executable):
  p=load_pe(executable)
  self.pe=p
  self.u=unicorn.Uc(unicorn.UC_ARCH_X86,unicorn.UC_MODE_32)
  self.u.mem_map(BASE,0x900000); self.u.mem_write(BASE,p.get_memory_mapped_image())
  for a,n in [(OBJ,0x20000),(STACK,0x20000),(STOP,0x1000),(0x3000000,0x10000)]:self.u.mem_map(a,n)
  self.hooks={};self.frames=[];self.w32(OBJ+0x208,1)
  self.hooks[0x5b4f30]=self.memset
  self.hooks[0x5b1f22]=lambda: self.ret()
  self.hooks[0x436080]=lambda: self.ret(self.arg(0))
  self.hooks[0x5ba6c1]=self.strtol
  self.actions=[]
  self.hooks[0x43b140]=self.list_action
  self.imports={}
  for d in p.DIRECTORY_ENTRY_IMPORT:
   for i in d.imports:
    addr=0x3000000+len(self.imports)*16
    self.imports[addr]=i.name or b'ordinal'
    self.w32(i.address,addr)
    self.hooks[addr]=lambda name=i.name:self.winapi(name)
  self.u.hook_add(unicorn.UC_HOOK_CODE,self.code)
  self.nextstr=0x2010000
  for a in [0x65cf8c,0x65d064,0x65d06c,0x65d074,0x65d07c,0x65d084]:self.string(a,'0')
 def w32(self,a,v):self.u.mem_write(a,struct.pack('<I',v))
 def r32(self,a):return struct.unpack('<I',self.u.mem_read(a,4))[0]
 def arg(self,i):return self.r32(self.u.reg_read(UC_X86_REG_ESP)+4+i*4)
 def ret(self,value=0,pop=0):
  sp=self.u.reg_read(UC_X86_REG_ESP);pc=self.r32(sp)
  self.u.reg_write(UC_X86_REG_EAX,value&0xffffffff);self.u.reg_write(UC_X86_REG_ESP,sp+4+pop);self.u.reg_write(UC_X86_REG_EIP,pc)
 def memset(self):
  a,v,n=[self.arg(i) for i in range(3)];self.u.mem_write(a,bytes([v&255])*n);self.ret(a)
 def strtol(self):
  a=self.arg(0);b=bytes(self.u.mem_read(a,60));s=b.decode('utf-16-le').split('\0')[0]
  self.ret(int(s,self.arg(2)))
 def list_action(self):
  dest,index=self.arg(0),self.arg(1)
  self.u.mem_write(dest,self.actions[index]);self.ret(dest,8)
 def winapi(self,name):
  if name==b'Sleep':self.ret(pop=4)
  elif name==b'HidD_SetFeature':
   self.frames.append(bytes(self.u.mem_read(self.arg(1),self.arg(2))));self.ret(1,12)
  else:raise RuntimeError(f'Unsupported {name!r}')
 def code(self,u,a,n,ctx):
  if a==STOP:u.emu_stop()
  elif a in self.hooks:self.hooks[a]()
 def string(self,addr,s):
  a=self.nextstr;self.nextstr+=0x100;self.w32(a+12,100000);self.u.mem_write(a+16,s.encode('utf-16-le')+b'\0\0');self.w32(addr,a+16)
 def run(self,addr,args=()):
  sp=STACK+0x10000;self.u.mem_write(sp,struct.pack('<'+'I'*(len(args)+1),STOP,*args));self.u.reg_write(UC_X86_REG_ESP,sp);self.u.reg_write(UC_X86_REG_ECX,OBJ)
  self.u.emu_start(addr,STOP,count=1000000)
  if self.u.reg_read(UC_X86_REG_EIP)!=STOP:raise RuntimeError('Execution did not return')
  return self.frames
def generate(executable):
 vectors=[]
 def oracle():
  o=Oracle(executable)
  o.u.mem_write(0x65b6b0,struct.pack('<H',0x3212));o.run(0x408680)
  return o
 def add(kind,inputs,frames):
  if not frames: raise RuntimeError(f'No original output for {kind} {inputs}')
  # Each independent command comparison starts at sequence zero.
  normalized=[]
  for i,frame in enumerate(frames):
   frame=bytearray(frame);frame[4]=i;normalized.append(frame.hex())
  vectors.append({'kind':kind,'input':inputs,'packets':normalized})
 o=oracle(); count=o.r32(OBJ+0x82fc)
 dpis=[o.r32(0x65be88+i*4)*100 for i in range(count)]
 for idx,value in enumerate(dpis):
  o=oracle();o.u.mem_write(0x65d308,b'\x01');o.u.mem_write(0x65d30a,struct.pack('<H',idx))
  add('dpi',{'values':[value],'active':0},[f for f in o.run(0x431420) if f[2]==5])
 o=oracle();o.u.mem_write(0x65d308,b'\x15');o.u.mem_write(0x65d31a,b'\x04')
 o.u.mem_write(0x65d30a,struct.pack('<5H',*range(5)))
 add('dpi',{'values':dpis[:5],'active':4,'enabled':[True,False,True,False,True]},
     [f for f in o.run(0x431420) if f[2]==5])
 colors=['#112233','#445566','#778899','#AABBCC','#DDEEFF','#123456','#654321','#102030']
 o=oracle()
 for i,color in enumerate(colors):o.w32(0x65d31c+i*4,int.from_bytes(bytes.fromhex(color[1:]),'little'))
 add('dpi_colors',{'colors':colors},[f for f in o.run(0x431420) if f[2]==6])
 for idx,hz in enumerate((125,250,500,1000)):
  o=oracle();o.u.mem_write(0x65d0d4,bytes([idx]))
  add('polling',{'hz':hz},[f for f in o.run(0x431420) if f[2]==7])
 modes=[(0,'off'),(1,'stream'),(2,'breathing'),(3,'steady'),(4,'neon'),(5,'snake'),(6,'colorful'),(9,'wave')]
 for mode_idx,mode in modes:
  for speed,brightness in [(1,1),(2,3),(4,4)]:
   o=oracle();o.u.mem_write(0x65d0e0,bytes([mode_idx]))
   o.u.mem_write(0x65d0e3+mode_idx,bytes([speed-1]));o.u.mem_write(0x65d0ed+mode_idx,bytes([brightness-1]))
   palette=colors[:7] if mode_idx in (2,6) else colors[:1]
   if mode_idx==3:o.w32(0x65d1d4,int.from_bytes(bytes.fromhex(palette[0][1:]),'little'))
   if mode_idx in (2,6,7):
    flags,values={2:(0x65d10b,0x65d1ac),6:(0x65d133,0x65d24c),7:(0x65d13d,0x65d274)}[mode_idx]
    for i,color in enumerate(palette):
     o.u.mem_write(flags+i,b'\x01');o.w32(values+i*4,int.from_bytes(bytes.fromhex(color[1:]),'little'))
   o.u.mem_write(0x65d2ed,b'\x01')
   add('lighting',{'mode':mode,'speed':speed,'brightness':brightness,'colors':palette,'direction':1},
       [f for f in o.run(0x431420) if f[2]==8])
 for speed in (1,2,4):
  o=oracle();o.u.mem_write(0x65d0e0,b'\x01');o.u.mem_write(0x65d0e4,bytes([speed-1]))
  add('lighting',{'mode':'stream','speed':speed,'brightness':1,'colors':['#112233'],'direction':0},
      [f for f in o.run(0x431420) if f[2]==8])
 actions=['100100','100200','700106','808301','304603','400100']
 o=oracle()
 for i,action in enumerate(actions):o.u.mem_write(0x65d08c+i*12,bytes.fromhex(action))
 add('buttons',{'actions':actions},[f for f in o.run(0x430f60) if f[2]==4])
 lookup=o.pe.get_data(0x61e2e8-BASE,256)
 for count in [1,8,9,25,125]:
  events=[]
  for i in range(count):
   kind=i%6
   event=({'type':'key','key':'a','state':'down'}, {'type':'key','key':'a','state':'up'},
          {'type':'key','key':'ctrl','state':'down'},{'type':'mouse','button':'left','state':'up'},
          {'type':'move_x','value':-12},{'type':'wheel','value':1})[kind]
   events.append(dict(event,delay_ms=(i*71)%4096))
  o=oracle();lib=OBJ+0x14000;node=OBJ+0x15000;mapping=OBJ+0x16000
  o.w32(0x65cf58,lib);o.w32(lib+4,1);o.w32(lib+8,node);o.w32(OBJ+0x658,mapping)
  o.w32(mapping,2);o.u.mem_write(node+0x54e,bytes([count]))
  for event in events:
   if event['type']=='key':usage=4 if event['key']=='a' else 0xe0;value=0
   elif event['type']=='mouse':usage=0xf0;value=0
   else:usage=0xf6 if event['type']=='move_x' else 0xf5;value=event['value']&255
   vk=lookup.index(usage)
   o.actions.append(bytes([vk,0x80 if event.get('state')=='up' else 0,0,value]))
   delay=event['delay_ms'];o.actions.append(bytes([3,delay>>16,(delay>>8)&255,delay&255]))
  o.w32(node+4,len(o.actions))
  add('macro',{'slot':3,'events':events},[f for f in o.run(0x4301c0,(1,)) if f[2]==0x0e])
 return {'source':'GamingMS.exe from SC830 V1.1.8','dpi_values':dpis,'vectors':vectors}


if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('executable',type=Path);parser.add_argument('output',type=Path)
 args=parser.parse_args()
 result=generate(args.executable)
 args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(f"Generated {len(result['vectors'])} original-program vectors")
