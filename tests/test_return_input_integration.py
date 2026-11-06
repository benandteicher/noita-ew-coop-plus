"""Real text handler and serializer integration under the engine adapter."""
import unittest
import test_recovery_income_v18 as v18

class InputIntegrationTests(unittest.TestCase):
 peers=v18.ReturnTests.peers
 click=v18.ReturnTests.click
 def test_chat_takes_control_restriction_without_return_guard_releasing_it(self):
  p=self.peers();self.click(p);p.tick(20)
  p.b.execute("""
  local u=dofile_once('mods/quant.ew/files/system/coop_revival/return_ui.lua')
  local text=assert(loadstring(READ('mods/quant.ew/files/system/text/text.lua')))()
  local function handler(name)
   for i=1,100 do local n,fn=debug.getupvalue(text.on_world_update,i);if not n then break end;if n==name then return fn end end
   error('Missing real text handler '..name)
  end
  handler('starttext')()
  u.input(true)
  assert(ctx.is_texting and not entities[300].c.ControlsComponent.enabled)
  assert(not ComponentHasTag(entities[300].c.ControlsComponent,'ew_coop_return_controls_owned'))
  u.input(true);assert(not entities[300].c.ControlsComponent.enabled)
  handler('stoptext')();assert(entities[300].c.ControlsComponent.enabled)
  -- Same held confirmation resumes suppression after chat closes; release ends it.
  u.input(true);assert(ctx.coop_return_input_block and not entities[300].c.ControlsComponent.enabled)
  key_down=false;u.input(true)
  assert(entities[300].c.ControlsComponent.enabled and not ctx.coop_return_input_block)
  """)
 def test_real_util_serializer_observes_clean_owned_state_only(self):
  p=self.peers();self.click(p);p.tick(20)
  p.b.execute("""
  np={CrossCallAdd=function()end,SerializeEntity=function(e)
   calls.serialized_enabled=entities[e].c.ControlsComponent.enabled
   calls.serialized_tag=ComponentHasTag(entities[e].c.ControlsComponent,'ew_coop_return_controls_owned')
   return 'serialized'
  end}
  local real=assert(loadstring(READ('mods/quant.ew/files/core/util.lua')))()
  assert(real.serialize_entity(300)=='serialized')
  assert(calls.serialized_enabled and not calls.serialized_tag)
  entities[100].c.ControlsComponent.enabled=false
  real.serialize_entity(100)
  assert(not calls.serialized_enabled and not calls.serialized_tag)
  """)
if __name__=='__main__':unittest.main()
