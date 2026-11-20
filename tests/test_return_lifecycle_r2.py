"""Production adapter checks; not native input or Save & Quit evidence."""
import unittest
import test_recovery_income_v18 as v18

class LifecycleTests(unittest.TestCase):
 peers=v18.ReturnTests.peers
 click=v18.ReturnTests.click
 # Reuse setup helpers, but do not duplicate the inherited test matrix.
 def test_second_death_snapshot_then_ordinary_recovery(self):
  p=self.peers();self.click(p);p.tick(20)
  p.b.execute("""
  local u=dofile_once('mods/quant.ew/files/system/coop_revival/return_ui.lua')
  assert(not entities[300].c.ControlsComponent.enabled)
  ctx.cap.coop_revival.died(300)
  -- Same ordering as local_health: died hook, then original body serialization.
  globals.ew_local_player_dead=encode(entities[300])
  assert(decode(globals.ew_local_player_dead).c.ControlsComponent.enabled)
  assert(not ComponentHasTag(entities[300].c.ControlsComponent,'ew_coop_return_controls_owned'))
  flags.ew_flag_notplayer_active=true;entities[200].alive=true;ctx.my_player.entity=200
  ctx.my_player.status={is_alive=false};ctx.cap.coop_revival.dead_ready();key_down=false
  """)
  p.flush();p.a.execute('settings.manual=true');p.tick(35)
  p.b.execute("assert(ctx.cap.coop_revival.chat_command('/revive'))")
  p.flush();p.tick(25)
  self.assertEqual(p.b.eval('calls.deserialize'),2)
  self.assertTrue(p.b.eval('entities[300].c.ControlsComponent.enabled'))
 def test_cold_module_owned_guard_and_unowned_disabled_controls(self):
  p=self.peers();self.click(p);p.tick(20)
  p.b.execute("""
  local cold=assert(loadstring(READ('mods/quant.ew/files/system/coop_revival/return_ui.lua')))()
  key_down=false;cold.input(true)
  assert(entities[300].c.ControlsComponent.enabled)
  assert(not ComponentHasTag(entities[300].c.ControlsComponent,'ew_coop_return_controls_owned'))
  entities[300].c.ControlsComponent.enabled=false
  cold.suppress_restored(300);cold.clear()
  assert(not entities[300].c.ControlsComponent.enabled)
  """)
 def test_owned_restore_at_spawn_snapshot_menu_focus_replacement_and_teardown(self):
  for action in ("u.before_snapshot(300)","menu=true;u.input(true)","u.input(false)","u.clear()","ctx.my_player.entity=100;u.input(true)","coop.on_local_player_spawn()"):
   p=self.peers();self.click(p);p.tick(20)
   p.b.execute("local u=dofile_once('mods/quant.ew/files/system/coop_revival/return_ui.lua');"+action+";assert(entities[300].c.ControlsComponent.enabled)")
 def test_cold_guard_keeps_confirmation_swallowed_until_release(self):
  p=self.peers();self.click(p);p.tick(20)
  p.b.execute("""
  local cold=assert(loadstring(READ('mods/quant.ew/files/system/coop_revival/return_ui.lua')))()
  cold.input(true);assert(not entities[300].c.ControlsComponent.enabled)
  key_down=false;cold.input(true);assert(entities[300].c.ControlsComponent.enabled)
  assert(not ctx.coop_return_input_block)
  """)
 def test_component_replacement_releases_only_tagged_original(self):
  p=self.peers();self.click(p);p.tick(20)
  p.b.execute("""
  local u=dofile_once('mods/quant.ew/files/system/coop_revival/return_ui.lua')
  local old=entities[300].c.ControlsComponent;local replacement={enabled=false}
  entities[300].c.ControlsComponent=replacement
  local components=EntityGetComponentIncludingDisabled
  EntityGetComponentIncludingDisabled=function(e,t)
   if e==300 and t=='ControlsComponent'then return {replacement,old}end
   return components(e,t)
  end
  u.clear();assert(old.enabled);assert(not replacement.enabled)
  """)
 def test_untagged_other_entity_remains_disabled(self):
  p=self.peers();p.b.execute("""
  local u=dofile_once('mods/quant.ew/files/system/coop_revival/return_ui.lua')
  entities[100].c.ControlsComponent.enabled=false
  u.restore_owned(100);u.before_snapshot(100);u.clear()
  assert(not entities[100].c.ControlsComponent.enabled)
  """)
 def test_withdrawal_reasons_available_and_pending(self):
  causes={
   'entities[100].x=2000':'Your teammate left the Holy Mountain.',
   'ctx.players.A.status.is_alive=false':'No teammate is available in the Holy Mountain.',
   'ctx.players.A.dc=true':'No teammate is available in the Holy Mountain.',
   'settings.offer_return=false':'Holy Mountain return was disabled.',
   "local s=decode(globals.ew_coop_run_v1);s.players.B.returns['0:2']=1;receive('A','snapshot',encode({s}))":'No Holy Mountain returns left for this stage.',
   'ng=1':'Mountain return is no longer available.'}
  for pending in (False,True):
   for cause,message in causes.items():
    with self.subTest(cause=cause,pending=pending):
     p=self.peers('blocked=true')
     if pending:self.click(p);p.tick(5)
     p.a.execute(cause);p.tick(45)
     p.b.execute("dofile_once('mods/quant.ew/files/system/coop_revival/return_ui.lua').render()")
     self.assertIn(message,p.b.eval('calls.ui'))
 def test_lost_selected_target_does_not_claim_all_other_occupants_left(self):
  p=self.peers('blocked=true');self.click(p);p.tick(5)
  p.a.execute("""
  ctx.players.C={peer_id='C',entity=100,name='Other',status={is_alive=true}}
  ctx.players.A.dc=true
  """)
  p.tick(45);p.b.execute("dofile_once('mods/quant.ew/files/system/coop_revival/return_ui.lua').render()")
  self.assertNotIn('No teammate is available',p.b.eval('calls.ui'))
 def test_timeout_notice_and_duplicate_cancel_do_not_replace_new_offer(self):
  p=self.peers('blocked=true');self.click(p);p.tick(10)
  saved=p.a.eval('encode(decode(globals.ew_coop_run_v1).players.B.pending)')
  p.tick(310)
  p.b.execute("dofile_once('mods/quant.ew/files/system/coop_revival/return_ui.lua').render()")
  self.assertIn('No safe place yet. You can confirm again.',p.b.eval('calls.ui'))
  p.b.execute("old=decode('"+saved+"')")
  p.b.execute("receive('A','cancel_return',encode({'B',old.return_run,old.id,old.death,'WRONG'}))")
  p.flush();p.tick(1)
  self.assertNotIn('WRONG',p.b.eval('calls.ui'))

if __name__=='__main__':unittest.main()
