"""Execute real generated wrappers in x86 emulation, never a game process.

The row's separately relocation-tested native call block is replaced by a deliberate
GPR/flags/x87/SSE/MXCSR clobberer to isolate its save/restore contract.
"""
import pathlib,struct,sys,unittest
from lupa.luajit21 import LuaRuntime
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'target/hover-tools'))
try:
 import unicorn as U
 from unicorn import x86_const as X
except ImportError:U=X=None
CODE,PAGE,BODY,STACK,FRAME,SP=0x10000000,0x20000000,0x22000000,0x30000000,0x30018000,0x30010000

def generated(kind):
 lua=LuaRuntime(encoding=None,unpack_returned_tuples=True)
 # Synthetic stand-in replaces only the native row body; the wrapper is production.
 clobber=b'\x89\x1d'+struct.pack('<I',PAGE+148)
 for op in (b'\x31\xc0',b'\x31\xdb',b'\x31\xc9',b'\x31\xd2',b'\x31\xf6',b'\x31\xff',b'\x31\xed'):clobber+=op
 for i in range(8):clobber+=b'\x66\x0f\xef'+bytes([0xc0+i*9])
 clobber+=b'\x0f\xae\x15'+struct.pack('<I',PAGE+144)+b'\xdb\xe3\xd9\xe8\xf9\xfc'
 lua.globals()[b'BODY_CLOBBER']=clobber
 lua.execute(b"function dofile_once()return {build=function()return BODY_CLOBBER end}end")
 backend=lua.execute((ROOT/'quant.ew/files/system/player_ping/native_inventory_capture.lua').read_bytes())
 key={'row':b'build_speed_code','layout':b'build_speed_layout','padding':b'build_speed_padding'}[kind]
 lua.globals()[b'BUILD']=backend[key]
 lua.execute(b"for i=1,50 do local n=debug.getupvalue(BUILD,i);if not n then break end;if n=='image'then debug.setupvalue(BUILD,i,0x400000);break end end")
 return backend[key](CODE,PAGE,b'')if kind=='row'else backend[key](CODE,PAGE)

