"""Production inventory-hover input/RPC (capture frontend separately tested) with two isolated engine adapters."""
import unittest
import test_ping_integration as ping_tests
T=ping_tests.PingIntegrationTests()
EXTRA=r'''
ctx.players.B.status.is_alive=true
entities[200].tags={};entities[200].parent=nil
entities[200].c.Inventory2Component={mActiveItem=70}
if PEER=='B'then flags.ew_flag_notplayer_active=nil end
new_wand(50,20,20,'owned A');entities[50].parent=100
new_wand(51,20,20,'replacement A');entities[51].parent=100
new_wand(70,30,30,'owned B');entities[70].parent=200
entities[100].c.Inventory2Component.mActiveItem=50
entities[100].c.InventoryGuiComponent={mActive=true}
entities[200].c.InventoryGuiComponent={mActive=true}
function GameGetAllInventoryItems(body)
 local out={};for e,v in pairs(entities)do if v.parent==body then out[#out+1]=e end end;return out
end
function held_records()
 local out={};for _,r in ipairs(ping_records())do if r.source=='held'then out[#out+1]=r end end;return out
end
'''
class HeldShareIntegrationTests(unittest.TestCase):
 def peers(self):return T.peers(EXTRA)
 def click(self,p,who='A',flush=True):
  l=p.peers[who];l.execute('menu=true;hover_item=entities[ctx.my_player.entity].c.Inventory2Component.mActiveItem')
  T.step(p,3);T.press(p,who);T.step(p,2)
  return l.eval('calls.shared_until~=nil')
 def test_hovered_b_captured_while_a_remains_held_and_snapshot_frozen(self):
  p=self.peers();p.a.execute('menu=true;hover_item=51');T.step(p,3);T.press(p);T.step(p,2)
  p.a.execute('entities[51].c.AbilityComponent.gunaction_config.speed_multiplier=9')
  self.assertEqual(p.a.eval('entities[100].c.Inventory2Component.mActiveItem'),50)
  self.assertEqual(p.b.eval('held_records()[1].wand.name'),'replacement A')
  self.assertAlmostEqual(p.b.eval('held_records()[1].wand.props.speedMultiplier'),.982264)
 def test_share_again_replaces_even_after_item_switch_and_preserves_world_ping(self):
  p=self.peers();T.press(p);self.assertTrue(self.click(p));p.a.execute('entities[100].c.Inventory2Component.mActiveItem=51')
  self.assertTrue(self.click(p))
  for l in p.peers.values():
   self.assertEqual(l.eval('#held_records()'),1);self.assertEqual(l.eval('held_records()[1].wand.name'),'replacement A')
   self.assertEqual(l.eval('#ping_records()'),2)
  p.a.execute('menu=false');T.step(p,20);T.press(p)
  self.assertTrue(p.a.eval("held_records()[1].kind=='wand'"))
 def test_empty_inventory_press_never_falls_through_and_guards_cancel(self):
  p=self.peers();p.a.execute('menu=true');T.press(p);T.step(p,3);self.assertEqual(p.a.eval('#ping_records()'),0)
  for guard in('focused=false','ctx.is_texting=true','ctx.is_paused=true','ctx.is_wand_pickup=true','ctx.coop_return_input_block=true',"ctx.spectating_over_peer_id='B'",'menu=false'):
   p=self.peers();p.a.execute('menu=true;hover_item=51');T.step(p,3);T.press(p)
   p.a.execute(guard);T.step(p,3);self.assertEqual(p.a.eval('#held_records()'),0,guard)
 def test_invalid_or_no_longer_owned_item_never_substitutes_held(self):
  for invalid in('entities[51].parent=nil','entities[51].alive=false','entities[51].c.AbilityComponent=nil','entities[100].c.Inventory2Component=nil'):
   p=self.peers();p.a.execute('menu=true;hover_item=51');T.step(p,3);T.press(p)
   p.a.execute(invalid);T.step(p,3);self.assertEqual(p.a.eval('#held_records()'),0,invalid)
 def test_keyboard_remap_shares_once_without_control_writes(self):
  p=self.peers();p.a.execute("settings['quant.ew.rebind_ping']=15;function InputIsKeyJustDown(k)return k==15 and ping_pressed end;menu=true;hover_item=51")
  T.step(p,3);T.press(p);T.step(p,6)
  self.assertEqual(p.b.eval('#held_records()'),1);self.assertEqual(p.b.eval('held_records()[1].id'),1)
  self.assertTrue(p.a.eval('entities[100].c.ControlsComponent.enabled'))
 def test_two_senders_and_current_sender_anchor_not_packet_or_camera(self):
  p=self.peers();self.click(p);self.click(p,'B')
  self.assertEqual(p.a.eval('#held_records()'),2)
  p.b.execute("menu=false;entities[100].x=550;entities[100].y=620;ctx.spectating_over_peer_id='A'");T.step(p)
  self.assertEqual(p.b.eval('calls.ping_draw[1].x'),550);self.assertEqual(p.b.eval('calls.ping_view.origin_x'),550)
  self.assertEqual(p.b.eval('held_records()[1].x'),100)
  p.b.execute("entities[100].alive=false");T.step(p)
  self.assertEqual(p.b.eval('#calls.ping_draw'),1) # only B, no stale A fallback
  p.b.execute("entities[300]={alive=true,x=700,y=800,tags={},c={}};ctx.players.A.entity=300");T.step(p)
  self.assertEqual(p.b.eval('calls.ping_draw[1].x'),700)
 def test_death_cutoff_before_delivery_rejects_stale_then_new_share_survives_old_clear(self):
  p=self.peers();self.click(p);p.a.execute("local r=held_records()[1];stale=encode({{v=1,run=r.run,epoch=r.epoch,id=r.id,seq=r.seq,target=r.target,source=r.source,kind=r.kind,x=r.x,y=r.y,wand=r.wand}})")
  p.a.execute('ctx.my_player.status.is_alive=false');T.step(p) # owner reliable cutoff first
  p.flush();p.b.execute("receive('A','ping',"+repr(p.a.eval('stale'))+")");self.assertEqual(p.b.eval('#held_records()'),0)
  p.a.execute('ctx.my_player.status.is_alive=true');self.assertTrue(self.click(p))
  p.b.execute("receive('A','held_clear',encode({ping_state().run,held_records()[1].epoch,2}))")
  self.assertEqual(p.b.eval('#held_records()'),1)
 def test_dead_disconnect_unknown_and_expiry_hide_without_inventory_mutation(self):
  p=self.peers();self.click(p);p.b.execute('menu=false;ctx.players.A.status.is_alive=false');T.step(p)
  self.assertEqual(p.b.eval('#calls.ping_draw'),0)
  p.b.execute('ctx.players.A.status=nil');T.step(p);self.assertEqual(p.b.eval('#calls.ping_draw'),0)
  p.b.execute('ctx.players.A.dc=true');T.step(p);self.assertEqual(p.b.eval('#held_records()'),0)
  p=self.peers();p.b.execute("settings['quant.ew.ping_life']='2'");self.click(p);T.step(p,365)
  self.assertEqual(p.b.eval('#held_records()'),1);self.assertEqual(p.a.eval('entities[100].c.Inventory2Component.mActiveItem'),50)
  self.assertTrue(p.a.eval('entities[100].c.ControlsComponent.enabled'))
 def test_body_replacement_cancels_deferred_target_then_new_context_can_share(self):
  p=self.peers();p.a.execute('menu=true;hover_item=51');T.step(p,3);T.press(p)
  p.a.execute("entities[300]={alive=true,x=500,y=600,tags={},c={Inventory2Component={mActiveItem=51},InventoryGuiComponent={mActive=true}}};entities[51].parent=300;ctx.my_player.entity=300")
  T.step(p,3);self.assertEqual(p.b.eval('#held_records()'),0)
  self.assertTrue(self.click(p));self.assertEqual(p.b.eval('held_records()[1].wand.name'),'replacement A')
 def test_client_frame_rollback_resets_share_target_with_epoch(self):
  p=self.peers();T.press(p,'B');T.step(p,20);p.b.execute('cursor_x=80');T.press(p,'B')
  self.click(p,'B');self.assertEqual(p.b.eval('held_records()[1].target'),3)
  p.b.execute('frame=0');T.step(p);self.assertTrue(self.click(p,'B'))
  for l in p.peers.values():
   self.assertEqual(l.eval('#held_records()'),1);self.assertEqual(l.eval('held_records()[1].id'),1);self.assertEqual(l.eval('held_records()[1].target'),1)
if __name__=='__main__':unittest.main()
