"""Production adapters with controlled peers. Native observations are separate."""
import unittest
from test_coop_revival import Peers
from test_recovery_pass import SWAP
from test_shared_income import IncomePeers

MAP='''
function BiomeMapGetSize()return 64,48 end
function BiomeMapGetName(x,y)
 local wx=(x+16384)%32768-16384;local row=math.floor(y/512)
 if wx>=-1024 and wx<0 and(row==2 or row==5 or row==8)then return '$biome_holymountain'end
 return '$biome_coalmine'
end
key_down,key_just,pad_down,pad_just=false,false,false,false
function InputIsKeyDown(k)return key_down end
function InputIsKeyJustDown(k)return key_just and k==(tonumber(settings['quant.ew.rebind_mountain_return'])or 44)end
function InputIsJoystickButtonDown()return pad_down end
function InputIsJoystickButtonJustDown()return pad_just end
function GameGetIsGamepadConnected()return use_pad==true end
function InputGetJoystickAnalogStick()return 0,0 end
'''
OPTIONAL=MAP+'''
settings.statue=false;settings.offer_return=true;settings.return_limit='1'
entities[100].x=-677;entities[100].y=1420;entities[100].c.Inventory2Component.mActiveItem=0
entities[200].parent=nil
if GameHasFlagRun('ew_flag_notplayer_active')then
 globals.ew_coop_death_location=encode({death=1,x=0,y=700,ng=0,mountain=false})
end
'''

class ArrivalTests(unittest.TestCase):
 def test_stale_client_empty_map_merges_host_death_with_return_off(self):
  p=Peers(SWAP+';settings.statue=false;settings.offer_return=false')
  p.a.execute('ctx.cap.coop_revival.dead_ready()');p.flush()
  self.assertEqual(p.state('.players.A.death'),1)
  saved=p.a.eval('globals.ew_coop_run_v1')
  p.b.execute("local s=decode('"+saved+"');s.players.A.death=0;receive('A','snapshot',encode({s}));ctx.players.A.status={is_alive=true};entities[200].parent=nil;entities[100].c.Inventory2Component.mActiveItem=0;entities[100].y=700;cross.ew_coop_portal_used(100,100,700)")
  p.a.execute('entities[100].y=700;entities[200].parent=nil;entities[100].c.Inventory2Component.mActiveItem=0')
  p.flush();p.tick(180)
  self.assertEqual(p.a.eval('calls.deserialize'),1);self.assertEqual(p.state('.stage'),1)
  self.assertEqual(p.state('.players.A.run'),0)

 def test_early_notice_does_not_include_later_death_during_streaming(self):
  p=Peers('settings.statue=false;settings.offer_return=false')
  p.a.execute("local s=decode(globals.ew_coop_run_v1);s.players.B.death=0;s.players.B.resolved=1;receive('A','snapshot',encode({s}));ctx.players.B.status={is_alive=true};cross.ew_coop_portal_used(100,100,700);entities[100].y=700;loaded=false")
  p.flush();p.a.execute("receive('B','presence',encode({2}));ctx.players.B.status={is_alive=false,coop_death=2}")
  p.b.execute("globals.ew_coop_death='2'");p.tick(180)
  p.a.execute('loaded=true');p.tick(180)
  self.assertIsNone(p.b.eval('calls.deserialize'))