@unittest.skipIf(U is None,'Optional pinned unicorn==2.1.4 CPU emulator is unavailable')
class NativeMachineStateTests(unittest.TestCase):
 def run_case(self,kind,ids,entity=42,column=40,flags=0xad7,mutate=False,detailed=True):
  uc=U.Uc(U.UC_ARCH_X86,U.UC_MODE_32)
  for a,n in ((CODE,4096),(PAGE,65536),(BODY,4096),(STACK,0x20000),(0xb6a000,4096),(0xb66000,4096)):
   uc.mem_map(a,n)
  stub=generated(kind)
  if mutate:stub=stub.replace(b'\x9d\x8d\x64\x24\x04',b'\x9d\x83\xc4\x04\x90')
  uc.mem_write(CODE,stub);uc.mem_write(PAGE+128,struct.pack('<III',len(ids),76,0))
  uc.mem_write(PAGE+136,struct.pack('<I',5));uc.mem_write(PAGE+144,struct.pack('<I',0x1f80))
  for i,id in enumerate(ids[:512]):uc.mem_write(PAGE+256+i*100,struct.pack('<I',id)+b'0.33x\0')
  uc.mem_write(BODY,struct.pack('<I',entity or 0));uc.mem_write(FRAME+8,struct.pack('<I',BODY if entity else 0))
  uc.mem_write(FRAME-0x40a4,struct.pack('<I',0x12345678));uc.mem_write(FRAME-0x408c,struct.pack('<f',column))
  uc.mem_write(SP,b'unchanged native stack/return data'+b'\0'*32)
  regs=[X.UC_X86_REG_EAX,X.UC_X86_REG_EBX,X.UC_X86_REG_ECX,X.UC_X86_REG_EDX,X.UC_X86_REG_ESI,X.UC_X86_REG_EDI,X.UC_X86_REG_EBP,X.UC_X86_REG_ESP]
  before={r:(0x11110000+i*0x1111)for i,r in enumerate(regs)};before[X.UC_X86_REG_EBP]=FRAME;before[X.UC_X86_REG_ESP]=SP
  if kind=='padding':
   uc.mem_map(0x1153000,4096);uc.mem_map(0xb67000,4096)
   uc.mem_write(0x115331c,struct.pack('<f',8));uc.mem_write(FRAME-0x4071,b'\1'if detailed else b'\0')
  for r,n in before.items():uc.reg_write(r,n)
  simd=[]
  for i in range(8):
   value=int.from_bytes(bytes([31+i])*16,'little')
   if i==0:value=(value&~0xffffffff)|struct.unpack('<I',struct.pack('<f',column))[0]
   uc.reg_write(X.UC_X86_REG_XMM0+i,value);simd.append(value)
  uc.reg_write(X.UC_X86_REG_MXCSR,0x3f85)
  uc.reg_write(X.UC_X86_REG_FPCW,0x37f);uc.reg_write(X.UC_X86_REG_FPSW,0);uc.reg_write(X.UC_X86_REG_FPTAG,0xfff0)
  for i in range(8):uc.reg_write(X.UC_X86_REG_FP0+i,(0x8000000000000000+i*0x100000000000000,0x3fff))
  fp=[(r,uc.reg_read(r))for r in [X.UC_X86_REG_FPCW,X.UC_X86_REG_FPSW,X.UC_X86_REG_FPTAG]+list(range(X.UC_X86_REG_FP0,X.UC_X86_REG_FP0+8))]
  uc.reg_write(X.UC_X86_REG_EFLAGS,flags|2)
  stop={'row':0xb6af4b,'layout':0xb66513,'padding':0xb67973}[kind]
  uc.emu_start(CODE,stop,count=10000)
  matched=bool(entity)and len(ids)<=512 and entity in ids and (kind!='padding'or detailed)
  for r,n in before.items():
   expected=0x12345678 if kind=='row'and r==X.UC_X86_REG_EAX else n
   self.assertEqual(uc.reg_read(r),expected,('GPR',kind,r))
  self.assertEqual(uc.reg_read(X.UC_X86_REG_ESP),SP)
  self.assertEqual(bytes(uc.mem_read(SP,32)),b'unchanged native stack/return data'[:32])
  for i,n in enumerate(simd):
   if kind in ('layout','padding')and matched and i==0:n=(n&~0xffffffff)|struct.unpack('<I',struct.pack('<f',max(column,76)if kind=='layout'else column+8))[0]
   self.assertEqual(uc.reg_read(X.UC_X86_REG_XMM0+i),n,('full XMM',kind,i))
  self.assertEqual(uc.reg_read(X.UC_X86_REG_MXCSR),0x3f85)
  for r,n in fp:self.assertEqual(uc.reg_read(r),n,('x87',kind,r))
  if kind=='padding':self.assertEqual(struct.unpack('<f',uc.mem_read(FRAME-0x40bc,4))[0],column+8 if matched else column)
  if kind=='layout':self.assertEqual(struct.unpack('<f',uc.mem_read(FRAME-0x408c,4))[0],max(column,76)if matched else column)
  if kind=='row':self.assertEqual(struct.unpack('<I',uc.mem_read(PAGE+148,4))[0],PAGE+260+ids.index(entity)*100 if matched else 0)
  actual=uc.reg_read(X.UC_X86_REG_EFLAGS)
  if mutate:self.assertNotEqual(actual,flags|2)
  else:self.assertEqual(actual,flags|2,('EFLAGS',kind,hex(actual),hex(flags|2)))

 def test_matched_and_unmatched_real_wrappers_preserve_machine_state(self):
  for kind in ('row','layout','padding'):
   for ids,e in (([42],42),([7,42,99],42),([7,8,42],42),([7,8,9],42),([],42),([42]*513,42),([42],None)):
    for flags in (0x202,0xad7,0xed7):
     with self.subTest(kind=kind,ids=len(ids),entity=e,flags=flags):self.run_case(kind,ids,e,flags=flags)
 def test_layout_does_not_reduce_a_wider_native_column(self):self.run_case('layout',[42],column=120)
 def test_negative_control_detects_flag_changing_cleanup_after_popfd(self):
  for kind in ('row','layout'):self.run_case(kind,[42],flags=0xad7,mutate=True)

 def test_height_prepass_skips_compact_native_cards(self):self.run_case('padding',[42],detailed=False)
