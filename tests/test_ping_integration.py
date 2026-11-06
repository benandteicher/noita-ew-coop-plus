"""Production input/RPC integration with two engine-adapter peers."""
import unittest
from test_coop_revival import Peers
SETUP=r"""
settings.statue=false
settings['quant.ew.ping_life']='6.0'
settings['quant.ew.ping_sound']=true
function GameGetCameraBounds()return -213.5,-120,427,240 end
cursor_x,cursor_y,ping_pressed=20,20,false
function DEBUG_GetMouseWorld()return cursor_x,cursor_y end
function InputIsMouseButtonJustDown(n)return n==3 and ping_pressed end
function InputGetJoystickAnalogStick()return 0,0 end
function ComponentObjectGetValue2(c,o,k)return c[o][k]end
function EntityGetAllChildren(e)return entities[e].children or{}end
wand_ids={}
function EntityGetInRadiusWithTag()return wand_ids end
function new_wand(e,x,y,name)
 entities[e]={alive=true,x=x,y=y,tags={wand=true},c={SpriteComponent={image_file='data/items_gfx/handgun.png',alpha=1,visible=true,offset_x=0,offset_y=0,update_transform=true,update_transform_rotation=true},ItemComponent={is_pickable=true,item_name=name},AbilityComponent={use_gun_script=true,mana_max=100,mana=80,mana_charge_speed=20,sprite_file='data/items_gfx/handgun.png',gun_config={shuffle_deck_when_empty=false,actions_per_round=1,reload_time=30,deck_capacity=2},gunaction_config={fire_rate_wait=12,spread_degrees=1.25,speed_multiplier=.982264}}}}
end
hover_item,hover_mx,hover_my=0,40,40
local inventory_pending
local function hover_poll(c)
 if inventory_pending and c.frame>inventory_pending.frame+1 then
  local q=inventory_pending;inventory_pending=nil
  if c.run==q.run and c.body==q.body and c.inventory==q.inventory and c.mx==q.mx and c.my==q.my
    and c.focused and c.inventory_open and not c.blocked and hover_item~=0 then return hover_item end
 end
end
local once=dofile_once
local stubs={
 ['mods/quant.ew/files/system/player_ping/inventory_hover.lua']={mouse=function()if not menu then return screen_override and screen_x or cursor_x-calls.camera[1]+213.5,screen_override and screen_y or cursor_y-calls.camera[2]+120,0 end;return hover_mx,hover_my,0 end,poll=hover_poll,
 request=function(c)if not c.blocked and c.focused then inventory_pending=c end end,
 close=function()inventory_pending=nil;calls.hover_closes=(calls.hover_closes or 0)+1 end,cancel=function()inventory_pending=nil end},
 ['mods/quant.ew/files/system/player_ping/renderer.lua']={clear=function()end,hit=function(records,o)return once('mods/quant.ew/files/system/player_ping/renderer.lua').hit(records,o)end,draw=function(_,records,o)calls.ping_draw=records;calls.ping_view=o.view;calls.ping_names=o.names;calls.ping_opacity=o.idle_opacity;return{}end},
 ['mods/quant.ew/files/system/player_ping/wand_preview.lua']={active_key=function()end,clear_held=function()end,clear=function()calls.ping_clear=(calls.ping_clear or 0)+1 end,draw=function()end,draw_held=function(_,e,o)calls.ping_held=e;calls.shared_until=o.shared_until end},
 ['mods/quant.ew/files/system/player_ping/sound.lua']={clear=function()end,update=function()end,play=function(v,now)calls.ping_cues=(calls.ping_cues or 0)+1 end},
 ['mods/quant.ew/files/system/player_arrows/teammates.lua']={color=function()return{1,1,1}end,position=function(p)return EntityGetTransform(EntityGetRootEntity(p.entity))end,origin=function()local p=ctx.players[ctx.spectating_over_peer_id or ctx.my_id];if p and not p.dc then return EntityGetTransform(EntityGetRootEntity(p.entity))end end},
}
function dofile_once(path)return stubs[path]or once(path)end
function ping_state()
 for i=1,100 do local n,v=debug.getupvalue(ping.on_world_update,i);if n=='state'then return v end end
end
function ping_records()return once('mods/quant.ew/files/system/player_ping/model.lua').list(ping_state())end
"""
class PingIntegrationTests(unittest.TestCase):
 def peers(self,extra=''):
  p=Peers(SETUP+extra)
  for l in p.peers.values():l.execute("ping=dofile_once('mods/quant.ew/files/system/player_ping/player_ping.lua')")
  for l in p.peers.values():l.execute('ping.on_local_player_spawn()')
  p.flush();return p
 def step(self,p,n=1):
  for _ in range(n):
   for l in p.peers.values():l.execute('frame=frame+1;ping.on_world_update();ping.on_world_update_post()')
   p.flush()
 def press(self,p,who='A'):
  p.peers[who].execute('ping_pressed=true');self.step(p);p.peers[who].execute('ping_pressed=false')
 def test_inventory_capture_cleanup_uses_dispatched_pause_and_run_end_hooks(self):
  from pathlib import Path
  source=(Path(__file__).resolve().parents[1]/'quant.ew/init.lua').read_text()
  pause=source.split('function OnPausedChanged',1)[1].split('function OnWorldInitialized',1)[0]
  self.assertIn('if paused or is_wand_pickup then',pause)
  self.assertIn('ctx.hook.on_inventory_hover_suspend()',pause)
  for fn in('local function on_world_pre_update_inner()', 'local function on_world_post_update_inner()'):
   block=source.split(fn,1)[1].split('if ctx.run_ended then',1)[1].split('\n    end',1)[0]
   self.assertLess(block.index('ctx.hook.on_inventory_hover_suspend()'),block.index('return'))
  p=self.peers();before=p.a.eval('calls.hover_closes or 0')
  p.a.execute('ping.on_inventory_hover_suspend();ping.on_player_died(ctx.my_player.entity)')
  self.assertEqual(p.a.eval('calls.hover_closes'),before+2)

 def test_one_id_upgrade_third_tap_refresh_and_single_cue(self):
  p=self.peers();self.press(p);self.step(p,10);self.press(p);self.press(p)
  for l in p.peers.values():
   self.assertEqual(l.eval('#ping_records()'),1);self.assertEqual(l.eval('ping_records()[1].kind'),'danger');self.assertEqual(l.eval('ping_records()[1].seq'),2)
  self.assertIsNone(p.a.eval('calls.ping_cues'))
  self.assertEqual(p.b.eval('calls.ping_cues'),1)
  self.step(p,25);p.a.execute('cursor_x=35');self.press(p)
  self.assertEqual(p.a.eval('#ping_records()'),1);self.assertEqual(p.a.eval('ping_records()[1].kind'),'location')
  self.assertEqual(p.a.eval('ping_records()[1].id'),2);self.assertEqual(p.a.eval('ping_records()[1].target'),1)
  self.assertIsNone(p.a.eval('calls.ping_cues'))
  self.assertEqual(p.b.eval('calls.ping_cues'),2)
 def test_migration_preserves_custom_and_world_target_is_deterministic_visible_unowned(self):
  p=self.peers();self.assertEqual(p.a.eval("settings['quant.ew.ping_life']"),'30.0')
  p.a.execute("new_wand(51,20,20,'high');new_wand(50,20,20,'low');wand_ids={51,50}")
  self.press(p)
  for l in p.peers.values():
   self.assertEqual(l.eval('ping_records()[1].kind'),'wand');self.assertEqual(l.eval('ping_records()[1].wand.name'),'low')
   self.assertAlmostEqual(l.eval('ping_records()[1].wand.props.speedMultiplier'),.982264)
  q=self.peers("settings['quant.ew.ping_life']='45'")
  self.assertEqual(q.a.eval("settings['quant.ew.ping_life']"),'45')
  for change in ('entities[50].parent=100','entities[50].c.SpriteComponent._enabled=false','entities[50].c.ItemComponent._enabled=false','loaded=false','entities[50].x=400'):
   r=self.peers();r.a.execute("new_wand(50,20,20,'hidden');wand_ids={50};"+change);self.press(r)
   self.assertEqual(r.a.eval('ping_records()[1].kind'),'location',change)
 def test_menus_focus_inventory_and_return_guard_block_without_control_mutation(self):
  for block in('focused=false','ctx.is_texting=true','ctx.is_paused=true','ctx.is_wand_pickup=true','menu=true','ctx.coop_return_input_block=true'):
   p=self.peers();p.a.execute(block);self.press(p)
   self.assertEqual(p.a.eval('#ping_records()'),0,block)
   self.assertTrue(p.a.eval('entities[100].c.ControlsComponent.enabled'))
  p=self.peers();p.a.execute('menu=true');self.step(p);self.assertEqual(p.a.eval('calls.ping_held'),100)
  p.b.execute('menu=true');self.step(p);self.assertIsNone(p.b.eval('calls.ping_held'))
 def test_independent_actions_do_not_block_default_ping_and_known_binding_conflicts_do(self):
  for action in('Jump','Fire','Fire2','Fly','Right','Interact','Throw','Kick','ChangeItemR'):
   p=self.peers();p.a.execute('entities[100].c.ControlsComponent.mButtonFrame'+action+'=1');self.press(p)
   self.assertEqual(p.a.eval('#ping_records()'),1,action)
  p=self.peers();self.press(p);p.a.execute('ctx.my_player.entity=101');self.press(p)
  self.assertEqual(p.a.eval('ping_records()[1].kind'),'location')
  p=self.peers("settings['quant.ew.rebind_ping']=55;function InputIsKeyJustDown(k)return k==55 and ping_pressed end")
  self.press(p,'B');self.assertEqual(p.b.eval('#ping_records()'),0)
 def test_duplicate_bad_packets_disconnected_and_run_reset(self):
  p=self.peers();self.press(p)
  p.a.execute("local r=ping_records()[1];bad={v=1,run=ping_state().run,epoch=r.epoch,id=r.id,seq=r.seq,kind=r.kind,x=r.x,y=r.y};receive('A','ping',encode({bad}));bad.seq=99;bad.kind='wand';bad.wand={};receive('A','ping',encode({bad}))")
  self.assertEqual(p.a.eval('ping_records()[1].seq'),1)
  p.a.execute('ctx.players.A.dc=true');self.step(p);self.assertEqual(p.a.eval('#ping_records()'),0)
  p=self.peers();self.press(p);old=p.a.eval('ping_state().run');p.a.execute('ng=1');p.b.execute('ng=1');self.step(p)
  self.assertEqual(p.a.eval('#ping_records()'),0);self.assertNotEqual(p.a.eval('ping_state().run'),old)
 def test_post_handler_chat_inventory_recheck_with_unrelated_actions_allowed(self):
  for change in ('ctx.is_texting=true','menu=true','ctx.coop_return_input_block=true'):
   p=self.peers();p.a.execute('frame=frame+1;ping_pressed=true;ping.on_world_update();'+change+';ping.on_world_update_post()');p.flush()
   self.assertEqual(p.a.eval('#ping_records()'),0,change)
  p=self.peers();p.a.execute('frame=frame+1;ping_pressed=true;ping.on_world_update();entities[100].c.ControlsComponent.mButtonFrameFire=frame;ping.on_world_update_post()');p.flush()
  self.assertEqual(p.a.eval('#ping_records()'),1)
  p=self.peers("settings['quant.ew.rebind_ping']=40;function InputIsKeyJustDown(k)return k==40 and ping_pressed end")
  self.press(p);self.assertEqual(p.a.eval('#ping_records()'),0)
 def test_deliberate_reping_survives_shorter_recipient_expiry_and_rejects_stale(self):
  p=self.peers();p.b.execute("settings['quant.ew.ping_life']='2'");self.press(p)
  p.a.execute("local r=ping_records()[1];old={v=1,run=ping_state().run,epoch=r.epoch,id=r.id,seq=99,target=r.target,kind=r.kind,x=r.x,y=r.y}")
  self.step(p,125);self.assertEqual(p.b.eval('#ping_records()'),1);self.assertEqual(p.a.eval('#ping_records()'),1)
  p.a.execute('cursor_x=35');self.press(p)
  for l in p.peers.values():
   self.assertEqual(l.eval('#ping_records()'),1);self.assertEqual(l.eval('ping_records()[1].id'),2);self.assertEqual(l.eval('ping_records()[1].target'),1)
  p.a.execute("receive('A','ping',encode({old}))");self.assertEqual(p.a.eval('ping_records()[1].id'),2)
 def test_target_cache_outlives_sender_expiry_and_wand_reping_recaptures(self):
  p=self.peers();p.a.execute("settings['quant.ew.ping_life']='2'");self.press(p);self.step(p,125)
  self.assertEqual(p.a.eval('#ping_records()'),1);self.assertEqual(p.b.eval('#ping_records()'),1)
  p.a.execute('cursor_x=35');self.press(p);self.assertEqual(p.b.eval('#ping_records()'),1);self.assertEqual(p.b.eval('ping_records()[1].target'),1)
  p=self.peers();p.a.execute("new_wand(50,20,20,'actual');wand_ids={50}");self.press(p);self.step(p,25)
  p.a.execute('entities[50].c.AbilityComponent.gunaction_config.speed_multiplier=.3333;cursor_x=30');self.press(p)
  for l in p.peers.values():
   self.assertEqual(l.eval('#ping_records()'),1);self.assertEqual(l.eval('ping_records()[1].id'),2);self.assertEqual(l.eval('ping_records()[1].target'),1)
   self.assertAlmostEqual(l.eval('ping_records()[1].wand.props.speedMultiplier'),.3333)
 def test_per_sender_bound_and_local_opacity_names(self):
  p=self.peers();p.a.execute("settings['quant.ew.ping_opacity']=65")
  for x in(20,80,140,200):
   p.a.execute('cursor_x='+str(x));self.press(p);self.step(p,20)
  self.assertEqual(p.a.eval('#ping_records()'),4);self.assertEqual(p.b.eval('#ping_records()'),4)
  self.assertEqual(p.a.eval('calls.ping_names.A'),'You');self.assertAlmostEqual(p.a.eval('calls.ping_opacity'),.65)
 def test_rollbacks_reject_old_runs_and_reset_cue_cadence(self):
  p=self.peers();p.a.execute('frame=10000');p.b.execute('frame=10000');self.press(p)
  p.a.execute("local r=ping_records()[1];old={v=1,run=ping_state().run,epoch=r.epoch,id=r.id,seq=99,kind='location',x=1,y=1}")
  p.a.execute('frame=0');p.b.execute('frame=0');self.step(p);self.press(p)
  self.assertIsNone(p.a.eval('calls.ping_cues'))
  self.assertEqual(p.b.eval('calls.ping_cues'),2)
  p.a.execute("receive('A','ping',encode({old}))")
  self.assertEqual(p.a.eval('#ping_records()'),1)
 def test_rpc_before_world_update_cannot_consume_rollback_reset(self):
  p=self.peers();p.a.execute('frame=100;ping.on_world_update()');old=p.a.eval('ping_state().run')
  p.a.execute("frame=5;receive('B','ping',encode({{v=1,run=ping_state().run,epoch=1,id=1,seq=1,kind='location',x=10,y=10}}));ping.on_world_update()")
  self.assertNotEqual(p.a.eval('ping_state().run'),old)
  self.assertEqual(p.a.eval('#ping_records()'),0)
 def test_spectator_origin_uses_selected_current_root(self):
  p=self.peers();p.a.execute("ctx.spectating_over_peer_id='B';entities[200].parent=nil;entities[200].x=1000;entities[200].y=1200")
  self.step(p)
  self.assertEqual(p.a.eval('calls.ping_view.origin_x'),1000);self.assertEqual(p.a.eval('calls.ping_view.origin_y'),1200)
  p.a.execute('entities[200].parent=100');self.step(p)
  self.assertEqual(p.a.eval('calls.ping_view.origin_x'),100)
 def test_mute_and_batch_cue_throttle_do_not_drop_pings(self):
  p=self.peers("settings['quant.ew.ping_sound']=false")
  self.press(p);self.assertIsNone(p.a.eval('calls.ping_cues'));self.assertEqual(p.a.eval('#ping_records()'),1)
  p=self.peers();self.press(p);p.b.execute('cursor_x=40');self.press(p,'B')
  self.assertEqual(p.a.eval('#ping_records()'),2);self.assertEqual(p.a.eval('calls.ping_cues'),1)
if __name__=='__main__':unittest.main()
