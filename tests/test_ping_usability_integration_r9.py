"""Production two-peer Ping+ adapter; engine input/render proof is separate."""
import unittest
import test_ping_integration as fixture

class PingUsabilityIntegrationTests(unittest.TestCase):
 def setUp(self):self.t=fixture.PingIntegrationTests()
 def test_explicit_suspend_clears_widgets_without_removing_records(self):
  p=self.t.peers();self.t.press(p)
  p.a.execute('calls.clear_frames=0;function GuiStartFrame()calls.clear_frames=calls.clear_frames+1 end;ping.on_inventory_hover_suspend()')
  self.assertEqual(p.a.eval('calls.clear_frames'),1)
  self.assertEqual(p.a.eval('#ping_records()'),1)
 def test_post_hit_reprojects_current_camera_not_queued_view(self):
  p=self.t.peers();self.t.press(p);self.t.step(p,20)
  p.a.execute('frame=frame+1;ping_pressed=true;ping.on_world_update();calls.camera={5000,5000};ping.on_world_update_post();ping_pressed=false')
  p.flush()
  # The old dot is no longer under the current cursor projection. This refreshes
  # the nearby world intent, rather than canceling through its stale screen slot.
  self.assertEqual(p.a.eval('#ping_records()'),1)
  self.assertEqual(p.a.eval('ping_records()[1].id'),2)
 def test_ten_world_plus_inventory_share_and_eleventh_replaces_oldest(self):
  p=self.t.peers()
  for i in range(11):
   p.a.execute(f'cursor_x={-180+i*30};cursor_y=40')
   self.t.press(p);self.t.step(p,20)
  for l in p.peers.values():
   self.assertEqual(l.eval('#ping_records()'),10)
   self.assertEqual(l.eval('ping_records()[1].id'),2)
  p.a.execute("new_wand(50,0,0,'held');entities[50].parent=100;entities[100].children={50};entities[100].c.Inventory2Component={mActiveItem=50};entities[100].c.InventoryGuiComponent={mActive=true};menu=true;hover_item=50;function GameGetAllInventoryItems()return{50}end")
  self.t.step(p,3);self.t.press(p);self.t.step(p,3)
  for l in p.peers.values():self.assertEqual(l.eval('#ping_records()'),11)
 def test_direct_own_core_cancel_vs_nearby_refresh_and_terminal_replay(self):
  p=self.t.peers();self.t.press(p);self.t.step(p,20)
  p.a.execute("local r=ping_records()[1];old=encode({{v=1,run=ping_state().run,epoch=r.epoch,id=r.id,seq=99,target=r.target,kind=r.kind,x=r.x,y=r.y}})")
  p.a.execute('cursor_x=35');self.t.press(p)
  for l in p.peers.values():self.assertEqual(l.eval('ping_records()[1].id'),2)
  self.t.step(p,20);self.t.press(p)
  for l in p.peers.values():self.assertEqual(l.eval('#ping_records()'),0)
  old=p.a.eval('old');p.b.execute("receive('A','ping',"+repr(old)+")")
  self.assertEqual(p.b.eval('#ping_records()'),0)
  # Same-place post-cancel press is suppressed, elsewhere is immediately usable.
  self.t.press(p);self.assertEqual(p.a.eval('#ping_records()'),0)
  p.a.execute('cursor_x=80');self.t.press(p)
  self.assertEqual(p.b.eval('#ping_records()'),1)
 def test_foreign_edge_hit_never_falls_through_to_clamped_location(self):
  p=self.t.peers();p.b.execute('cursor_x=1000;cursor_y=0');self.t.press(p,'B');self.t.step(p,20)
  p.a.execute('local v=calls.ping_view;local r=ping_records()[1];local q=dofile_once("mods/quant.ew/files/system/player_ping/projection.lua").project(r.x,r.y,v);cursor_x=v.camera_x+(q.x-v.w/2)*v.world_w/v.w;cursor_y=v.camera_y+(q.y-v.h/2)*v.world_h/v.h');self.t.press(p)
  self.assertEqual(p.a.eval('#ping_records()'),0)
  self.assertEqual(p.b.eval('#ping_records()'),1)
  self.assertEqual(p.b.eval('ping_records()[1].sender'),'B')
 def test_same_place_danger_and_different_quick_points_and_owner_lifetime(self):
  p=self.t.peers();p.a.execute("settings['quant.ew.ping_life']='2'");p.b.execute("settings['quant.ew.ping_life']='120'")
  self.t.press(p);self.t.step(p,3);self.t.press(p)
  for l in p.peers.values():
   self.assertEqual(l.eval('ping_records()[1].kind'),'danger')
   self.assertEqual(l.eval('ping_records()[1].life'),120)
  self.t.step(p,121)
  for l in p.peers.values():self.assertEqual(l.eval('#ping_records()'),0)
  p=self.t.peers();self.t.press(p);p.a.execute('cursor_x=30');self.t.press(p)
  for l in p.peers.values():self.assertEqual(l.eval('#ping_records()'),2)
 def test_own_edge_cancel_and_other_sender_cannot_cancel_it(self):
  p=self.t.peers();p.a.execute('cursor_x=1000;cursor_y=0');self.t.press(p);self.t.step(p,20)
  p.b.execute("receive('B','cancel',encode({ping_state().run,1,1}))")
  self.assertEqual(p.b.eval('#ping_records()'),1)
  p.a.execute('local v=calls.ping_view;local r=ping_records()[1];local q=dofile_once("mods/quant.ew/files/system/player_ping/projection.lua").project(r.x,r.y,v);cursor_x=v.camera_x+(q.x-v.w/2)*v.world_w/v.w;cursor_y=v.camera_y+(q.y-v.h/2)*v.world_h/v.h');self.t.press(p)
  for l in p.peers.values():self.assertEqual(l.eval('#ping_records()'),0)
