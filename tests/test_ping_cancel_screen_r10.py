"""Cancel-only screen-space burst contract; adapter evidence is not native input."""
import unittest
import test_ping_usability_lifecycle as lifecycle
import test_ping_integration as integration

class CancelScreenTests(unittest.TestCase):
 def runtime(self):
  l=lifecycle.runtime()
  l.execute('''
  function at(t,x,y,sx,sy,device,selected,space,w,h)
    return g.classify(history,{now=t,x=x,y=y,own='A',kind='location',records=m.list(s),selected=selected,
      input={x=sx,y=sy,device=device or 'mouse',space=space or 'window',w=w or 427,h=h or 240}})
  end
  own=add(1,'location',100,0,nil,'world',0)
  cancelled=at(100,100,0,200,150,'mouse',own)
  assert(cancelled.action=='cancel');publish(cancelled)
  ''')
  return l
 def test_same_screen_repeat_ignores_camera_world_displacement_and_extends_only_guard(self):
  self.runtime().execute('''
  local d=at(105,110,0,200,150);assert(d.action=='blocked'and d.reason=='cancel burst')
  assert(g.commit(history,d));assert(history.last.x==100 and history.last.input.x==200 and history.last.at==105)
  d=at(118,1200,-300,202,151);assert(d.action=='blocked');assert(g.commit(history,d))
  assert(#m.list(s)==0 and history.last.action=='cancel')
  ''')
 def test_elsewhere_is_immediate_even_when_world_coordinates_do_not_change(self):
  self.runtime().execute('''
  assert(at(101,100,0,210,150).action=='create')
  assert(at(101,100,0,200,160).action=='create')
  assert(at(101,110,0,210,150).action=='create')
  assert(at(101,110,0,200,150,'keyboard').action=='create')
  ''')
 def test_guard_expiry_context_and_missing_position_do_not_block(self):
  self.runtime().execute('''
  assert(at(118,110,0,200,150).action=='blocked')
  assert(at(119,110,0,200,150).action=='create')
  assert(at(101,100,0,200,150,'mouse',nil,'gui').action=='create')
  assert(at(101,100,0,200,150,'mouse',nil,'window',640,360).action=='create')
  local d=g.classify(history,{now=101,x=100,y=0,kind='location',own='A',records={}})
  assert(d.action=='create') -- Never invent a visual position from old world data.
  g.clear(history);assert(at(102,100,0,200,150).action=='create')
  ''')
 def test_input_is_frozen_and_cancel_burst_precedes_an_overlapping_foreign_marker(self):
  self.runtime().execute('''
  local original=cancelled.input;original.x=999
  assert(history.last.input.x==200)
  local foreign={sender='B',id=1,epoch=1,key='B:1:1',kind='location',created=0}
  local d=at(101,110,0,200,150,'mouse',foreign)
  assert(d.reason=='cancel burst'and g.commit(history,d))
  assert(history.last.at==101 and history.last.action=='cancel')
  ''')
 def test_controller_uses_visual_aim_and_fresh_danger_remains_world_based(self):
  self.runtime().execute('''
  history.last.input.device='controller';history.last.input.space='gui'
  assert(at(105,110,0,200,150,'controller',nil,'gui').reason=='cancel burst')
  assert(at(105,100,0,210,150,'controller',nil,'gui').action=='create')
  g.clear(history);m.reset(s,'run',0);next_id=0
  local d=at(0,0,0,50,60);local first=publish(d)
  d=at(8,2,0,80,90);assert(d.action=='upgrade');publish(d)
  assert(first.kind=='danger'and first.seq==2)
  ''')

class CancelScreenIntegrationTests(unittest.TestCase):
 def setUp(self):self.t=integration.PingIntegrationTests()
 def cancel(self,edge=False):
  p=self.t.peers();p.a.execute('cursor_x='+('1000'if edge else'20')+';cursor_y=20')
  self.t.press(p);self.t.step(p,20)
  p.a.execute('''
   local v=calls.ping_view;local r=ping_records()[1]
   local q=dofile_once('mods/quant.ew/files/system/player_ping/projection.lua').project(r.x,r.y,v)
   cursor_x=v.camera_x+(q.x-v.w/2)*v.world_w/v.w
   cursor_y=v.camera_y+(q.y-v.h/2)*v.world_h/v.h
   screen_override=true;screen_x=300;screen_y=200
  ''')
  self.t.press(p)
  for l in p.peers.values():self.assertEqual(l.eval('#ping_records()'),0)
  return p
 def test_core_and_edge_cancel_then_camera_motion_same_sdl_position_stays_quiet(self):
  for edge in (False,True):
   with self.subTest(edge=edge):
    p=self.cancel(edge);p.a.execute('cursor_x=cursor_x+10;calls.camera[1]=calls.camera[1]+10')
    self.t.press(p)
    for l in p.peers.values():self.assertEqual(l.eval('#ping_records()'),0)
    p.a.execute('screen_x=320');self.t.press(p)
    for l in p.peers.values():self.assertEqual(l.eval('#ping_records()'),1)
 def test_guard_expires_without_borrowing_a_world_coordinate_cooldown(self):
  p=self.cancel();self.t.step(p,19);p.a.execute('cursor_x=cursor_x+10')
  self.t.press(p);self.assertEqual(p.b.eval('#ping_records()'),1)
 def test_menu_body_and_run_cleanup_clear_cancel_history(self):
  for change in ('ctx.is_texting=true','ctx.is_paused=true','focused=false','ctx.run_ended=true',
      "entities[300]={alive=true,x=100,y=100,tags={},c={}};ctx.my_player.entity=300"):
   with self.subTest(change=change):
    p=self.cancel();p.a.execute(change);self.t.step(p)
    p.a.execute('local G=dofile_once("mods/quant.ew/files/system/player_ping/world_gestures.lua");for i=1,100 do local n,v=debug.getupvalue(ping.on_world_update,i);if n=="clear_view"then for j=1,100 do local k,z=debug.getupvalue(v,j);if k=="gesture_state"then cleared=z.last==nil end end end end')
    self.assertTrue(p.a.eval('cleared'))
