import unittest
from pathlib import Path
from lupa.luajit21 import LuaRuntime
ROOT=Path(__file__).resolve().parents[1]
class SoundTests(unittest.TestCase):
 def test_muted_or_one_native_event_without_audio_entities_or_loops(self):
  l=LuaRuntime();l.execute("""
  calls={}
  function GameGetCameraPos()return 10,20 end
  function GamePlaySound(bank,event,x,y)calls[#calls+1]={bank,event,x,y}end
  function EntityCreateNew()error('No cosmetic audio entities')end
  function GameEntityPlaySoundLoop()error('No repeating loop')end
  """)
  l.execute('S=assert(loadstring(...))()',(ROOT/'quant.ew/files/system/player_ping/sound.lua').read_text())
  l.execute("""
  S.play(false);assert(#calls==0)
  S.play(true);assert(#calls==1 and calls[1][1]=='data/audio/Desktop/ui.bank'and calls[1][2]=='ui/button_select')
  assert(calls[1][3]==10 and calls[1][4]==20)
  for i=1,100 do S.update(i)end;S.clear();assert(#calls==1)
  """)
if __name__=='__main__':unittest.main()