class ReturnTests(unittest.TestCase):
 def peers(self,extra='',swap=''):
  p=Peers(swap+'\n'+OPTIONAL+'\n'+extra);p.tick(3);return p
 def click(self,p,owner='B',release=True):
  lua=p.peers[owner]
  if release:lua.execute('key_down=false;key_just=false');p.tick(1)
  lua.execute('key_down=true;key_just=true');p.tick(1);lua.execute('key_just=false')
 def test_both_roles_confirmation_hp_wallet_and_single_usage(self):
  for swap,owner in(('', 'B'),(SWAP,'A')):
   p=self.peers(swap=swap);p.tick(120)
   self.assertIsNone(p.peers[owner].eval('calls.deserialize'),'ignoring does nothing')
   self.click(p,owner);p.tick(20)
   self.assertEqual(p.peers[owner].eval('calls.deserialize'),1)
   self.assertAlmostEqual(p.peers[owner].eval('ctx.my_player.status.hp'),2.4)
   self.assertEqual(p.peers[owner].eval('entities[300].c.WalletComponent.money'),12345)
   self.assertEqual(p.state(f".players.{owner}.returns['0:2']"),1)
   self.assertEqual(p.state(f'.players.{owner}.run'),0)
 def test_held_before_offer_needs_release_then_new_press(self):
  p=self.peers('key_down=true;key_just=true');p.tick(100)
  self.assertIsNone(p.b.eval('calls.deserialize'))
  p.b.execute('key_just=false');self.click(p);p.tick(20)
  self.assertEqual(p.b.eval('calls.deserialize'),1)
  self.assertFalse(p.b.eval('entities[300].c.ControlsComponent.enabled'))
  p.b.execute('key_down=false');p.tick(1)
  self.assertTrue(p.b.eval('entities[300].c.ControlsComponent.enabled'))
 def test_focus_chat_menu_and_post_update_chat_guard(self):
  for hidden in('focused=false','ctx.is_texting=true','menu=true','ctx.is_paused=true'):
   p=self.peers();p.b.execute(hidden);self.click(p);p.tick(10)
   self.assertIsNone(p.b.eval('calls.deserialize'))
  p=self.peers();p.b.execute('key_just=true;key_down=true;coop.on_world_update();ctx.is_texting=true;coop.on_world_update_post()');p.flush();p.tick(10)
  self.assertIsNone(p.b.eval('calls.deserialize'))
 def test_controller_binding_and_released_fresh_press(self):
  p=self.peers('use_pad=true');p.b.execute('pad_down=true;pad_just=true');p.tick(1)
  p.b.execute('pad_just=false');p.tick(20)
  self.assertEqual(p.b.eval('calls.deserialize'),1)
 def test_timeout_tombstone_late_grant_and_fresh_confirmation(self):
  p=self.peers('blocked=true');self.click(p);p.tick(50)
  saved=p.a.eval("encode(decode(globals.ew_coop_run_v1).players.B.pending)")
  p.b.execute("saved_grant=decode('"+saved+"')")
  p.tick(270)
  self.assertIsNone(p.state('.players.B.pending'));self.assertIsNone(p.state(".players.B.returns['0:2']"))
  p.b.execute("receive('A','restore',encode({'B',saved_grant}));blocked=false")
  p.flush();p.tick(15);self.assertIsNone(p.b.eval('calls.deserialize'))
  self.click(p);p.tick(20);self.assertEqual(p.b.eval('calls.deserialize'),1)
  self.assertEqual(p.state(".players.B.returns['0:2']"),1)
 def test_departure_death_disconnect_disable_and_ng_cancel_pending(self):
  for change in('entities[100].x=2000','ctx.players.A.status.is_alive=false','ctx.players.A.dc=true','settings.offer_return=false','ng=1'):
   p=self.peers('blocked=true');self.click(p);p.tick(20)
   self.assertIsNotNone(p.state('.players.B.pending'))
   p.a.execute(change);p.tick(45)
   self.assertIsNone(p.state('.players.B.pending'));self.assertIsNone(p.state(".players.B.returns['0:2']"))
   self.assertIsNone(p.b.eval('calls.deserialize'))
 def test_room_world_depth_and_recorded_death_not_statue(self):
  for location in("{death=1,x=0,y=700,ng=0,mountain=false}","{death=1,x=0,y=2000,ng=0,mountain=false}","{death=1,x=32768,y=700,ng=0,mountain=false}","{death=1,x=0,y=700,ng=1,mountain=false}"):
   p=self.peers("if PEER=='B'then globals.ew_coop_death_location=encode("+location+")end")
   self.click(p);p.tick(20)
   self.assertEqual(p.b.eval('calls.deserialize')or 0,1 if 'y=700,ng=0' in location and'x=0,'in location else 0)
 def test_usage_survives_checkpoint_parallel_limit_change_and_unlimited(self):
  p=self.peers();p.a.execute("local s=decode(globals.ew_coop_run_v1);s.players.B.returns['0:2']=1;receive('A','snapshot',encode({s}))")
  p.tick(35);self.click(p);p.tick(20);self.assertIsNone(p.b.eval('calls.deserialize'))
  p.a.execute("settings.return_limit='unlimited'");p.tick(35);self.click(p);p.tick(20)
  self.assertEqual(p.state(".players.B.returns['0:2']"),2)
  p.a.execute("local s=decode(globals.ew_coop_run_v1);local r=dofile_once('mods/quant.ew/files/system/coop_revival/rules.lua');r.checkpoint(s,'0:5');assert(s.players.B.returns['0:2']==2);assert(r.return_remaining(s,'B','1:2')==math.huge)")
 def test_boss_does_not_disable_but_arena_is_not_a_mountain(self):
  p=self.peers();p.a.execute("local s=decode(globals.ew_coop_run_v1);s.boss=true;receive('A','snapshot',encode({s}))")
  p.tick(35);self.click(p);p.tick(20);self.assertEqual(p.b.eval('calls.deserialize'),1)
  q=self.peers('entities[100].y=5000');self.click(q);q.tick(20);self.assertIsNone(q.b.eval('calls.deserialize'))
 def test_continue_migration_keeps_usage_and_cancels_saved_pending(self):
  p=self.peers('blocked=true');self.click(p);p.tick(20)
  saved=p.a.eval('globals.ew_coop_run_v1')
  q=self.peers("globals.ew_coop_run_v1='"+saved+"';blocked=true")
  q.tick(35);self.assertIsNone(q.state('.players.B.pending'))
  self.assertEqual(q.state('.serial'),p.state('.serial'))
 def test_success_waiting_for_ack_wins_cancellation_and_charges_once(self):
  p=self.peers();p.b.execute('key_down=false;key_just=false');p.tick(1)
  # Run input/restore, but hold the owner's acknowledgements until the host
  # invalidates the destination. The saved success receipt must still win.
  p.b.execute('key_down=true;key_just=true;tick()');p.flush()
  p.b.execute('key_just=false;tick()')
  held=[]
  while p.q:
   item=p.q.popleft()
   if item[0]=='B'and item[1]in('receipt','presence'):held.append(item)
   else:
    for name,lua in p.peers.items():
     if name!=item[0]:lua.globals().receive(*item)
  self.assertEqual(p.b.eval('calls.deserialize'),1)
  p.a.execute('entities[100].x=2000');p.tick(2)
  self.assertEqual(p.state(".players.B.returns['0:2']"),1)
  for item in held:p.q.append(item)
  p.flush();p.tick(3)
  self.assertEqual(p.state(".players.B.returns['0:2']"),1)
 def test_paused_time_does_not_consume_the_five_second_placement_window(self):
  p=self.peers('blocked=true');self.click(p);p.tick(10)
  p.b.execute('ctx.is_paused=true');p.tick(400)
  self.assertIsNotNone(p.state('.players.B.pending'))
  p.b.execute('ctx.is_paused=false');p.tick(310)
  self.assertIsNone(p.state('.players.B.pending'));self.assertIsNone(p.state(".players.B.returns['0:2']"))
 def test_statue_and_return_race_resolves_once_without_both_charges(self):
  for first in('statue','return'):
   p=self.peers('settings.statue=true;blocked=true')
   for lua in p.peers.values():lua.execute('entities[200].parent=101;entities[100].c.Inventory2Component.mActiveItem=200')
   if first=='return':self.click(p);p.tick(1)
   p.a.execute("receive('A','statue_complete',encode({'B',1,300}))");p.flush()
   if first=='statue':self.click(p)
   p.b.execute('blocked=false');p.tick(20)
   self.assertEqual(p.b.eval('calls.deserialize'),1)
   self.assertEqual(p.state('.players.B.run'),1 if first=='statue'else 0)
   self.assertEqual(p.state(".players.B.returns['0:2']")or 0,1 if first=='return'else 0)
 def test_same_stage_parallel_world_does_not_refill_and_ng_has_new_namespace(self):
  p=self.peers("settings.return_limit='1'")
  self.click(p);p.tick(20)
  saved=p.a.eval('globals.ew_coop_run_v1')
  q=self.peers("globals.ew_coop_run_v1='"+saved+"'")
  self.assertEqual(q.state(".players.B.returns['0:2']"),1)
  q.a.execute('''
   local s=decode(globals.ew_coop_run_v1);local r=dofile_once('mods/quant.ew/files/system/coop_revival/rules.lua')
   assert(r.return_remaining(s,'B','0:2')==0)
   local m=dofile_once('mods/quant.ew/files/system/coop_revival/mountains.lua');local a=m.room(-677,1420);local b=m.room(32768-677,1420)
   assert(a.key~=b.key and a.stage==b.stage)
   assert(r.return_remaining(s,'B','1:2')==1)
  ''')
 def test_old_run_request_and_cancel_cannot_match_new_offer_or_transaction(self):
  p=self.peers('blocked=true')
  run=p.state('.return_run')
  p.a.execute("receive('B','mountain_return',encode({1,'A:OLD:1','A:OLD'}))")
  self.assertIsNone(p.state('.players.B.pending'))
  self.click(p);p.tick(5)
  ident=p.state('.players.B.pending.id')
  p.b.execute("receive('A','cancel_return',encode({'B','A:OLD',"+str(ident)+",1,'old'}))")
  p.flush();self.assertIsNotNone(p.state('.players.B.pending'))
  self.assertEqual(p.state('.return_run'),run)
 def test_offer_percent_change_requires_a_fresh_offer_identity(self):
  p=self.peers();serial=p.state('.return_serial')
  p.a.execute('settings.mountain_percent=55');p.tick(35)
  self.assertGreater(p.state('.return_serial'),serial)
  self.click(p);p.tick(20);self.assertAlmostEqual(p.b.eval('ctx.my_player.status.hp'),4.4)
 def test_utf8_offer_bounds_and_pending_wording(self):
  p=self.peers();p.b.execute('''
  local u=dofile_once('mods/quant.ew/files/system/coop_revival/return_ui.lua')
  calls.ui='';local b=u.draw(1,{name=string.rep('Éléonore',18),percent=30},{w=427,h=240,now=0,alpha=1,binding='[Space]',spectator_top=196})
  assert(b.y+b.h<196 and b.x>=0 and b.x+b.w<=427)
  assert(calls.ui:find('30%% health')and not calls.ui:find('heart',1,true))
  assert(calls.images[#calls.images].path=='data/ui_gfx/empty_white.png')
  calls.ui='';u.draw(1,{pending=true,pressed=0,name='Alex',percent=30},{w=427,h=240,now=46,alpha=1})
  assert(calls.ui:find('Waiting for a safe place',1,true))
  ''')

class GoldTests(unittest.TestCase):
 def peers(self,extra=''):
  return IncomePeers("settings.statue=false;if PEER=='B'then flags.ew_flag_notplayer_active=nil;entities[200].tags.ew_notplayer=nil end;"+extra)
 def test_tag_without_identity_falls_back_to_pickable_personal_gold(self):
  p=self.peers();p.a.execute("e=EntityCreateNew('legacy gold');EntityAddTag(e,'gold_nugget');EntityAddTag(e,'ew_income_gold');EntityAddComponent2(e,'ItemComponent',{is_pickable=false});assert(cross.ew_income_pickup(e,ctx.my_player.entity)==false)")
  p.tick(180);self.assertTrue(p.a.eval('entities[e].c.ItemComponent.is_pickable'))
  self.assertEqual((p.money('A'),p.money('B')),(100,100));self.assertEqual(p.ledger('.accounts.A.next'),0)
 def test_nested_native_generators_cannot_save_the_temporary_false_gate(self):
  p=self.peers();p.a.execute('''
  e=EntityCreateNew('canonical gold');EntityAddTag(e,'gold_nugget');EntityAddComponent2(e,'ItemComponent',{is_pickable=true})
  cross.ew_income_generated(e,true);cross.ew_income_generated(e,true)
  income.on_world_update();assert(entities[e].c.ItemComponent.is_pickable)
  ''')
 def test_legacy_gated_replica_keeps_delay_but_becomes_collectable(self):
  p=self.peers();p.a.execute("g=nugget('719',10);entities[g].c.ItemComponent.is_pickable=false;entities[g].c.ItemComponent.next_frame_pickable=300;income.on_world_update();assert(entities[g].c.ItemComponent.is_pickable and entities[g].c.ItemComponent.next_frame_pickable==300)")
 def test_generator_append_is_once_per_native_lua_environment(self):
  p=self.peers();p.a.execute('''
  local source=READ('mods/quant.ew/files/system/shared_income/generated.lua');local generated_count=0
  cross.ew_income_source=function()return 'share'end
  cross.ew_income_generated=function()generated_count=generated_count+1 end
  EntityLoad=function()return 999 end
  local append=source..'\\ntrailing_append=(trailing_append or 0)+1'
  assert(loadstring(append))();assert(loadstring(append))();assert(trailing_append==2)
  EntityLoad('data/entities/items/pickup/goldnugget.xml',0,0)
  assert(generated_count==1)
  ''')
 def test_delayed_id_and_lost_generation_table_reconstruct_from_entity(self):
  p=self.peers();p.a.execute('''
  e=EntityCreateNew('canonical gold');EntityAddTag(e,'gold_nugget');EntityAddComponent2(e,'ItemComponent',{is_pickable=true});EntityAddComponent2(e,'VariableStorageComponent',{name='gold_value',value_int=10})
  local track=ewext.track;ewext.track=function()end
  cross.ew_income_generated(e,true);income.on_world_update();assert(not entities[e].c.ItemComponent.is_pickable)
  ewext.track=track
  -- Cold adapter instance loses its Lua-local generated table but keeps entity components.
  income=assert(loadstring(READ('mods/quant.ew/files/system/shared_income/shared_income.lua')))();income.on_local_player_spawn()
  income.on_world_update();assert(entities[e].c.ItemComponent.is_pickable)
  assert(cross.ew_income_pickup(e,ctx.my_player.entity));
  ''');p.flush();p.tick(2);self.assertEqual((p.money('A'),p.money('B')),(110,110))
 def test_local_cosmetic_is_immediate_credit_is_authoritative_and_prompt(self):
  p=self.peers('''calls.gold_fx={};local load=EntityLoad;EntityLoad=function(path,x,y)
   if path:find('data/entities/particles/gold_pickup',1,true)then calls.gold_fx[#calls.gold_fx+1]={frame,x,y};return 0 end
   return load(path,x,y)
  end''')
  for lua in p.peers.values():lua.execute("g=nugget('712',10);entities[g].x=170;entities[g].y=90")
  p.a.execute("frame=1;cross.ew_income_pickup(g,ctx.my_player.entity);assert(#calls.gold_fx==1 and calls.gold_fx[1][1]==1 and calls.gold_fx[1][2]==170);cross.ew_income_pickup(g,ctx.my_player.entity);assert(#calls.gold_fx==1)")
  self.assertEqual(p.money('A'),100);p.flush();self.assertEqual(p.money('B'),100)
  p.tick(2);self.assertEqual((p.money('A'),p.money('B')),(110,110))
  p.a.execute("late=nugget('712',10);cross.ew_income_pickup(late,ctx.my_player.entity);assert(#calls.gold_fx==1)")
 def test_retirement_before_delivery_keeps_cosmetic_body_and_hp_receipt(self):
  p=self.peers();p.a.execute("g=nugget('713',10);EntityAddComponent2(g,'VariableStorageComponent',{name='hp_value',value_float=.16});cross.ew_income_pickup(g,ctx.my_player.entity)")
  p.flush();p.a.execute("local o=decode(globals.ew_income_owner_v1);assert(o.effects['713'].cosmetic and o.effects['713'].item.hp==.16 and not o.claims['713'])")
  p.tick(2);self.assertEqual((p.money('A'),p.money('B')),(110,110))

if __name__=='__main__':unittest.main()
